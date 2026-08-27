"""Focused tests for auth bypass environment variables.

Verifies that VAULTBOT_SKIP_AUTH controls auth bypass and VAULTBOT_SKIP_LOCK no
longer bypasses authentication (it should only control the PID lock).
"""

from __future__ import annotations

import auth


def test_skip_auth_env_bypasses_validation(monkeypatch, caplog):
    monkeypatch.setenv("VAULTBOT_SKIP_AUTH", "1")
    monkeypatch.setattr(auth, "_auth_bypass_warned", False)
    # Should return True even with no token
    with caplog.at_level("WARNING"):
        assert auth.validate_token(None) is True
        # Warning should be emitted exactly once
        assert any(
            "VAULTBOT_SKIP_AUTH" in rec.message
            or "auth validation DISABLED" in rec.message
            for rec in caplog.records
        )


def test_skip_lock_does_not_bypass_auth(monkeypatch):
    monkeypatch.delenv("VAULTBOT_SKIP_AUTH", raising=False)
    monkeypatch.setattr(auth, "_auth_bypass_warned", False)
    monkeypatch.setenv("VAULTBOT_SKIP_LOCK", "1")
    # SKIP_LOCK should not bypass auth anymore — validate_token requires a token
    assert auth.validate_token(None) is False
