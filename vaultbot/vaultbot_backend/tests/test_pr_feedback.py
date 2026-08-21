"""Unit tests for the pr_feedback custom tool.

Covers the pure-logic surface with NO network access: gh_api calls are
monkeypatched. Only the leaf module `custom_tools.pr_feedback` is imported.
"""

import pytest

pytestmark = pytest.mark.unit

from custom_tools import gh_client
from custom_tools import pr_feedback as pf


@pytest.fixture
def fake_gh(monkeypatch):
    """Replace gh_client.gh_available + gh_api with deterministic fakes."""
    calls = {"api": []}

    monkeypatch.setattr(gh_client, "gh_available", lambda: True)
    monkeypatch.setenv("VAULTBOT_ALLOW_CONTRIBUTIONS", "true")

    def _fake_gh_api(method, path, body=None, timeout=60):
        calls["api"].append((method, path, body))
        # PR metadata
        if path.endswith("/pulls/67"):
            return {
                "number": 67,
                "title": "Add create action",
                "state": "open",
                "merged": False,
                "mergeable": True,
                "html_url": "http://x/67",
                "head": {"sha": "abc123", "ref": "add-create"},
                "additions": 162,
                "deletions": 11,
            }
        # Check-runs for the head commit
        if "/commits/abc123/check-runs" in path:
            return {
                "check_runs": [
                    {
                        "name": "CI / ruff + pyright + pytest (Python 3.12)",
                        "status": "completed",
                        "conclusion": "failure",
                        "html_url": "http://x/checks/999",
                    },
                    {
                        "name": "CI / ruff + pyright + pytest (Python 3.11)",
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": "http://x/checks/998",
                    },
                ]
            }
        # Annotations for the failed check-run (ID 999)
        if "/check-runs/999/annotations" in path:
            return [
                {
                    "annotation_level": "failure",
                    "message": "SIM102: Use a single `if` statement",
                    "path": "vaultbot_backend/self_improver.py",
                    "start_line": 116,
                    "html_url": "http://x/ann/1",
                }
            ]
        # Annotations for the passing check-run (ID 998) — empty
        if "/check-runs/998/annotations" in path:
            return []
        # Issue-level comments
        if path.endswith("/issues/67/comments"):
            return [
                {
                    "user": {"login": "Ziggibot0"},
                    "body": "Please fix the ruff error.",
                    "created_at": "2026-08-19T12:00:00Z",
                }
            ]
        # Inline review comments
        if path.endswith("/pulls/67/comments"):
            return [
                {
                    "user": {"login": "Ziggibot0"},
                    "body": "Consider combining these ifs",
                    "path": "vaultbot_backend/self_improver.py",
                    "line": 116,
                    "created_at": "2026-08-19T12:01:00Z",
                }
            ]
        return {}

    monkeypatch.setattr(gh_client, "gh_api", _fake_gh_api)
    return calls


@pytest.fixture
def contributions_off(monkeypatch):
    monkeypatch.setenv("VAULTBOT_ALLOW_CONTRIBUTIONS", "false")
    monkeypatch.setattr(gh_client, "gh_available", lambda: True)


def test_requires_pr_number(fake_gh):
    result = pf.run({})
    assert "error" in result
    assert "pr_number" in result["error"]


def test_blocked_when_contributions_off(contributions_off):
    result = pf.run({"pr_number": 67})
    assert "error" in result
    assert "Contributions are not enabled" in result["error"]


def test_blocked_when_gh_not_available(monkeypatch):
    monkeypatch.setenv("VAULTBOT_ALLOW_CONTRIBUTIONS", "true")
    monkeypatch.setattr(gh_client, "gh_available", lambda: False)
    result = pf.run({"pr_number": 67})
    assert "error" in result
    assert "gh CLI" in result["error"]


def test_returns_pr_state(fake_gh):
    result = pf.run({"pr_number": 67})
    assert result["pr_number"] == 67
    assert result["title"] == "Add create action"
    assert result["state"] == "open"
    assert result["merged"] is False
    assert result["mergeable"] is True


def test_returns_ci_checks(fake_gh):
    result = pf.run({"pr_number": 67})
    assert result["ci_summary"] == "failure"
    assert len(result["ci_checks"]) == 2
    assert result["ci_checks"][0]["conclusion"] == "failure"
    assert result["ci_checks"][1]["conclusion"] == "success"


def test_returns_failed_annotations(fake_gh):
    result = pf.run({"pr_number": 67})
    assert len(result["failed_annotations"]) == 1
    ann = result["failed_annotations"][0]
    assert ann["check"] == "CI / ruff + pyright + pytest (Python 3.12)"
    assert "SIM102" in ann["message"]
    assert ann["path"] == "vaultbot_backend/self_improver.py"
    assert ann["start_line"] == 116


def test_returns_issue_comments(fake_gh):
    result = pf.run({"pr_number": 67})
    assert len(result["issue_comments"]) == 1
    assert result["issue_comments"][0]["author"] == "Ziggibot0"
    assert "ruff" in result["issue_comments"][0]["body"]


def test_returns_review_comments(fake_gh):
    result = pf.run({"pr_number": 67})
    assert len(result["review_comments"]) == 1
    assert result["review_comments"][0]["path"] == "vaultbot_backend/self_improver.py"
    assert result["review_comments"][0]["line"] == 116


def test_all_checks_passing(fake_gh, monkeypatch):
    """When all check-runs pass, ci_summary is 'success' and no annotations."""
    monkeypatch.setattr(
        gh_client,
        "gh_api",
        lambda method, path, body=None, timeout=60: (
            {
                "check_runs": [
                    {
                        "name": "CI",
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": "http://x/checks/1",
                    }
                ]
            }
            if "/check-runs" in path and "/annotations" not in path
            else []
            if "/annotations" in path
            else (
                {
                    "number": 67,
                    "title": "OK",
                    "state": "open",
                    "merged": False,
                    "html_url": "http://x/67",
                    "head": {"sha": "abc123", "ref": "main"},
                }
                if path.endswith("/pulls/67")
                else {}
            )
        ),
    )
    result = pf.run({"pr_number": 67})
    assert result["ci_summary"] == "success"
    assert result["failed_annotations"] == []
