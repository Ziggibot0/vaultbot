"""Unit tests for the vaultbot_sync custom tool.

Covers the pure-logic surface with NO network access and NO real git
operations — git calls are monkeypatched. Only the leaf module
``custom_tools.vaultbot_sync`` is imported.
"""

import pytest

pytestmark = pytest.mark.unit

from custom_tools import vaultbot_sync as vs

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_git_results(monkeypatch, results):
    """Patch _run_git to return a sequence of canned results.

    ``results`` is a list of (success, stdout, stderr) tuples, returned in
    order for each _run_git call.
    """
    call_log = []

    def _fake_run_git(git_args, cwd):
        if results:
            r = results.pop(0)
            call_log.append((git_args, r))
            return r
        return (True, "", "")  # default: success, empty

    monkeypatch.setattr(vs, "_run_git", _fake_run_git)
    return call_log


def _make_git_root(monkeypatch, path="/fake/repo"):
    """Patch _find_git_root to always return the given path."""
    monkeypatch.setattr(vs, "_find_git_root", lambda start_dir: path)


# ─── No git root ──────────────────────────────────────────────────────────────


def test_no_git_root_returns_error(monkeypatch):
    monkeypatch.setattr(vs, "_find_git_root", lambda start_dir: None)
    result = vs.run({})
    assert "error" in result
    assert ".git" in result["error"]


# ─── Dirty working tree ───────────────────────────────────────────────────────


def test_dirty_tree_refuses_merge(monkeypatch):
    _make_git_root(monkeypatch)
    _make_git_results(
        monkeypatch,
        [
            (True, " M vaultbot/some_note.md\n?? vaultbot/new_file.md\n", ""),
        ],
    )
    result = vs.run({})
    assert "error" in result
    assert "uncommitted" in result["error"]
    assert "dirty_files" in result
    assert len(result["dirty_files"]) == 2


# ─── No upstream remote ───────────────────────────────────────────────────────


def test_no_upstream_remote_returns_error(monkeypatch):
    _make_git_root(monkeypatch)
    _make_git_results(
        monkeypatch,
        [
            (True, "", ""),  # status --porcelain (clean)
            (True, "origin\nfork\n", ""),  # remote (no upstream)
        ],
    )
    result = vs.run({})
    assert "error" in result
    assert "upstream" in result["error"]


# ─── Fetch failure ────────────────────────────────────────────────────────────


def test_fetch_failure_returns_error(monkeypatch):
    _make_git_root(monkeypatch)
    _make_git_results(
        monkeypatch,
        [
            (True, "", ""),  # status (clean)
            (True, "origin\nupstream\n", ""),  # remote (has upstream)
            (False, "", "network error"),  # fetch fails
        ],
    )
    result = vs.run({})
    assert "error" in result
    assert "fetch" in result["error"].lower()


# ─── Already up to date ───────────────────────────────────────────────────────


def test_already_up_to_date(monkeypatch):
    _make_git_root(monkeypatch)
    _make_git_results(
        monkeypatch,
        [
            (True, "", ""),  # status (clean)
            (True, "origin\nupstream\n", ""),  # remote
            (True, "Fetching upstream\n", ""),  # fetch
            (True, "main\n", ""),  # branch --show-current
            (True, "v0.1.0\n", ""),  # describe --tags (latest tag)
            (True, "0\n", ""),  # rev-list count (0 = up to date)
        ],
    )
    result = vs.run({})
    assert result["status"] == "already_up_to_date"
    assert "v0.1.0" in result["target"]


# ─── Successful sync to latest tag ────────────────────────────────────────────


def test_sync_to_latest_tag(monkeypatch):
    _make_git_root(monkeypatch)
    _make_git_results(
        monkeypatch,
        [
            (True, "", ""),  # status (clean)
            (True, "origin\nupstream\n", ""),  # remote
            (True, "Fetching upstream\n", ""),  # fetch
            (True, "main\n", ""),  # branch --show-current
            (True, "v0.2.0\n", ""),  # describe --tags
            (True, "3\n", ""),  # rev-list count (3 behind)
            (True, "abc123\n", ""),  # rev-parse HEAD (pre-merge)
            (True, "Merge made\n", ""),  # merge
            (
                True,
                "def456 Fix bug\n789abc Add feature\n012def Update docs\n",
                "",
            ),  # log
            (True, " 3 files changed\n", ""),  # diff --stat
            (True, "def456\n", ""),  # rev-parse HEAD (post-merge)
        ],
    )
    result = vs.run({})
    assert result["status"] == "success"
    assert "v0.2.0" in result["target"]
    assert "stable" in result["target"]
    assert result["merge_ref"] == "v0.2.0"
    assert result["commits_pulled"] == 3
    assert len(result["new_commits"]) == 3
    assert result["previous_head"] == "abc123"
    assert result["new_head"] == "def456"


