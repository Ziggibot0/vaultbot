from __future__ import annotations

import json
import subprocess

import pytest
from workspace import WorkspaceError, WorkspaceRegistry, _parse_github_remote

pytestmark = pytest.mark.unit


def _git(root, *args):
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def _repo(tmp_path, name="project", remote="https://github.com/acme/widget.git"):
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-b", "trunk")
    _git(root, "remote", "add", "origin", remote)
    return root


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/acme/widget.git", ("acme", "widget")),
        ("git@github.com:acme/widget.git", ("acme", "widget")),
        ("https://gitlab.com/acme/widget.git", None),
    ],
)
def test_parse_github_remote(remote, expected):
    assert _parse_github_remote(remote) == expected


def test_select_persists_and_reloads(tmp_path):
    state = tmp_path / "state.json"
    root = _repo(tmp_path)
    selected = WorkspaceRegistry(state).select(root)

    assert selected.local_root == str(root.resolve())
    assert (selected.owner, selected.repository) == ("acme", "widget")
    assert selected.default_branch == "trunk"
    assert WorkspaceRegistry(state).get() == selected


def test_upstream_remote_defines_target_identity(tmp_path):
    state = tmp_path / "state.json"
    root = _repo(tmp_path, remote="https://github.com/forker/widget.git")
    _git(root, "remote", "add", "upstream", "git@github.com:acme/widget.git")

    selected = WorkspaceRegistry(state).select(root)

    assert (selected.owner, selected.repository) == ("acme", "widget")
    assert selected.origin_url == "https://github.com/forker/widget.git"


def test_switch_replaces_the_complete_descriptor(tmp_path):
    state = tmp_path / "state.json"
    first = _repo(tmp_path, "first", "https://github.com/acme/first.git")
    second = _repo(tmp_path, "second", "https://github.com/acme/second.git")
    registry = WorkspaceRegistry(state)

    registry.select(first)
    registry.select(second)

    assert registry.get().repository == "second"
    assert registry.get().local_root == str(second.resolve())


def test_project_path_uses_selected_workspace_exclusively(tmp_path):
    state = tmp_path / "state.json"
    first = _repo(tmp_path, "first", "https://github.com/acme/first.git")
    second = _repo(tmp_path, "second", "https://github.com/acme/second.git")
    (first / "README.md").write_text("first", encoding="utf-8")
    (second / "README.md").write_text("second", encoding="utf-8")
    registry = WorkspaceRegistry(state)
    registry.select(second)

    resolved = registry.resolve_project_path("README.md")

    assert resolved == second / "README.md"


def test_project_path_rejects_escape(tmp_path):
    registry = WorkspaceRegistry(tmp_path / "state.json")
    registry.select(_repo(tmp_path))

    assert registry.resolve_project_path("../outside.py", allow_create=True) is None


def test_disconnect_removes_selection(tmp_path):
    state = tmp_path / "state.json"
    registry = WorkspaceRegistry(state)
    registry.select(_repo(tmp_path))

    registry.disconnect()

    assert registry.get() is None


def test_rejects_non_repository(tmp_path):
    with pytest.raises(WorkspaceError, match="Not a Git repository"):
        WorkspaceRegistry(tmp_path / "state.json").select(tmp_path)


def test_invalid_persisted_state_fails_loudly(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"local_root": str(tmp_path)}), encoding="utf-8")

    with pytest.raises(WorkspaceError, match="Workspace state is invalid"):
        WorkspaceRegistry(state).get()
