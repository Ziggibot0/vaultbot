"""Unit tests for the github_issues custom tool.

Covers the pure-logic surface with NO network access: the gh_api calls are
monkeypatched, and the safe-mode gate is exercised via env var. Only the
leaf module `custom_tools.github_issues` is imported — never `main`.
"""

import importlib

import pytest

pytestmark = pytest.mark.unit

from custom_tools import gh_client
from custom_tools import github_issues as gi


@pytest.fixture
def fake_gh(monkeypatch):
    """Replace gh_client.gh_available + gh_api with deterministic fakes.

    github_issues.run() imports gh_api/gh_available from gh_client at call
    time, so we patch the gh_client module (the source of truth).
    """
    calls = {"api": []}

    monkeypatch.setattr(gh_client, "gh_available", lambda: True)

    def _fake_gh_api(method, path, body=None, timeout=60):
        calls["api"].append((method, path, body))
        # Return canned data based on the path.
        if "/issues?" in path:
            return [
                {
                    "number": 1,
                    "title": "Bug A",
                    "state": "open",
                    "labels": [{"name": "bug"}],
                    "comments": 2,
                    "created_at": "2026-01-01",
                    "html_url": "http://x/1",
                },
                {
                    "number": 2,
                    "title": "PR B",
                    "state": "open",
                    "labels": [],
                    "comments": 0,
                    "created_at": "2026-01-02",
                    "html_url": "http://x/2",
                    "pull_request": {},
                },
            ]
        if path.endswith("/issues/1/comments") and method == "POST":
            return {"html_url": "http://x/comment"}
        if "/issues/1" in path and path.endswith("/comments"):
            return [
                {"user": {"login": "alice"}, "body": "hi", "created_at": "2026-01-03"}
            ]
        if path.endswith("/issues/1"):
            return {
                "number": 1,
                "title": "Bug A",
                "state": "open",
                "user": {"login": "bob"},
                "labels": [{"name": "bug"}],
                "body": "the body",
                "html_url": "http://x/1",
            }
        if path.endswith("/issues/1/labels"):
            return [{"name": "bug"}, {"name": "fixed"}]
        return {}

    monkeypatch.setattr(gh_client, "gh_api", _fake_gh_api)
    return calls


@pytest.fixture
def developer_mode(monkeypatch):
    monkeypatch.setenv("VAULTBOT_SAFE_MODE", "0")
    import safe_mode

    importlib.reload(safe_mode)
    return safe_mode


@pytest.fixture
def safe_mode_on(monkeypatch):
    monkeypatch.setenv("VAULTBOT_SAFE_MODE", "true")
    import safe_mode

    importlib.reload(safe_mode)
    return safe_mode


def test_list_filters_out_pull_requests(fake_gh):
    result = gi.run({"action": "list"})
    assert result["status"] == "success"
    # PR B (has "pull_request" key) must be filtered out.
    assert result["count"] == 1
    assert result["issues"][0]["number"] == 1


def test_read_returns_issue_and_comments(fake_gh):
    result = gi.run({"action": "read", "issue_number": 1})
    assert result["status"] == "success"
    assert result["title"] == "Bug A"
    assert result["body"] == "the body"
    assert result["comments"][0]["author"] == "alice"


def test_comment_requires_body(fake_gh):
    result = gi.run({"action": "comment", "issue_number": 1})
    assert "error" in result


def test_comment_blocked_in_safe_mode(fake_gh, safe_mode_on):
    result = gi.run({"action": "comment", "issue_number": 1, "body": "x"})
    assert result.get("safe_mode_blocked") is True


def test_comment_allowed_in_developer_mode(fake_gh, developer_mode):
    result = gi.run({"action": "comment", "issue_number": 1, "body": "x"})
    assert result["status"] == "success"


def test_close_blocked_in_safe_mode(fake_gh, safe_mode_on):
    result = gi.run({"action": "close", "issue_number": 1})
    assert result.get("safe_mode_blocked") is True


def test_close_allowed_in_developer_mode(fake_gh, developer_mode):
    result = gi.run({"action": "close", "issue_number": 1})
    assert result["status"] == "success"


def test_label_blocked_in_safe_mode(fake_gh, safe_mode_on):
    result = gi.run({"action": "label", "issue_number": 1, "labels": ["x"]})
    assert result.get("safe_mode_blocked") is True


def test_label_allowed_in_developer_mode(fake_gh, developer_mode):
    result = gi.run({"action": "label", "issue_number": 1, "labels": ["x"]})
    assert result["status"] == "success"


def test_read_allowed_in_safe_mode(fake_gh, safe_mode_on):
    result = gi.run({"action": "read", "issue_number": 1})
    assert result["status"] == "success"


def test_list_allowed_in_safe_mode(fake_gh, safe_mode_on):
    result = gi.run({"action": "list"})
    assert result["status"] == "success"


def test_missing_action_returns_error(fake_gh):
    result = gi.run({})
    assert "error" in result


def test_unknown_action_returns_error(fake_gh):
    result = gi.run({"action": "bogus"})
    assert "error" in result
