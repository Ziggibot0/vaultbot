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

import hashlib
import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.unit

from custom_tools import google_workspace as gw


@pytest.fixture
def patched_paths(tmp_path, monkeypatch):
    """Point CONFIG_PATH and TOKEN_PATH at a throwaway tmp tree."""
    monkeypatch.setattr(gw, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(gw, "TOKEN_PATH", tmp_path / "tokens.json")
    return tmp_path


@pytest.fixture(autouse=True)
def clear_pending_states(monkeypatch):
    """Clear the module-level in-flight OAuth state and env vars between tests."""
    gw._PENDING_STATES.clear()
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    yield
    gw._PENDING_STATES.clear()


@pytest.fixture(autouse=True)
def fake_secret_store(monkeypatch):
    store = {}

    def fake_load(entry):
        return dict(store.get(entry, {}))

    def fake_save(entry, payload):
        store[entry] = json.loads(json.dumps(payload))

    monkeypatch.setattr(gw, "_load_secret_blob", fake_load)
    monkeypatch.setattr(gw, "_save_secret_blob", fake_save)
    return store


# ── config / token persistence ───────────────────────────────────────────────


def test_load_config_missing_returns_empty(patched_paths):
    assert gw._load_config() == {}


def test_save_and_load_config_roundtrip(patched_paths, fake_secret_store):
    gw._save_config({"client_id": "abc", "client_secret": "xyz"})
    assert gw._load_config() == {"client_id": "abc", "client_secret": "xyz"}
    assert json.loads(gw.CONFIG_PATH.read_text(encoding="utf-8")) == {
        "client_id": "abc"
    }
    assert fake_secret_store[gw.CONFIG_SECRET_ENTRY] == {"client_secret": "xyz"}


def test_load_tokens_missing_returns_none(patched_paths):
    assert gw._load_tokens() is None


def test_save_and_load_tokens_roundtrip(patched_paths, fake_secret_store):
    gw._save_tokens({"access_token": "tok", "refresh_token": "ref"})
    assert gw._load_tokens() == {"access_token": "tok", "refresh_token": "ref"}
    assert json.loads(gw.TOKEN_PATH.read_text(encoding="utf-8")) == {}
    assert fake_secret_store[gw.TOKEN_SECRET_ENTRY] == {
        "access_token": "tok",
        "refresh_token": "ref",
    }


def test_get_credentials_empty_when_unconfigured(patched_paths):
    assert gw._get_credentials() == (None, None)


def test_get_credentials_returns_stored(patched_paths):
    gw._save_config({"client_id": "cid", "client_secret": "csec"})
    assert gw._get_credentials() == ("cid", "csec")


def test_load_config_migrates_legacy_plaintext_secret(patched_paths, fake_secret_store):
    gw.CONFIG_PATH.write_text(
        json.dumps({"client_id": "cid", "client_secret": "csec"}),
        encoding="utf-8",
    )
    assert gw._load_config() == {"client_id": "cid", "client_secret": "csec"}
    assert json.loads(gw.CONFIG_PATH.read_text(encoding="utf-8")) == {
        "client_id": "cid"
    }
    assert fake_secret_store[gw.CONFIG_SECRET_ENTRY] == {"client_secret": "csec"}


def test_load_tokens_migrates_legacy_plaintext_tokens(patched_paths, fake_secret_store):
    gw.TOKEN_PATH.write_text(
        json.dumps({"access_token": "tok", "refresh_token": "ref", "expires_in": 3600}),
        encoding="utf-8",
    )
    assert gw._load_tokens() == {
        "access_token": "tok",
        "refresh_token": "ref",
        "expires_in": 3600,
    }
    assert json.loads(gw.TOKEN_PATH.read_text(encoding="utf-8")) == {"expires_in": 3600}
    assert fake_secret_store[gw.TOKEN_SECRET_ENTRY] == {
        "access_token": "tok",
        "refresh_token": "ref",
    }


# New tests for permission hardening behavior


def test_save_helpers_attempt_chmod_on_posix(patched_paths, monkeypatch):
    calls = []

    def fake_chmod(path, mode):
        calls.append((str(path), mode))

    monkeypatch.setattr(gw.os, "name", "posix")
    monkeypatch.setattr(gw.os, "chmod", fake_chmod)

    gw._save_config({"client_id": "x"})
    gw._save_tokens({"access_token": "t"})

    config_targets = {
        f"{gw.CONFIG_PATH}.tmp",
        str(gw.CONFIG_PATH),
    }
    token_targets = {
        f"{gw.TOKEN_PATH}.tmp",
        str(gw.TOKEN_PATH),
    }
    chmod_targets = {path for path, _mode in calls}

    assert config_targets.issubset(chmod_targets)
    assert token_targets.issubset(chmod_targets)
    assert all(mode == 0o600 for _path, mode in calls)


def test_save_helpers_swallow_oserror_from_chmod(patched_paths, monkeypatch):
    calls = []

    def raising_chmod(path, mode):
        calls.append((str(path), mode))
        raise OSError("nope")

    monkeypatch.setattr(gw.os, "name", "posix")
    monkeypatch.setattr(gw.os, "chmod", raising_chmod)

    # Should not raise
    gw._save_config({"client_id": "x"})
    gw._save_tokens({"access_token": "t"})
    expected_targets = {
        f"{gw.CONFIG_PATH}.tmp",
        str(gw.CONFIG_PATH),
        f"{gw.TOKEN_PATH}.tmp",
        str(gw.TOKEN_PATH),
    }
    assert {path for path, _mode in calls} == expected_targets
    assert all(mode == 0o600 for _path, mode in calls)


# ── run() dispatch: setup / auth / callback / status ─────────────────────────


def test_setup_requires_both_credentials(patched_paths):
    assert gw.run({"action": "setup"}) == {
        "error": "client_id and client_secret are required for setup"
    }


def test_setup_saves_credentials(patched_paths):
    result = gw.run({"action": "setup", "client_id": "cid", "client_secret": "csec"})
    assert result["status"] == "ok"
    assert gw._load_config() == {"client_id": "cid", "client_secret": "csec"}


def test_auth_requires_credentials(patched_paths, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    result = gw.run({"action": "auth"})
    assert "error" in result
    assert "not configured" in result["error"]


def test_auth_returns_url_with_expected_params(patched_paths, monkeypatch):
    monkeypatch.setattr(gw.webbrowser, "open", lambda url: None)
    gw._save_config({"client_id": "cid", "client_secret": "csec"})
    result = gw.run({"action": "auth"})
    assert result["status"] == "auth_started"
    assert "accounts.google.com/o/oauth2/v2/auth" in result["auth_url"]
    assert "client_id=cid" in result["auth_url"]
    assert "access_type=offline" in result["auth_url"]


def test_auth_issues_state_and_pkce(patched_paths, monkeypatch):
    monkeypatch.setattr(gw.webbrowser, "open", lambda url: None)
    gw._save_config({"client_id": "cid", "client_secret": "csec"})
    result = gw.run({"action": "auth"})
    url = result["auth_url"]
    assert "state=" in url
    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    # The issued state must be tracked with a verifier for the callback.
    assert len(gw._PENDING_STATES) == 1
    state, verifier = next(iter(gw._PENDING_STATES.items()))
    assert state in url
    # The challenge must be the S256 hash of the verifier.
    expected = gw._b64url(hashlib.sha256(verifier.encode()).digest())
    assert f"code_challenge={expected}" in url


def test_callback_success_exchanges_code(patched_paths, monkeypatch):
    import urllib.request

    gw._save_config({"client_id": "cid", "client_secret": "csec"})
    # Issue a real state via 'auth'.
    monkeypatch.setattr(gw.webbrowser, "open", lambda url: None)
    gw.run({"action": "auth"})
    state, verifier = next(iter(gw._PENDING_STATES.items()))

    captured = {}

    class FakeResp:
        def read(self):
            return json.dumps(
                {"access_token": "tok", "refresh_token": "ref", "expires_in": 3600}
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=10):
        captured["data"] = req.data.decode()
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = gw.run({"action": "callback", "code": "abc", "state": state})
    assert result["status"] == "ok"
    # The exchange must include the PKCE verifier and the code.
    assert "code=abc" in captured["data"]
    assert f"code_verifier={verifier}" in captured["data"]
    assert "grant_type=authorization_code" in captured["data"]
    # The state is consumed (single-use).
    assert state not in gw._PENDING_STATES


def test_callback_requires_code(patched_paths):
    assert gw.run({"action": "callback"}) == {
        "error": "Authorization code is required for callback."
    }


def test_callback_rejects_missing_state(patched_paths):
    result = gw.run({"action": "callback", "code": "abc"})
    assert "error" in result
    assert "Invalid or missing OAuth state" in result["error"]


def test_callback_rejects_unknown_state(patched_paths):
    result = gw.run({"action": "callback", "code": "abc", "state": "bogus"})
    assert "error" in result
    assert "Invalid or missing OAuth state" in result["error"]


def test_callback_requires_credentials(patched_paths):
    # A valid state is issued by 'auth', but no credentials are configured.
    gw._save_config({})
    state = gw.run({"action": "auth"})  # no credentials → error, no state issued
    assert "error" in state
    # Manually seed a pending state to reach the credentials check.
    gw._PENDING_STATES["s"] = "verifier"
    assert gw.run({"action": "callback", "code": "abc", "state": "s"}) == {
        "error": "No credentials configured."
    }


def test_status_unconfigured(patched_paths, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    result = gw.run({"action": "status"})
    assert result["configured"] is False
    assert result["authenticated"] is False
    assert "hint" in result
    assert len(result["hint"]) > 0


def test_status_configured_but_unauthenticated(patched_paths, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    gw._save_config({"client_id": "cid", "client_secret": "csec"})
    result = gw.run({"action": "status"})
    assert result["configured"] is True
    assert result["authenticated"] is False
    assert "sign_in" in result["hint"]


def test_status_authenticated(patched_paths, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    gw._save_config({"client_id": "cid", "client_secret": "csec"})
    gw._save_tokens(
        {
            "access_token": "tok",
            "obtained_at": datetime.now(UTC).isoformat(),
            "expires_in": 3600,
        }
    )
    result = gw.run({"action": "status"})
    assert result["configured"] is True
    assert result["authenticated"] is True
    assert result["hint"] == ""


def test_unknown_action_returns_error(patched_paths):
    assert gw.run({"action": "bogus"}) == {"error": "Unknown action: bogus"}


# ── env-var credential support (#373) ──────────────────────────────────────


def test_get_credentials_from_env_vars(patched_paths, monkeypatch):
    """Env vars take priority over config file."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-csec")
    # Even if config file has different values, env vars win.
    gw._save_config({"client_id": "file-cid", "client_secret": "file-csec"})
    assert gw._get_credentials() == ("env-cid", "env-csec")


def test_get_credentials_falls_back_to_config(patched_paths, monkeypatch):
    """When env vars are not set, fall back to config file."""
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    gw._save_config({"client_id": "file-cid", "client_secret": "file-csec"})
    assert gw._get_credentials() == ("file-cid", "file-csec")


def test_auth_works_with_env_vars_only(patched_paths, monkeypatch):
    """User can sign in without running setup — just env vars."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-csec")
    monkeypatch.setattr(gw.webbrowser, "open", lambda url: None)
    result = gw.run({"action": "auth"})
    assert result["status"] == "auth_started"
    assert "client_id=env-cid" in result["auth_url"]


def test_sign_in_alias_works(patched_paths, monkeypatch):
    """sign_in is a user-friendly alias for auth."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-csec")
    monkeypatch.setattr(gw.webbrowser, "open", lambda url: None)
    result = gw.run({"action": "sign_in"})
    assert result["status"] == "auth_started"
    # Parse the host precisely rather than substring-matching the URL
    # (substring checks on URLs are spoofable, e.g. "accounts.google.com.evil.com").
    parsed = urllib.parse.urlparse(result["auth_url"])
    assert parsed.hostname == "accounts.google.com"


def test_sign_in_requires_credentials(patched_paths, monkeypatch):
    """sign_in gives a helpful error when no credentials configured."""
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    result = gw.run({"action": "sign_in"})
    assert "error" in result
    assert "not configured" in result["error"]


def test_status_shows_configured_with_env_vars(patched_paths, monkeypatch):
    """status reports configured=True when env vars are set, no config file."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-csec")
    result = gw.run({"action": "status"})
    assert result["configured"] is True
    assert result["authenticated"] is False
    assert "sign_in" in result["hint"]


def test_sign_in_action_in_enum(patched_paths):
    """sign_in must be in the action enum so it's not rejected as unknown."""
    assert "sign_in" in gw.SCHEMA["parameters"]["properties"]["action"]["enum"]


# ── _get_access_token ────────────────────────────────────────────────────────


def test_get_access_token_no_tokens(patched_paths):
    token, err = gw._get_access_token()
    assert token is None
    assert "No tokens stored" in err


def test_get_access_token_returns_stored(patched_paths):
    gw._save_tokens(
        {
            "access_token": "tok",
            "obtained_at": datetime.now(UTC).isoformat(),
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

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=10: FakeResp())
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

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=30: FakeResp())
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
        urllib.request,
        "urlopen",
        lambda req, timeout=30: (_ for _ in ()).throw(FakeHTTPError()),
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


# ── calendar student-ux extensions (#271) ──────────────────────────────────


def test_calendar_list_surfaces_recurrence_and_reminders(patched_paths, monkeypatch):
    monkeypatch.setattr(gw, "_get_access_token", lambda: ("tok", None))

    def fake_api(method, url, token, data=None):
        assert method == "GET"
        return {
            "items": [
                {
                    "id": "evt1",
                    "summary": "Cell Bio Lab",
                    "start": {"dateTime": "2026-09-01T09:00:00Z"},
                    "end": {"dateTime": "2026-09-01T10:00:00Z"},
                    "recurrence": ["RRULE:FREQ=WEEKLY;COUNT=8"],
                    "recurringEventId": "series1",
                    "reminders": {"useDefault": False, "overrides": []},
                }
            ]
        }

    monkeypatch.setattr(gw, "_api_request", fake_api)
    result = gw.run({"action": "calendar_list", "time_min": "2026-09-01T00:00:00Z"})
    assert result["events"][0]["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=8"]
    assert result["events"][0]["recurring_event_id"] == "series1"
    assert result["events"][0]["reminders"]["useDefault"] is False


def test_calendar_create_includes_recurrence_and_reminders(patched_paths, monkeypatch):
    monkeypatch.setattr(gw, "_get_access_token", lambda: ("tok", None))
    monkeypatch.setattr(gw, "_list_event_conflicts", lambda *a, **k: {"conflicts": []})

    captured = {}

    def fake_api(method, url, token, data=None):
        captured["method"] = method
        captured["url"] = url
        captured["data"] = data
        return {"id": "evt-created"}

    monkeypatch.setattr(gw, "_api_request", fake_api)
    result = gw.run(
        {
            "action": "calendar_create",
            "summary": "Exam",
            "start": "2026-09-10T13:00:00Z",
            "end": "2026-09-10T14:00:00Z",
            "recurrence": ["RRULE:FREQ=WEEKLY;COUNT=2"],
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": 1440}],
            },
        }
    )
    assert result["id"] == "evt-created"
    assert captured["method"] == "POST"
    assert captured["data"]["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=2"]
    assert captured["data"]["reminders"]["overrides"][0]["minutes"] == 1440


def test_calendar_create_blocks_on_conflict(patched_paths, monkeypatch):
    monkeypatch.setattr(gw, "_get_access_token", lambda: ("tok", None))
    monkeypatch.setattr(
        gw,
        "_list_event_conflicts",
        lambda *a, **k: {
            "conflicts": [
                {
                    "id": "evt-existing",
                    "summary": "Existing class",
                    "start": "2026-09-10T13:00:00Z",
                    "end": "2026-09-10T14:00:00Z",
                }
            ]
        },
    )
    result = gw.run(
        {
            "action": "calendar_create",
            "summary": "Exam",
            "start": "2026-09-10T13:00:00Z",
            "end": "2026-09-10T14:00:00Z",
        }
    )
    assert "error" in result
    assert "conflicts" in result
    assert result["conflicts"][0]["id"] == "evt-existing"


def test_calendar_update_requires_event_id(patched_paths, monkeypatch):
    monkeypatch.setattr(gw, "_get_access_token", lambda: ("tok", None))
    result = gw.run({"action": "calendar_update", "summary": "new"})
    assert result == {"error": "event_id is required for calendar_update."}


def test_calendar_update_uses_patch(patched_paths, monkeypatch):
    monkeypatch.setattr(gw, "_get_access_token", lambda: ("tok", None))
    monkeypatch.setattr(gw, "_list_event_conflicts", lambda *a, **k: {"conflicts": []})

    captured = {}

    def fake_api(method, url, token, data=None):
        captured["method"] = method
        captured["url"] = url
        captured["data"] = data
        return {"id": "evt1", "status": "confirmed"}

    monkeypatch.setattr(gw, "_api_request", fake_api)
    result = gw.run(
        {
            "action": "calendar_update",
            "event_id": "evt1",
            "summary": "Updated",
            "start": "2026-09-10T15:00:00Z",
            "end": "2026-09-10T16:00:00Z",
        }
    )
    assert result["id"] == "evt1"
    assert captured["method"] == "PATCH"
    assert "evt1" in captured["url"]
    assert captured["data"]["summary"] == "Updated"


def test_calendar_delete_requires_event_id(patched_paths, monkeypatch):
    monkeypatch.setattr(gw, "_get_access_token", lambda: ("tok", None))
    result = gw.run({"action": "calendar_delete"})
    assert result == {"error": "event_id is required for calendar_delete."}


def test_calendar_delete_returns_deleted_status(patched_paths, monkeypatch):
    monkeypatch.setattr(gw, "_get_access_token", lambda: ("tok", None))
    monkeypatch.setattr(gw, "_api_request", lambda *a, **k: {})
    result = gw.run({"action": "calendar_delete", "event_id": "evt1"})
    assert result["status"] == "deleted"
    assert result["event_id"] == "evt1"


def test_calendar_freebusy_returns_busy_windows(patched_paths, monkeypatch):
    monkeypatch.setattr(gw, "_get_access_token", lambda: ("tok", None))

    def fake_api(method, url, token, data=None):
        assert method == "POST"
        assert "freeBusy" in url
        return {
            "calendars": {
                "primary": {
                    "busy": [
                        {
                            "start": "2026-09-10T13:00:00Z",
                            "end": "2026-09-10T14:00:00Z",
                        }
                    ]
                }
            }
        }

    monkeypatch.setattr(gw, "_api_request", fake_api)
    result = gw.run(
        {
            "action": "calendar_freebusy",
            "time_min": "2026-09-10T00:00:00Z",
            "time_max": "2026-09-11T00:00:00Z",
        }
    )
    assert result["calendar_id"] == "primary"
    assert len(result["busy"]) == 1
