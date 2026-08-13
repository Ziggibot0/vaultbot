"""
rate_limit.py — Lightweight rate limiter for VaultBot backend endpoints.

WHY THIS EXISTS
---------------
The VaultBot backend has no rate limiting. A buggy agentic loop or a
malicious local process could hammer endpoints thousands of times per
second. This module provides a simple in-memory token-bucket rate limiter
that doesn't add external dependencies.

DESIGN
------
- Token bucket algorithm: each client (identified by a key) gets a bucket
  that refills at a configurable rate. Requests consume tokens; when the
  bucket is empty, requests are rejected with 429 Too Many Requests.
- In-memory only (no Redis, no disk). Restarting the backend resets all
  buckets — acceptable for a localhost-only service.
- Per-endpoint limits: different endpoints have different limits.
  - /custom_tools/call: 10 req/min (tool execution is expensive)
  - /shutdown: 5 req/min
  - WebSocket /ws: 10 connections/min
  - Default: 60 req/min for everything else
- The rate limiter is a FastAPI middleware that runs before the auth
  middleware (so rate limiting applies even to unauthenticated requests).

Pure stdlib. No new dependencies.
"""

from __future__ import annotations

import time
import threading


class TokenBucket:
    """A single token bucket with refill rate and burst capacity."""

    def __init__(self, rate: float, capacity: int):
        self.rate = rate  # tokens per second
        self.capacity = capacity  # max tokens (burst)
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()

    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if allowed, False if rate limited."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


class RateLimiter:
    """In-memory token-bucket rate limiter.

    Thread-safe. Buckets are created lazily and auto-expire after a period
    of inactivity to prevent memory leaks.
    """

    def __init__(self, default_rate: float = 1.0, default_capacity: int = 60):
        self.default_rate = default_rate
        self.default_capacity = default_capacity
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()
        # Per-path overrides: (rate, capacity)
        self._path_limits: dict[str, tuple[float, int]] = {
            "/custom_tools/call": (0.167, 10),  # 10/min
            "/shutdown": (0.083, 5),  # 5/min
            "/ws": (0.167, 10),  # 10/min
        }
        # Last cleanup time.
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 300  # 5 minutes

    def _get_bucket_key(self, path: str, client: str) -> str:
        """Generate a bucket key from path and client identifier."""
        return f"{client}:{path}"

    def _cleanup_expired(self):
        """Remove buckets that haven't been used in 10 minutes."""
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        expired = []
        for key, bucket in self._buckets.items():
            if now - bucket.last_refill > 600:  # 10 min inactivity
                expired.append(key)
        for key in expired:
            del self._buckets[key]

    def is_allowed(self, path: str, client: str = "127.0.0.1") -> bool:
        """Check if a request is allowed. Returns True if not rate limited."""
        with self._lock:
            self._cleanup_expired()

            # Determine rate/capacity for this path.
            rate, capacity = self._path_limits.get(
                path, (self.default_rate, self.default_capacity)
            )

            key = self._get_bucket_key(path, client)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(rate, capacity)
                self._buckets[key] = bucket

            return bucket.consume()


# Singleton instance for the middleware.
_limiter = RateLimiter()


def is_rate_allowed(path: str, client: str = "127.0.0.1") -> bool:
    """Check if a request to `path` from `client` is within rate limits."""
    return _limiter.is_allowed(path, client)
