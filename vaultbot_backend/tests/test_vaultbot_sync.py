"""Unit tests for the vaultbot_sync custom tool.

Covers the pure-logic surface with NO network access and NO real git
operations — git calls are monkeypatched. The tool lands on the target
release with ``reset --hard`` (shallow-clone safe), so these tests assert
it NEVER merges, NEVER requires a clean tree, and NEVER requires a named
branch (installs are detached-HEAD shallow clones).
"""

import pytest

pytestmark = pytest.mark.unit

from custom_tools import vaultbot_sync as vs

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _install(monkeypatch, mapping):
    """Patch _run_git with a dispatch keyed on the exact git argv tuple.

    Unspecified calls default to (True, "", "") — success with empty output.
    Returns the list of argv lists actually invoked, so a test can assert on
    which git commands ran (e.g. that 'merge' and 'push' never did).
    """
    calls: list[list[str]] = []

    def _fake_run_git(git_args, cwd):
        calls.append(list(git_args))
        return mapping.get(tuple(git_args), (True, "", ""))

    monkeypatch.setattr(vs, "_run_git", _fake_run_git)
    return calls


def _make_git_root(monkeypatch, path="/fake/repo"):
    monkeypatch.setattr(vs, "_find_git_root", lambda start_dir: path)


# A minimal healthy latest-tag mapping the individual tests tweak.
def _latest_tag_mapping(pre="abc123", target_sha="def456", tag="v1.5.9"):
    return {
        ("remote",): (True, "origin\nupstream", ""),
        ("branch", "--show-current"): (True, "main", ""),
        ("ls-remote", "--tags", "--refs", "upstream"): (
            True,
            f"111\trefs/tags/v1.5.8\n222\trefs/tags/{tag}",
            "",
        ),
        ("fetch", "--depth", "1", "upstream", f"refs/tags/{tag}"): (True, "", ""),
        ("rev-parse", "HEAD"): (True, pre, ""),
        ("rev-parse", "FETCH_HEAD"): (True, target_sha, ""),
        ("diff", "--name-only", "HEAD"): (True, "", ""),
        ("reset", "--hard", "FETCH_HEAD"): (True, "", ""),
    }


# ─── _latest_semver_tag (pure) ────────────────────────────────────────────────


class TestLatestSemverTag:
    def test_picks_highest(self):
        out = "111\trefs/tags/v1.5.8\n222\trefs/tags/v1.5.10\n333\trefs/tags/v1.5.9\n"
        assert vs._latest_semver_tag(out) == "v1.5.10"

    def test_ignores_non_semver(self):
        out = (
            "111\trefs/tags/nightly\n222\trefs/tags/v1.2.3-rc1\n333\trefs/tags/v0.4.0\n"
        )
        assert vs._latest_semver_tag(out) == "v0.4.0"

    def test_no_tags_returns_none(self):
        assert vs._latest_semver_tag("") is None
        assert vs._latest_semver_tag("111\trefs/tags/nightly\n") is None

    def test_major_minor_ordering(self):
        out = "1\trefs/tags/v2.0.0\n2\trefs/tags/v10.0.0\n3\trefs/tags/v1.99.99\n"
        assert vs._latest_semver_tag(out) == "v10.0.0"


# ─── No git root ──────────────────────────────────────────────────────────────


def test_no_git_root_returns_error(monkeypatch):
    monkeypatch.setattr(vs, "_find_git_root", lambda start_dir: None)
    result = vs.run({})
    assert "error" in result
    assert ".git" in result["error"]


# ─── Remote resolution ────────────────────────────────────────────────────────


def test_no_remote_returns_error(monkeypatch):
    _make_git_root(monkeypatch)
    _install(monkeypatch, {("remote",): (True, "", "")})
    result = vs.run({})
    assert "error" in result
    assert "remote" in result["error"].lower()


