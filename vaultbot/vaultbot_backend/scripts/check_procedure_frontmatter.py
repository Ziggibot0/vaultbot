#!/usr/bin/env python3
"""Pre-commit hook + CI gate: block procedures with invalid frontmatter.

Scans .md files under vaultbot/System/Procedures/ (and any other .md whose
frontmatter declares ``type: procedure``) and runs the authoritative
``procedure_validator.validate_procedure_text`` on each. Any procedure whose
frontmatter is missing a required field is blocked.

This is the CI enforcement of the same gate that ``Procedure-Creator`` and
``Build-Procedure`` apply at publication time. It closes the gap where a
procedure could be hand-edited (or written by a tool that bypasses the
validator) and ship with incomplete frontmatter, silently breaking RAG
retrieval and subprocess execution.

Required frontmatter fields (from procedure_validator):
    type, description, when_to_use (or when), allowed_tools,
    falsifiable_if, status, model_cartridge, created, summary, tags

Exit 0 = all clear. Exit 1 = blocked, with a list of offending files.

Stdlib only — no dependencies beyond Python 3.11+.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# The script lives at vaultbot/vaultbot_backend/scripts/, so the backend
# package is two levels up. Add it to sys.path so we can import the
# authoritative validator (no duplicated frontmatter logic).
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from procedure_validator import validate_procedure_text  # noqa: E402


def _is_procedure(text: str) -> bool:
    """Return True if the note's frontmatter declares type: procedure."""
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    fm = text[3:end]
    for line in fm.split("\n"):
        line = line.strip()
        if line.startswith("type:"):
            value = line.split(":", 1)[1].strip().strip('"').strip("'").lower()
            return value == "procedure"
    return False


def _frontmatter_errors(text: str) -> list[str]:
    """Return the frontmatter-only errors from the authoritative validator."""
    result = validate_procedure_text(text)
    return [e for e in result.get("errors", []) if e.startswith("Frontmatter")]


def main() -> int:
    # --all mode: scan every committed .md file under vaultbot/System/
    # (used by CI, where there is no staging area). Default: scan staged
    # files only (used by the pre-commit hook).
    if "--all" in sys.argv:
        result = subprocess.run(
            ["git", "ls-files", "vaultbot/System/"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            print(
                f"check-procedure-frontmatter: git ls-files failed: {result.stderr}",
                file=sys.stderr,
            )
            return 0  # don't block on git failure
        candidates = [p.strip() for p in result.stdout.split("\n") if p.strip()]
    else:
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            print(
                "check-procedure-frontmatter: could not run git diff --cached",
                file=sys.stderr,
            )
            return 0  # don't block the commit on tool failure

        if result.returncode != 0:
            print(
                f"check-procedure-frontmatter: git diff failed: {result.stderr}",
                file=sys.stderr,
            )
            return 0  # don't block on git failure

        candidates = [p.strip() for p in result.stdout.split("\n") if p.strip()]

    # Filter to .md files under vaultbot/System/.
    SYSTEM_PREFIX = "vaultbot/System/"
    to_check = [
        p for p in candidates if p.startswith(SYSTEM_PREFIX) and p.endswith(".md")
    ]

    if not to_check:
        return 0  # nothing to check

    repo_root = Path.cwd()
    blocked: list[tuple[str, list[str]]] = []
    for rel_path in to_check:
        abs_path = repo_root / rel_path
        if not abs_path.exists():
            continue  # deleted file — let it through
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — can't read = can't verify = block (safe)
            blocked.append((rel_path, ["could not read file"]))
            continue

        if not _is_procedure(text):
            continue  # not a procedure note — skip

        errors = _frontmatter_errors(text)
        if errors:
            blocked.append((rel_path, errors))

    if blocked:
        print("", file=sys.stderr)
        print("=" * 68, file=sys.stderr)
        print("  PROCEDURE FRONTMATTER CHECK FAILED", file=sys.stderr)
        print("=" * 68, file=sys.stderr)
        print("", file=sys.stderr)
        print(
            "The following procedure notes are missing required frontmatter",
            file=sys.stderr,
        )
        print("fields (type, description, when_to_use, allowed_tools,", file=sys.stderr)
        print(
            "falsifiable_if, status, model_cartridge, created, summary, tags):",
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        for rel_path, errors in blocked:
            print(f"  • {rel_path}", file=sys.stderr)
            for err in errors:
                print(f"      - {err}", file=sys.stderr)
        print("", file=sys.stderr)
        print(
            "Add the missing fields to each file's YAML frontmatter. See",
            file=sys.stderr,
        )
        print(
            "[[Procedural-Bootstrap-and-Evolution-Plan]] and [[Procedure-Creator]]",
            file=sys.stderr,
        )
        print("for the full schema.", file=sys.stderr)
        print("", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
