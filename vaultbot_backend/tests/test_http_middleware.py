"""Direct behavior tests for VaultBot's HTTP policy middleware."""

from __future__ import annotations

import http_middleware
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def _app_with(middleware_class) -> FastAPI:
    app = FastAPI()
    app.add_middleware(middleware_class)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def endpoint(path: str) -> dict[str, bool]:
        return {"ok": True}

    return app


def test_rate_limit_middleware_rejects_disallowed_request(monkeypatch) -> None:
    monkeypatch.setattr(
        http_middleware.rate_limit, "is_rate_allowed", lambda path, client: False
    )

    response = TestClient(_app_with(http_middleware.RateLimitMiddleware)).get("/health")

    assert response.status_code == 429
    assert response.json() == {
        "detail": "Rate limit exceeded. Please wait before sending more requests."
    }


@pytest.mark.parametrize(
    ("headers", "expected_detail"),
    [
        ({}, "missing auth token"),
        ({"X-VaultBot-Token": "wrong"}, "invalid auth token"),
    ],
)
def test_auth_middleware_rejects_missing_or_invalid_token(
    monkeypatch, headers: dict[str, str], expected_detail: str
) -> None:
    _require_auth(monkeypatch)

    response = TestClient(_app_with(http_middleware.AuthMiddleware)).post(
        "/sensitive", headers=headers
    )

    assert response.status_code == 401
    assert response.json() == {"detail": expected_detail}


def test_auth_middleware_accepts_valid_header_token(monkeypatch) -> None:
    _require_auth(monkeypatch)

    response = TestClient(_app_with(http_middleware.AuthMiddleware)).post(
        "/sensitive", headers={"X-VaultBot-Token": "expected"}
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_auth_middleware_accepts_shutdown_query_token(monkeypatch) -> None:
    _require_auth(monkeypatch)

    response = TestClient(_app_with(http_middleware.AuthMiddleware)).post(
        "/shutdown?token=expected"
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def _require_auth(monkeypatch) -> None:
    monkeypatch.setattr(http_middleware.auth, "is_auth_exempt", lambda path: False)
    monkeypatch.setattr(
        http_middleware.auth,
        "is_auth_required_for_method",
        lambda path, method: True,
    )
    monkeypatch.setattr(http_middleware.auth, "is_auth_disabled", lambda: False)
    monkeypatch.setattr(http_middleware.auth, "get_or_create_token", lambda: "expected")