def test_falls_back_to_origin_when_no_upstream(monkeypatch):
    _make_git_root(monkeypatch)
    mapping = {
        ("remote",): (True, "origin", ""),
        ("branch", "--show-current"): (True, "main", ""),
        ("ls-remote", "--tags", "--refs", "origin"): (
            True,
            "222\trefs/tags/v1.5.9",
            "",
        ),
        ("fetch", "--depth", "1", "origin", "refs/tags/v1.5.9"): (True, "", ""),
        ("rev-parse", "HEAD"): (True, "abc", ""),
        ("rev-parse", "FETCH_HEAD"): (True, "def", ""),
        ("diff", "--name-only", "HEAD"): (True, "", ""),
        ("reset", "--hard", "FETCH_HEAD"): (True, "", ""),
    }
    calls = _install(monkeypatch, mapping)
    result = vs.run({})
    assert result["status"] == "success"
    # It fetched from origin (no upstream present).
    assert ["ls-remote", "--tags", "--refs", "origin"] in calls


# ─── ls-remote failure ────────────────────────────────────────────────────────


def test_ls_remote_failure_returns_error(monkeypatch):
    _make_git_root(monkeypatch)
    _install(
        monkeypatch,
        {
            ("remote",): (True, "origin\nupstream", ""),
            ("branch", "--show-current"): (True, "main", ""),
            ("ls-remote", "--tags", "--refs", "upstream"): (False, "", "network"),
        },
    )
    result = vs.run({})
    assert "error" in result
    assert "ls-remote" in result["error"]


# ─── Already up to date ───────────────────────────────────────────────────────


def test_already_up_to_date(monkeypatch):
    _make_git_root(monkeypatch)
    # HEAD == FETCH_HEAD → nothing to do.
    _install(monkeypatch, _latest_tag_mapping(pre="same", target_sha="same"))
    result = vs.run({})
    assert result["status"] == "already_up_to_date"
    assert "v1.5.9" in result["target"]


# ─── Successful sync to latest tag ────────────────────────────────────────────


def test_sync_to_latest_tag(monkeypatch):
    _make_git_root(monkeypatch)
    _install(monkeypatch, _latest_tag_mapping())
    result = vs.run({})
    assert result["status"] == "success"
    assert result["merge_ref"] == "v1.5.9"
    assert "v1.5.9" in result["target"]
    assert "stable" in result["target"]
    assert result["previous_head"] == "abc123"


def test_sync_never_merges_or_pushes(monkeypatch):
    """The whole fix: land with reset --hard, never merge (unrecoverable on a
    shallow clone) and never push (irrelevant + fails on detached HEAD)."""
    _make_git_root(monkeypatch)
    calls = _install(monkeypatch, _latest_tag_mapping())
    vs.run({})
    assert ["reset", "--hard", "FETCH_HEAD"] in calls
    assert not any(c and c[0] == "merge" for c in calls), "must not merge"
    assert not any(c and c[0] == "push" for c in calls), "must not push"


# ─── Detached HEAD (every real install) still syncs ───────────────────────────


def test_detached_head_still_syncs(monkeypatch):
    """A `--depth 1 --branch <tag>` install is in detached HEAD, so
    `branch --show-current` is empty. The old code errored here; the new
    code must proceed."""
    _make_git_root(monkeypatch)
    mapping = _latest_tag_mapping()
    mapping[("branch", "--show-current")] = (True, "", "")  # detached
    _install(monkeypatch, mapping)
    result = vs.run({})
    assert result["status"] == "success"
    assert result["current_branch"] == ""


# ─── Dirty tree (untracked bot files) still syncs ─────────────────────────────


def test_dirty_untracked_tree_still_syncs(monkeypatch):
    """Downstream installs always have untracked bot-authored files, so the
    old 'refuse on dirty tree' check blocked every real sync. reset --hard
    preserves untracked files, so we must NOT refuse."""
    _make_git_root(monkeypatch)
    calls = _install(monkeypatch, _latest_tag_mapping())
    result = vs.run({})
    assert result["status"] == "success"
    # The tool must not gate on `status --porcelain` anymore.
    assert not any(c[:2] == ["status", "--porcelain"] for c in calls)


