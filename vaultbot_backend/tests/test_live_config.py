"""Unit tests for live_config.py — runtime-mutable safe mode + contributions.

Verifies the module seeds from the environment at startup and that runtime
overrides (via set_safe_mode / set_allow_contributions) take precedence
without a restart.
"""

from __future__ import annotations

import live_config
import pytest


@pytest.fixture(autouse=True)
def _reset_overrides():
    """Reset runtime overrides after each test so state doesn't leak into
    other test modules (live_config is a module-level singleton)."""
    yield
    live_config.set_safe_mode(None)
    live_config.set_allow_contributions(None)


def test_safe_mode_defaults_on(monkeypatch):
    monkeypatch.delenv("VAULTBOT_SAFE_MODE", raising=False)
    live_config.set_safe_mode(None)  # reset override
    assert live_config.is_safe_mode() is True


def test_safe_mode_env_off(monkeypatch):
    monkeypatch.setenv("VAULTBOT_SAFE_MODE", "0")
    live_config.set_safe_mode(None)
    assert live_config.is_safe_mode() is False


def test_safe_mode_runtime_override_wins(monkeypatch):
    monkeypatch.setenv("VAULTBOT_SAFE_MODE", "true")
    live_config.set_safe_mode(False)  # GUI toggled to Developer Mode
    assert live_config.is_safe_mode() is False
    live_config.set_safe_mode(True)
    assert live_config.is_safe_mode() is True


def test_contributions_defaults_off(monkeypatch):
    monkeypatch.delenv("VAULTBOT_ALLOW_CONTRIBUTIONS", raising=False)
    live_config.set_allow_contributions(None)
    assert live_config.allow_contributions() is False


def test_contributions_env_on(monkeypatch):
    monkeypatch.setenv("VAULTBOT_ALLOW_CONTRIBUTIONS", "true")
    live_config.set_allow_contributions(None)
    assert live_config.allow_contributions() is True


def test_contributions_runtime_override_wins(monkeypatch):
    monkeypatch.setenv("VAULTBOT_ALLOW_CONTRIBUTIONS", "false")
    live_config.set_allow_contributions(True)  # GUI toggled on
    assert live_config.allow_contributions() is True
    live_config.set_allow_contributions(False)
    assert live_config.allow_contributions() is False
