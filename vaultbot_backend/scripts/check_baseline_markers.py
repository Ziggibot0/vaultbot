#!/usr/bin/env python3
"""Pre-commit hook: block commits of non-baseline System/ .md files.

Scans staged .md files under vaultbot-stuff/System/ and verifies each has
``baseline: true`` in its YAML frontmatter. Files without it are blocked
from being committed — they're personal, not shippable.

Backend .py files and root-level .md files are NOT checked (all backend
code is baseline by default; root directives are gitignored separately).

Exit 0 = all clear. Exit 1 = blocked, with a list of offending files.

Stdlib only — no dependencies beyond Python 3.11+.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse flat YAML frontmatter from markdown text.

    Returns a dict of key -> value. Handles flat key-value pairs and
    simple list values (``- item``). Does not support nested mappings.
    This is a standalone copy of procedure_compiler._parse_frontmatter
    so the hook has zero imports from the backend.
    """
    if not text.startswith("---"):
        return {}

    end = text.find("\n---", 3)
    if end == -1:
        return {}

    fm_str = text[3:end].strip()
    fm: dict[str, str] = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    for line in fm_str.split("\n"):
        line = line.rstrip()
        if not line:
            continue

        # List item: "  - value"
        if line.startswith("  - ") and current_key:
            value = line[4:].strip().strip('"').strip("'")
            if current_list is None:
                current_list = []
                fm[current_key] = current_list  # type: ignore[assignment]
            current_list.append(value)
            continue

        # Key-value pair: "key: value"
        if ":" in line:
            current_list = None
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            if value:
                fm[key] = value
                current_key = key
            else:
                current_key = key
                current_list = None

    return fm


def has_baseline_marker(file_path: Path) -> bool:
    """Return True if the file's frontmatter contains baseline: true."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — can't read = can't verify = block (safe); pre-commit hooks must not crash
        return False

    fm = parse_frontmatter(text)
    baseline = fm.get("baseline", "").strip().lower()
    return baseline == "true"


def main() -> int:
    # --all mode: scan every committed .md file under vaultbot-stuff/System/
    # (used by CI, where there is no staging area). Default: scan staged
    # files only (used by the pre-commit hook).
    if "--all" in sys.argv:
        result = subprocess.run(
            ["git", "ls-files", "vault/vaultbot-stuff/System/"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            print(
                f"check-baseline-markers: git ls-files failed: {result.stderr}",
                file=sys.stderr,
            )
            return 0  # don't block on git failure
        staged = [p.strip() for p in result.stdout.split("\n") if p.strip()]
    else:
        # Get staged files.
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            print(
                "check-baseline-markers: could not run git diff --cached",
                file=sys.stderr,
            )
            return 0  # don't block the commit on tool failure

        if result.returncode != 0:
            print(
                f"check-baseline-markers: git diff failed: {result.stderr}",
                file=sys.stderr,
            )
            return 0  # don't block on git failure

        staged = [p.strip() for p in result.stdout.split("\n") if p.strip()]

    # Filter to .md files under vault/vaultbot-stuff/System/.
    SYSTEM_PREFIX = "vault/vaultbot-stuff/System/"
    to_check = [p for p in staged if p.startswith(SYSTEM_PREFIX) and p.endswith(".md")]

    if not to_check:
        return 0  # nothing to check

    # Check each file.
    repo_root = Path.cwd()
    blocked: list[str] = []
    for rel_path in to_check:
        abs_path = repo_root / rel_path
        if not abs_path.exists():
            continue  # deleted file — let it through
        if not has_baseline_marker(abs_path):
            blocked.append(rel_path)

    if blocked:
        print("", file=sys.stderr)
        print("=" * 68, file=sys.stderr)
        print("  BASELINE MARKER CHECK FAILED", file=sys.stderr)
        print("=" * 68, file=sys.stderr)
        print("", file=sys.stderr)
        print(
            "The following files under vaultbot-stuff/System/ are missing",
            file=sys.stderr,
        )
        print(
            'the "baseline: true" frontmatter field:',
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        for f in blocked:
            print(f"  • {f}", file=sys.stderr)
        print("", file=sys.stderr)
        print(
            "Every committed file under vaultbot-stuff/System/ must be baseline:",
            file=sys.stderr,
        )
        print(
            "true — curated, reviewed, universal content that ships to every",
            file=sys.stderr,
        )
        print(
            "user. There is no baseline: false in the committed tree.",
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        print(
            "If this file IS baseline (curated, reviewed, universal), add to",
            file=sys.stderr,
        )
        print("its YAML frontmatter:", file=sys.stderr)
        print("", file=sys.stderr)
        print("  baseline: true", file=sys.stderr)
        print("", file=sys.stderr)
        print(
            "If this file is emergent/personal (e.g. Dream-Pass output, a",
            file=sys.stderr,
        )
        print(
            "procedure learned from your own usage), it must NOT ship. Move it",
            file=sys.stderr,
        )
        print(
            "outside vaultbot-stuff/System/ "
            "(e.g. vaultbot-stuff/Memory/Procedures/) or",
            file=sys.stderr,
        )
        print(
            "add it to .gitignore. Promoting emergent content to baseline is",
            file=sys.stderr,
        )
        print(
            "a deliberate, reviewed act — not a side effect of a commit.",
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        print(
            'See CONTRIBUTING.md → "Baseline vs. emergent content" for the',
            file=sys.stderr,
        )
        print("full policy.", file=sys.stderr)
        print("", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
