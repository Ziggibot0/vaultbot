"""Persisted active development workspace.

Project tools use this registry as the single source of truth for both the
local Git root and its GitHub identity. The Obsidian vault and VaultBot's
installation remain separate unless the user explicitly selects them.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from paths import FRAMEWORK_ROOT


class WorkspaceError(ValueError):
    """Raised when a workspace cannot be selected or loaded safely."""


@dataclass(frozen=True)
class WorkspaceDescriptor:
    workspace_id: str
    local_root: str
    owner: str
    repository: str
    origin_url: str
    upstream_url: str
    default_branch: str
    managed_clone: bool = False

    def to_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def _parse_github_remote(remote_url: str) -> tuple[str, str] | None:
    value = remote_url.strip()
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        parsed = urlparse(value)
        if parsed.hostname != "github.com":
            return None
        path = parsed.path.lstrip("/")
    parts = path.removesuffix(".git").split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


class WorkspaceRegistry:
    """Validate, persist, and expose one active Git workspace."""

    def __init__(self, state_path: Path | None = None) -> None:
        configured_path = os.getenv("VAULTBOT_WORKSPACE_STATE", "").strip()
        self._state_path = state_path or (
            Path(configured_path)
            if configured_path
            else FRAMEWORK_ROOT / ".vaultbot-workspace.json"
        )
        self._lock = threading.RLock()

    def get(self) -> WorkspaceDescriptor | None:
        with self._lock:
            if not self._state_path.exists():
                return None
            try:
                payload = json.loads(self._state_path.read_text(encoding="utf-8"))
                descriptor = WorkspaceDescriptor(**payload)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise WorkspaceError(f"Workspace state is invalid: {exc}") from exc
            root = Path(descriptor.local_root)
            if not root.is_dir() or not (root / ".git").exists():
                raise WorkspaceError(
                    f"Selected workspace is no longer a Git repository: {root}"
                )
            return descriptor

    def select(
        self, local_root: str | Path, *, managed_clone: bool = False
    ) -> WorkspaceDescriptor:
        requested_root = os.path.abspath(os.path.expanduser(os.fspath(local_root)))
        try:
            result = subprocess.run(
                ["git", "-C", requested_root, "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceError("Could not validate the Git repository") from exc
        if result.returncode != 0:
            raise WorkspaceError("Not a Git repository")

        root = Path(result.stdout.strip()).resolve()
        if os.path.normcase(str(root)) != os.path.normcase(requested_root):
            raise WorkspaceError("Select the Git repository's top-level folder")

        origin_url = self._git(root, "remote", "get-url", "origin")
        upstream_url = self._git(root, "remote", "get-url", "upstream", required=False)
        identity = _parse_github_remote(upstream_url or origin_url)
        if identity is None:
            raise WorkspaceError(
                "The selected repository needs a GitHub origin or upstream remote."
            )
        owner, repository = identity
        default_branch = self._default_branch(root)
        workspace_id = hashlib.sha256(str(root).casefold().encode()).hexdigest()[:16]
        descriptor = WorkspaceDescriptor(
            workspace_id=workspace_id,
            local_root=str(root),
            owner=owner,
            repository=repository,
            origin_url=origin_url,
            upstream_url=upstream_url,
            default_branch=default_branch,
            managed_clone=managed_clone,
        )
        self._save(descriptor)
        return descriptor

    def disconnect(self) -> None:
        with self._lock:
            try:
                self._state_path.unlink(missing_ok=True)
            except OSError as exc:
                raise WorkspaceError(f"Could not clear workspace state: {exc}") from exc

    def resolve_project_path(
        self, file_path: str | Path, *, allow_create: bool = False
    ) -> Path | None:
        """Resolve a project-relative path inside the selected workspace only."""
        descriptor = self.get()
        if descriptor is None:
            return None
        root = Path(descriptor.local_root).resolve()
        candidate = (root / file_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        if not allow_create and not candidate.exists():
            return None
        return candidate

    def _save(self, descriptor: WorkspaceDescriptor) -> None:
        with self._lock:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
            try:
                temporary.write_text(
                    json.dumps(descriptor.to_dict(), indent=2) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(self._state_path)
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise WorkspaceError(
                    f"Could not persist workspace state: {exc}"
                ) from exc

    @staticmethod
    def _git(root: Path, *args: str, required: bool = True) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceError(f"Git command failed: {exc}") from exc
        if result.returncode != 0:
            if not required:
                return ""
            detail = result.stderr.strip() or result.stdout.strip()
            raise WorkspaceError(f"Git {' '.join(args)} failed: {detail}")
        return result.stdout.strip()

    def _default_branch(self, root: Path) -> str:
        symbolic = self._git(
            root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", required=False
        )
        if symbolic.startswith("origin/"):
            return symbolic.removeprefix("origin/")
        current = self._git(root, "branch", "--show-current", required=False)
        return current or "main"


workspace_registry = WorkspaceRegistry()
