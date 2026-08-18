"""Empirical source credibility tracker.

Measures how trustworthy a source domain is based on how often its claims
hold up under verification. No hardcoded tiers, no manual reputation
assignments — the score is earned.

## How it works

Each domain (arxiv.org, github.com, britannica.com, ...) has a credibility
score in [0.0, 1.0]. The score is a Beta distribution posterior (alpha /
beta counts), reported as the expected value alpha / (alpha + beta).

- New domain: starts at 0.5 (neutral — 1 alpha, 1 beta, uninformative prior)
- Claim from domain verified as "supported": alpha += 1
- Claim from domain verified as "unsupported" or "contradicted": beta += 1
- Claim from domain verified as "unsourced" or "error": no update (the
  verification itself failed, not the source)

The score is used as:
  - A weight multiplier in extractive synthesis (sentence_score * credibility)
  - Context in the LLM synthesis prompt ("Source credibility: 0.82 (high)")
  - A trust signal in the research note's frontmatter

## Persistence

Scores are persisted to ``source_credibility.json`` in the backend directory.
The file is loaded on startup and saved after each update. The tracker is
thread-safe (a lock guards the in-memory dict + file writes).

## Domain extraction

Credibility is tracked at the **domain** level, not per-URL. This gives
enough signal to be useful (a domain's overall track record predicts the
quality of its pages) while keeping the state small and stable. A single
bad page on arxiv.org doesn't tank arxiv's score; a consistent pattern of
bad pages does.
"""

from __future__ import annotations

import json
import os
import threading
from urllib.parse import urlparse

# Import the low-credibility domain set so the credibility tracker can
# apply a lower default score to code-hosting platforms (GitHub/GitLab/etc).
# These are project planning documents, not authoritative sources.
try:
    from source_classification import _LOW_CREDIBILITY_DOMAINS
except ImportError:
    _LOW_CREDIBILITY_DOMAINS = set()


