"""Unit tests for the google_workspace custom tool.

These tests cover the pure-logic surface of the OAuth + Google API
integration with NO network access. All module-level paths
(CONFIG_PATH, TOKEN_PATH) are monkeypatched to a throwaway tmp_path so no
test ever reads or writes the real credential files.

Network calls (urllib.request.urlopen) are monkeypatched with fake
responses so the token-refresh and API-request branches are exercised
deterministically.

Only the leaf module `custom_tools.google_workspace` is imported — never
`main` (see conftest.py hard-fence).
"""

import json
from datetime import datetime, timezone
import urllib.request

import pytest

pytestmark = pytest.mark.unit

from custom_tools import google_workspace as gw


@pytest.fixture
def patched_paths(tmp_path, monkeypatch):
    """Point CONFIG_PATH and TOKEN_PATH at a throwaway tmp tree."""
    monkeypatch.setattr(gw, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(gw, "TOKEN_PATH", tmp_path / "tokens.json")
    return tmp_path


# ── config / token persistence ───────────────────────────────────────────────


def test_load_config_missing_returns_empty(patched_paths):
    assert gw._load_config() == {}


def test_save_and_load_config_roundtrip(patched_paths):
    gw._save_config({"client_id": "abc", "client_secret": "xyz"})
    assert gw._load_config() == {"client_id": "abc", "client_secret": "xyz"}


def test_load_tokens_missing_returns_none(patched_paths):
    assert gw._load_tokens() is None


def test_save_and_load_tokens_roundtrip(patched_paths):
    gw._save_tokens({"access_token": "tok", "refresh_token": "ref"})
    assert gw._load_tokens() == {"access_token": "tok", "refresh_token": "ref"}


def test_get_credentials_empty_when_unconfigured(patched_paths):
    assert gw._get_credentials() == (None, None)


def test_get_credentials_returns_stored(patched_paths):
    gw._save_config({"client_id": "cid", "client_secret": "csec"})
    assert gw._get_credentials() == ("cid", "csec")


# ── run() dispatch: setup / auth / callback / status ─────────────────────────


def test_setup_requires_both_credentials(patched_paths):
    assert gw.run({"action": "setup"}) == {
        "error": "client_id and client_secret are required for setup"
    }


def test_setup_saves_credentials(patched_paths):
    result = gw.run(
        {"action": "setup", "client_id": "cid", "client_secret": "csec"}
    )
    assert result["status"] == "ok"
    assert gw._load_config() == {"client_id": "cid", "client_secret": "csec"}


def test_auth_requires_credentials(patched_paths):
    assert gw.run({"action": "auth"}) == {
        "error": "No credentials configured. Run 'setup' first."
    }


def test_auth_returns_url_with_expected_params(patched_paths, monkeypatch):
    monkeypatch.setattr(gw.webbrowser, "open", lambda url: None)
    gw._save_config({"client_id": "cid", "client_secret": "csec"})
    result = gw.run({"action": "auth"})
    assert result["status"] == "auth_started"
    assert "accounts.google.com/o/oauth2/v2/auth" in result["auth_url"]
    assert "client_id=cid" in result["auth_url"]
    assert "access_type=offline" in result["auth_url"]


def test_callback_requires_code(patched_paths):
    assert gw.run({"action": "callback"}) == {
        "error": "Authorization code is required for callback."
    }


def test_callback_requires_credentials(patched_paths):
    assert gw.run({"action": "callback", "code": "abc"}) == {
        "error": "No credentials configured."
    }


def test_status_unconfigured(patched_paths):
    result = gw.run({"action": "status"})
    assert result["configured"] is False
    assert result["authenticated"] is False


def test_status_configured_but_unauthenticated(patched_paths):
    gw._save_config({"client_id": "cid", "client_secret": "csec"})
    result = gw.run({"action": "status"})
    assert result["configured"] is True
    assert result["authenticated"] is False


def test_status_authenticated(patched_paths):
    gw._save_config({"client_id": "cid", "client_secret": "csec"})
    gw._save_tokens(
        {
            "access_token": "tok",
            "obtained_at": datetime.now(timezone.utc).isoformat(),
            "expires_in": 3600,
        }
    )
    result = gw.run({"action": "status"})
    assert result["configured"] is True
    assert result["authenticated"] is True


def test_unknown_action_returns_error(patched_paths):
    assert gw.run({"action": "bogus"}) == {"error": "Unknown action: bogus"}


# ── _get_access_token ────────────────────────────────────────────────────────


def test_get_access_token_no_tokens(patched_paths):
    token, err = gw._get_access_token()
    assert token is None
    assert "No tokens stored" in err


def test_get_access_token_returns_stored(patched_paths):
    gw._save_tokens(
        {
            "access_token": "tok",
            "obtained_at": datetime.now(timezone.utc).isoformat(),
            "expires_in": 3600,
        }
    )
    token, err = gw._get_access_token()
    assert token == "tok"
    assert err is None


# ── _refresh_tokens ──────────────────────────────────────────────────────────


def test_refresh_tokens_no_tokens(patched_paths):
    assert gw._refresh_tokens() is None


def test_refresh_tokens_no_refresh_token(patched_paths):
    gw._save_tokens({"access_token": "tok"})
    assert gw._refresh_tokens() is None


def test_refresh_tokens_no_client_id(patched_paths):
    gw._save_tokens({"access_token": "tok", "refresh_token": "ref"})
    assert gw._refresh_tokens() is None


def test_refresh_tokens_success(patched_paths, monkeypatch):
    gw._save_config({"client_id": "cid", "client_secret": "csec"})
    gw._save_tokens({"access_token": "old", "refresh_token": "ref"})

    class FakeResp:
        def read(self):
            return json.dumps({"access_token": "new", "expires_in": 3600}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout=10: FakeResp()
    )
    result = gw._refresh_tokens()
    assert result["access_token"] == "new"
    # refresh_token is preserved across refresh
    assert result["refresh_token"] == "ref"
    assert "obtained_at" in result


def test_refresh_tokens_error(patched_paths, monkeypatch):
    import urllib.error

    gw._save_config({"client_id": "cid", "client_secret": "csec"})
    gw._save_tokens({"access_token": "old", "refresh_token": "ref"})

    def boom(req, timeout=10):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    result = gw._refresh_tokens()
    assert "error" in result
    assert "network down" in result["error"]


# ── _api_request ─────────────────────────────────────────────────────────────


def test_api_request_success(patched_paths, monkeypatch):
    class FakeResp:
        def read(self):
            return json.dumps({"items": []}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout=30: FakeResp()
    )
    result = gw._api_request("GET", "https://example.com", "tok")
    assert result == {"items": []}


def test_api_request_http_error(patched_paths, monkeypatch):
    import urllib.error

    class FakeHTTPError(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("https://example.com", 401, "Unauthorized", {}, None)

        def read(self):
            return b'{"error": "invalid_token"}'

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout=30: (_ for _ in ()).throw(FakeHTTPError())
    )
    result = gw._api_request("GET", "https://example.com", "tok")
    assert "error" in result
    assert "401" in result["error"]


def test_api_request_generic_error(patched_paths, monkeypatch):
    import urllib.error

    def boom(req, timeout=30):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    result = gw._api_request("GET", "https://example.com", "tok")
    assert "error" in result
    assert "boom" in result["error"]
