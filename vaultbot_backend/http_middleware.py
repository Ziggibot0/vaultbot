"""HTTP rate-limit and shared-secret authentication middleware."""

from __future__ import annotations

import secrets

import auth
import rate_limit
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        client = request.client.host if request.client else "127.0.0.1"
        if not rate_limit.is_rate_allowed(request.url.path, client):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please wait before "
                    "sending more requests."
                },
            )
        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path.rstrip("/") or "/"
        if auth.is_auth_exempt(path):
            return await call_next(request)
        if not auth.is_auth_required_for_method(path, request.method):
            return await call_next(request)

        token = request.headers.get("X-VaultBot-Token")
        if token is None and path == "/shutdown":
            token = request.query_params.get("token")
        if auth.is_auth_disabled():
            return await call_next(request)
        if token is None:
            return JSONResponse(
                status_code=401, content={"detail": "missing auth token"}
            )
        try:
            expected = auth.get_or_create_token()
        except Exception:  # noqa: BLE001 — auth must fail closed if token access fails
            return JSONResponse(status_code=503, content={"detail": "auth unavailable"})
        if not secrets.compare_digest(token, expected):
            return JSONResponse(
                status_code=401, content={"detail": "invalid auth token"}
            )
        return await call_next(request)