# ─── Sync to main explicitly ──────────────────────────────────────────────────


def test_sync_to_main_explicit(monkeypatch):
    _make_git_root(monkeypatch)
    _make_git_results(
        monkeypatch,
        [
            (True, "", ""),  # status (clean)
            (True, "origin\nupstream\n", ""),  # remote
            (True, "Fetching upstream\n", ""),  # fetch
            (True, "main\n", ""),  # branch --show-current
            # No describe call — target=main skips tag lookup
            (True, "5\n", ""),  # rev-list count (5 behind)
            (True, "abc123\n", ""),  # rev-parse HEAD (pre-merge)
            (True, "Merge made\n", ""),  # merge
            (True, "def456 Fix bug\n", ""),  # log (1 commit)
            (True, " 1 file changed\n", ""),  # diff --stat
            (True, "def456\n", ""),  # rev-parse HEAD (post-merge)
        ],
    )
    result = vs.run({"target": "main"})
    assert result["status"] == "success"
    assert result["merge_ref"] == "upstream/main"
    assert "bleeding edge" in result["target"]


# ─── No tags — falls back to main ─────────────────────────────────────────────


def test_no_tags_falls_back_to_main(monkeypatch):
    _make_git_root(monkeypatch)
    _make_git_results(
        monkeypatch,
        [
            (True, "", ""),  # status (clean)
            (True, "origin\nupstream\n", ""),  # remote
            (True, "Fetching upstream\n", ""),  # fetch
            (True, "main\n", ""),  # branch --show-current
            (False, "", "no tag"),  # describe --tags fails (no tags)
            (True, "2\n", ""),  # rev-list count (2 behind)
            (True, "abc123\n", ""),  # rev-parse HEAD (pre-merge)
            (True, "Merge made\n", ""),  # merge
            (True, "def456 Fix bug\nxyz789 Add feature\n", ""),  # log
            (True, " 2 files changed\n", ""),  # diff --stat
            (True, "def456\n", ""),  # rev-parse HEAD (post-merge)
        ],
    )
    result = vs.run({})
    assert result["status"] == "success"
    assert result["merge_ref"] == "upstream/main"
    assert "no tags" in result["target"]


# ─── Merge conflict ───────────────────────────────────────────────────────────


def test_merge_conflict_reports_files(monkeypatch):
    _make_git_root(monkeypatch)
    _make_git_results(
        monkeypatch,
        [
            (True, "", ""),  # status (clean)
            (True, "origin\nupstream\n", ""),  # remote
            (True, "Fetching upstream\n", ""),  # fetch
            (True, "main\n", ""),  # branch --show-current
            (True, "v0.2.0\n", ""),  # describe --tags
            (True, "3\n", ""),  # rev-list count (3 behind)
            (True, "abc123\n", ""),  # rev-parse HEAD (pre-merge)
            (False, "Auto-merge failed\n", "conflict in file.py"),  # merge fails
            (True, "vaultbot/some_file.py\nvaultbot/other.py\n", ""),  # conflict files
        ],
    )
    result = vs.run({})
    assert "error" in result
    assert "conflict" in result["error"].lower()
    assert "conflicted_files" in result
    assert len(result["conflicted_files"]) == 2


# ─── _find_git_root (real, no monkeypatch) ────────────────────────────────────


def test_find_git_root_walks_up(tmp_path):
    # Create a fake repo structure: tmp_path / repo / vaultbot / backend
    repo = tmp_path / "myrepo"
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True)
    start = repo / "vaultbot" / "backend"
    start.mkdir(parents=True)
    result = vs._find_git_root(str(start))
    assert result == str(repo)


def test_find_git_root_returns_none_at_fs_root():
    # Walking up from / on most systems will reach the root without
    # finding .git. We just assert it returns None (no crash).
    result = vs._find_git_root("/")
    assert result is None