# ─── Backup of modified tracked files ─────────────────────────────────────────


def test_backs_up_modified_tracked_files(monkeypatch, tmp_path):
    (tmp_path / "vaultbot_backend").mkdir()
    tracked = tmp_path / "vaultbot_backend" / "calibration.py"
    tracked.write_text("# locally edited\n", encoding="utf-8")

    monkeypatch.setattr(vs, "_find_git_root", lambda start_dir: str(tmp_path))
    mapping = _latest_tag_mapping()
    mapping[("diff", "--name-only", "HEAD")] = (
        True,
        "vaultbot_backend/calibration.py",
        "",
    )
    _install(monkeypatch, mapping)
    result = vs.run({})
    assert result["status"] == "success"
    assert "vaultbot_backend/calibration.py" in result["backed_up_files"]
    # A backup copy was actually written under .vaultbot-update-backup/.
    backups = list(
        (tmp_path / "vaultbot_backend" / ".vaultbot-update-backup").rglob("*")
    )
    assert any(p.is_file() for p in backups)


# ─── target=main ──────────────────────────────────────────────────────────────


def test_sync_to_main_explicit(monkeypatch):
    _make_git_root(monkeypatch)
    mapping = {
        ("remote",): (True, "origin\nupstream", ""),
        ("branch", "--show-current"): (True, "main", ""),
        ("fetch", "--depth", "1", "upstream", "main"): (True, "", ""),
        ("rev-parse", "HEAD"): (True, "abc", ""),
        ("rev-parse", "FETCH_HEAD"): (True, "def", ""),
        ("diff", "--name-only", "HEAD"): (True, "", ""),
        ("reset", "--hard", "FETCH_HEAD"): (True, "", ""),
    }
    calls = _install(monkeypatch, mapping)
    result = vs.run({"target": "main"})
    assert result["status"] == "success"
    assert result["merge_ref"] == "upstream/main"
    assert "bleeding edge" in result["target"]
    # target=main must NOT do a tag lookup.
    assert not any(c[:1] == ["ls-remote"] for c in calls)


# ─── No tags — falls back to main ─────────────────────────────────────────────


def test_no_tags_falls_back_to_main(monkeypatch):
    _make_git_root(monkeypatch)
    mapping = {
        ("remote",): (True, "origin\nupstream", ""),
        ("branch", "--show-current"): (True, "main", ""),
        ("ls-remote", "--tags", "--refs", "upstream"): (
            True,
            "111\trefs/tags/nightly",  # no semver tag
            "",
        ),
        ("fetch", "--depth", "1", "upstream", "main"): (True, "", ""),
        ("rev-parse", "HEAD"): (True, "abc", ""),
        ("rev-parse", "FETCH_HEAD"): (True, "def", ""),
        ("diff", "--name-only", "HEAD"): (True, "", ""),
        ("reset", "--hard", "FETCH_HEAD"): (True, "", ""),
    }
    _install(monkeypatch, mapping)
    result = vs.run({})
    assert result["status"] == "success"
    assert result["merge_ref"] == "upstream/main"
    assert "no tags" in result["target"]


# ─── reset --hard failure surfaces loudly ─────────────────────────────────────


def test_reset_failure_returns_error(monkeypatch):
    _make_git_root(monkeypatch)
    mapping = _latest_tag_mapping()
    mapping[("reset", "--hard", "FETCH_HEAD")] = (False, "", "reset boom")
    _install(monkeypatch, mapping)
    result = vs.run({})
    assert "error" in result
    assert "reset" in result["error"].lower()


# ─── _find_git_root (real, no monkeypatch) ────────────────────────────────────


def test_find_git_root_walks_up(tmp_path):
    repo = tmp_path / "myrepo"
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True)
    start = repo / "vaultbot" / "backend"
    start.mkdir(parents=True)
    result = vs._find_git_root(str(start))
    assert result == str(repo)


def test_find_git_root_returns_none_at_fs_root():
    result = vs._find_git_root("/")
    assert result is None
