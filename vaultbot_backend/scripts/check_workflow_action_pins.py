#!/usr/bin/env python3
"""Fail CI when workflow actions are not pinned to immutable SHAs.

This enforces the supply-chain hardening rule from issue #330:
third-party GitHub Actions in .github/workflows/ must use full commit SHAs,
not floating tags.

Exit codes:
  0: all action references are pinned
  1: one or more workflow action references are not pinned
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
_USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _is_exempt_action(ref: str) -> bool:
    """Return True for local or docker actions that are not tag-pinned refs."""
    return ref.startswith("./") or ref.startswith("docker://")


def _check_file(path: Path) -> list[str]:
    """Return a list of violations in a workflow file."""
    violations: list[str] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line_no, line in enumerate(lines, start=1):
        match = _USES_RE.match(line)
        if not match:
            continue
        ref = match.group(1).strip()
        if _is_exempt_action(ref):
            continue
        if "@" not in ref:
            violations.append(
                f"{path.as_posix()}:{line_no}: uses ref has no '@': {ref}"
            )
            continue
        _action, version = ref.rsplit("@", 1)
        if not _SHA_RE.fullmatch(version.lower()):
            violations.append(
                f"{path.as_posix()}:{line_no}: action is not SHA-pinned: {ref}"
            )
    return violations


def main() -> int:
    if not _WORKFLOWS_DIR.is_dir():
        print(
            f"workflow pin check: missing directory {_WORKFLOWS_DIR}",
            file=sys.stderr,
        )
        return 1

    all_violations: list[str] = []
    for workflow in sorted(_WORKFLOWS_DIR.glob("*.yml")):
        all_violations.extend(_check_file(workflow))

    if all_violations:
        print("WORKFLOW ACTION PINNING CHECK FAILED:", file=sys.stderr)
        for violation in all_violations:
            print(f"  - {violation}", file=sys.stderr)
        print(
            "\nPin every third-party action to a full 40-char commit SHA.",
            file=sys.stderr,
        )
        return 1

    print("workflow-action-pins: all workflow actions are SHA-pinned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
