"""Unit tests for the bot-account env helper in gh_client.

Verifies that ``_bot_env`` returns an env with ``GH_TOKEN`` set to the bot
account's token when ``VAULTBOT_GH_BOT_USER`` is set, and returns None when
it is unset or the token can't be retrieved. No network access — the
``gh auth token`` subprocess is monkeypatched.
"""

import pytest

pytestmark = pytest.mark.unit

from custom_tools import gh_client


def test_bot_env_unset_returns_none(monkeypatch):
    monkeypatch.delenv("VAULTBOT_GH_BOT_USER", raising=False)
    assert gh_client._bot_env() is None


def test_bot_env_set_returns_token_env(monkeypatch):
    monkeypatch.setenv("VAULTBOT_GH_BOT_USER", "ziggibot-uni")

    class _FakeResult:
        returncode = 0
        stdout = "gho_faketoken123\n"

    monkeypatch.setattr(
        gh_client, "_subprocess_run", lambda *a, **k: _FakeResult()
    )

    env = gh_client._bot_env()
    assert env is not None
    assert env["GH_TOKEN"] == "gho_faketoken123"


def test_bot_env_token_failure_returns_none(monkeypatch):
    monkeypatch.setenv("VAULTBOT_GH_BOT_USER", "ziggibot-uni")

    class _FakeResult:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(
        gh_client, "_subprocess_run", lambda *a, **k: _FakeResult()
    )

    assert gh_client._bot_env() is None