def _extract_domain(url: str) -> str:
    """Extract the registrable domain from a URL.

    "https://www.nature.com/articles/123" -> "nature.com"
    "https://github.com/owner/repo/issues/3" -> "github.com"
    "https://en.wikipedia.org/wiki/Cement" -> "wikipedia.org"
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not host:
            return ""
        host = host.lower()
        # Strip common prefixes.
        for prefix in ("www.", "en.", "m.", "lite."):
            if host.startswith(prefix):
                host = host[len(prefix):]
        # Keep the last two labels (e.g., nature.com, github.com).
        # For co.uk / co.jp etc., keep three.
        parts = host.split(".")
        if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov", "ac"):
            return ".".join(parts[-3:])
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    except Exception:  # noqa: BLE001 — best-effort
        return ""


class SourceCredibilityTracker:
    """Track and persist per-domain credibility scores.

    Thread-safe. The tracker is a singleton-like object — one instance per
    backend process, shared across all research turns and verification runs.
    """

    def __init__(self, path: str | None = None):
        self._path = path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "source_credibility.json",
        )
        self._lock = threading.Lock()
        # In-memory state: {domain: {"alpha": float, "beta": float}}
        self._scores: dict[str, dict[str, float]] = {}
        self._load()

    # -- Persistence --------------------------------------------------------

    def _load(self):
        """Load scores from disk. Creates an empty file if none exists."""
        try:
            if os.path.exists(self._path):
                with open(self._path, encoding="utf-8") as f:
                    self._scores = json.load(f)
        except Exception:  # noqa: BLE001 — best-effort
            self._scores = {}

    def _save(self):
        """Save scores to disk. Never raises."""
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._scores, f, indent=2, ensure_ascii=False)
        except Exception:  # noqa: BLE001 — best-effort
            pass

    # -- Scoring ------------------------------------------------------------

    def get(self, url: str) -> float:
        """Return the credibility score [0.0, 1.0] for a source URL.

        Uses the domain-level score. Unknown domains return 0.5 (neutral),
        EXCEPT for known low-credibility domains (code-hosting platforms
        like GitHub/GitLab/Bitbucket) which return 0.3 — these are project
        planning documents, not authoritative sources, and their default
        trust level should be below neutral.
        The score is the expected value of the Beta(alpha, beta) posterior:
        alpha / (alpha + beta).
        """
        domain = _extract_domain(url)
        if not domain:
            return 0.5
        with self._lock:
            entry = self._scores.get(domain)
            if not entry:
                # No empirical data for this domain. Use a prior based on
                # domain type: code-hosting platforms start below neutral
                # because they're project planning docs, not documentation.
                if domain in _LOW_CREDIBILITY_DOMAINS:
                    return 0.3
                return 0.5  # neutral prior
            alpha = entry.get("alpha", 1.0)
            beta = entry.get("beta", 1.0)
            total = alpha + beta
            if total == 0:
                return 0.5
            return alpha / total

    def get_label(self, url: str) -> str:
        """Return a human-readable credibility label for a source URL."""
        score = self.get(url)
        if score >= 0.75:
            return f"high ({score:.2f})"
        if score >= 0.55:
            return f"moderate ({score:.2f})"
        if score >= 0.35:
            return f"low ({score:.2f})"
        return f"very low ({score:.2f})"

    def get_weight(self, url: str) -> float:
        """Return the extractive-synthesis weight multiplier for a URL.

        Maps the [0.0, 1.0] credibility score to a [0.1, 1.0] weight:
          1.0 credibility -> 1.0 weight (full trust)
          0.5 credibility -> 0.5 weight (neutral)
          0.0 credibility -> 0.1 weight (almost zero, not fully zero)
        """
        return 0.1 + 0.9 * self.get(url)

    def get_domain_stats(self) -> dict[str, dict]:
        """Return all tracked domain stats. For logging/debugging."""
        with self._lock:
            out = {}
            for domain, entry in self._scores.items():
                alpha = entry.get("alpha", 1.0)
                beta = entry.get("beta", 1.0)
                total = alpha + beta
                out[domain] = {
                    "score": alpha / total if total > 0 else 0.5,
                    "alpha": alpha,
                    "beta": beta,
                    "verifications": int(total - 2),  # subtract prior
                }
            return out

    # -- Updates ------------------------------------------------------------

    def record_verification(self, url: str, verdict: str) -> float:
        """Update the credibility score for a source's domain.

        Called after a claim from this URL is verified against its source.
        The verdict determines the update direction:

          "supported"     -> alpha += 1 (claim held up)
          "unsupported"   -> beta += 1  (claim not backed by source)
          "contradicted"  -> beta += 2  (claim contradicts source — worse)
          anything else   -> no update (verification error, not source error)

        Returns the new credibility score for the domain.
        """
        domain = _extract_domain(url)
        if not domain:
            return 0.5
        with self._lock:
            entry = self._scores.setdefault(domain, {"alpha": 1.0, "beta": 1.0})
            v = verdict.lower().strip()
            if v == "supported":
                entry["alpha"] = entry.get("alpha", 1.0) + 1.0
            elif v == "unsupported":
                entry["beta"] = entry.get("beta", 1.0) + 1.0
            elif v == "contradicted":
                entry["beta"] = entry.get("beta", 1.0) + 2.0
            else:
                return self._score_unlocked(domain)
            self._save()
            return self._score_unlocked(domain)

    def record_verifications(
        self, verifications: list[dict]
    ) -> dict[str, float]:
        """Batch-update credibility from a list of verification results.

        Each dict should have "url" and "verdict" keys. Returns a mapping
        of domain -> new_score for all domains that were updated.
        """
        updated: dict[str, float] = {}
        for v in verifications:
            url = v.get("url", "")
            verdict = v.get("verdict", "")
            if not url or not verdict:
                continue
            new_score = self.record_verification(url, verdict)
            domain = _extract_domain(url)
            if domain:
                updated[domain] = new_score
        return updated

    def _score_unlocked(self, domain: str) -> float:
        """Get score without acquiring the lock (caller must hold it)."""
        entry = self._scores.get(domain)
        if not entry:
            return 0.5
        alpha = entry.get("alpha", 1.0)
        beta = entry.get("beta", 1.0)
        total = alpha + beta
        return alpha / total if total > 0 else 0.5