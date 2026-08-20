"""Unit tests for the CI gate in review_contributions.

Verifies that the merge step requires BOTH a PASS safety verdict AND green
CI (check-runs all "success"). A pending/failing/unknown CI status blocks
the merge. No network access — gh_api is monkeypatched.
"""

import pytest

pytestmark = pytest.mark.unit

from custom_tools import gh_client
from custom_tools import review_contributions as rc


def _make_pr(number=1, sha="abc123"):
    return {
        "number": number,
        "title": "Fix thing",
        "user": {"login": "alice"},
        "html_url": f"http://x/{number}",
        "head": {"ref": "branch", "sha": sha, "repo": {"full_name": "alice/vaultbot"}},
        "additions": 10,
        "deletions": 2,
    }


def _make_files():
    return [
        {
            "filename": "vaultbot/vaultbot_backend/foo.py",
            "status": "modified",
            "additions": 5,
            "deletions": 1,
            "patch": "@@ -1,1 +1,1 @@\n+def foo():\n+    return 1\n",
        }
    ]


def _run_review(monkeypatch, prs, files, check_runs, reviews=None):
    """Drive review_contributions.run with faked gh_api responses."""
    calls = {"merge": []}

    monkeypatch.setattr(gh_client, "gh_available", lambda: True)

    def _fake_gh_api(method, path, body=None, timeout=60):
        if path.endswith("/pulls/1"):
            return prs[0]
        if path.endswith("/pulls/1/files"):
            return files
        if "/check-runs" in path:
            return {"check_runs": check_runs}
        if path.endswith("/pulls/1/reviews"):
            return reviews if reviews is not None else []
        if path.endswith("/merge"):
            calls["merge"].append(body)
            return {"message": "Merged"}
        if path.endswith("/comments"):
            return {}
        return {}

    monkeypatch.setattr(gh_client, "gh_api", _fake_gh_api)
    return calls


def test_merge_blocked_when_ci_pending(monkeypatch):
    prs = [_make_pr()]
    check_runs = [{"name": "CI", "status": "in_progress", "conclusion": None}]
    calls = _run_review(monkeypatch, prs, _make_files(), check_runs)

    result = rc.run({"pr_number": 1, "merge": True})
    r = result["results"][0]
    assert r["ci_status"] == "pending"
    assert r["merged"] is False
    assert "CI not green" in r["merge_error"]
    assert calls["merge"] == []  # merge API never called


def test_merge_blocked_when_ci_failure(monkeypatch):
    prs = [_make_pr()]
    check_runs = [{"name": "CI", "status": "completed", "conclusion": "failure"}]
    calls = _run_review(monkeypatch, prs, _make_files(), check_runs)

    result = rc.run({"pr_number": 1, "merge": True})
    r = result["results"][0]
    assert r["ci_status"] == "failure"
    assert r["merged"] is False
    assert calls["merge"] == []


def test_merge_proceeds_when_ci_success(monkeypatch):
    prs = [_make_pr()]
    check_runs = [{"name": "CI", "status": "completed", "conclusion": "success"}]
    reviews = [{"state": "APPROVED", "user": {"login": "Ziggibot0"}}]
    calls = _run_review(monkeypatch, prs, _make_files(), check_runs, reviews)

    result = rc.run({"pr_number": 1, "merge": True})
    r = result["results"][0]
    assert r["ci_status"] == "success"
    assert r["approval_state"] == "approved"
    assert r["merged"] is True
    # Merge method must be squash (repo convention).
    assert calls["merge"][0]["merge_method"] == "squash"


def test_merge_blocked_when_no_approval(monkeypatch):
    """Green CI but no code-owner approval blocks the merge."""
    prs = [_make_pr()]
    check_runs = [{"name": "CI", "status": "completed", "conclusion": "success"}]
    calls = _run_review(monkeypatch, prs, _make_files(), check_runs, reviews=[])

    result = rc.run({"pr_number": 1, "merge": True})
    r = result["results"][0]
    assert r["ci_status"] == "success"
    assert r["approval_state"] == "pending"
    assert r["merged"] is False
    assert "Awaiting code-owner approval" in r["merge_error"]
    assert calls["merge"] == []


def test_merge_blocked_when_approval_unknown(monkeypatch):
    """Approval state that can't be confirmed blocks the merge (fail-loud)."""
    prs = [_make_pr()]
    check_runs = [{"name": "CI", "status": "completed", "conclusion": "success"}]

    monkeypatch.setattr(gh_client, "gh_available", lambda: True)

    def _fake_gh_api(method, path, body=None, timeout=60):
        if path.endswith("/pulls/1"):
            return prs[0]
        if path.endswith("/pulls/1/files"):
            return _make_files()
        if "/check-runs" in path:
            return {"check_runs": check_runs}
        if path.endswith("/pulls/1/reviews"):
            raise gh_client.GhError("boom")
        if path.endswith("/merge"):
            return {"message": "Merged"}
        if path.endswith("/comments"):
            return {}
        return {}

    monkeypatch.setattr(gh_client, "gh_api", _fake_gh_api)

    result = rc.run({"pr_number": 1, "merge": True})
    r = result["results"][0]
    assert r["approval_state"] == "error"
    assert r["merged"] is False
    assert "Could not confirm approval" in r["merge_error"]


def test_merge_blocked_when_no_check_runs(monkeypatch):
    prs = [_make_pr()]
    calls = _run_review(monkeypatch, prs, _make_files(), [])

    result = rc.run({"pr_number": 1, "merge": True})
    r = result["results"][0]
    assert r["ci_status"] == "none"
    assert r["merged"] is False
    assert calls["merge"] == []


def test_review_only_does_not_merge(monkeypatch):
    prs = [_make_pr()]
    check_runs = [{"name": "CI", "status": "completed", "conclusion": "success"}]
    calls = _run_review(monkeypatch, prs, _make_files(), check_runs)

    result = rc.run({"pr_number": 1, "merge": False})
    r = result["results"][0]
    assert r["ci_status"] == "success"
    assert "merged" not in r
    assert calls["merge"] == []
