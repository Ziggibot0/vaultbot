#!/usr/bin/env python3
"""Thinness ratchet: fail CI if inline backend .py logic grows past baseline.

Measures the amount of inline Python logic in vaultbot_backend/ (excluding
custom_tools/, tests/, scripts/, __pycache__/) and fails CI if it EXCEEDS the
committed baseline in .ci-baseline.json. This is the enforcement mechanism for
the "thin backend" goal (issue #150): logic should migrate out of inline .py
modules into procedures / custom_tools / a thin interpreter, and this ratchet
makes that migration monotonic — the count can only go down (or stay flat) as
we thin.

Metric: non-blank, non-comment lines (SLOC). A line is counted if, after
strip(), it is non-empty and does not start with '#'.

Exit 0 = within baseline. Exit 1 = grew (or baseline missing).

Stdlib only — no dependencies beyond Python 3.11+.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The script lives at vaultbot_backend/scripts/, so the repo root (where
# .ci-baseline.json lives) is two levels up.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BASELINE_PATH = _REPO_ROOT / ".ci-baseline.json"
_BACKEND_DIR = _REPO_ROOT / "vaultbot_backend"

# Directories excluded from the count. custom_tools/ is the sanctioned home
# for bespoke/emergent tooling; tests/ and scripts/ are not runtime logic;
# __pycache__ and .pytest_cache are build artifacts.
_EXCLUDED_DIRS = {"custom_tools", "tests", "scripts", "__pycache__", ".pytest_cache"}


def _count_sloc() -> int:
    """Return the non-blank, non-comment line count of backend .py files."""
    total = 0
    for path in _BACKEND_DIR.rglob("*.py"):
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # unreadable file — skip rather than crash
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            total += 1
    return total


def _load_baseline() -> dict:
    if not _BASELINE_PATH.exists():
        print(
            f"thinness-ratchet: baseline file not found: {_BASELINE_PATH}",
            file=sys.stderr,
        )
        return {}
    return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))


def main() -> int:
    baseline = _load_baseline()
    if not baseline:
        return 1

    current = _count_sloc()
    base = baseline.get("thinness", {}).get("sloc")
    if base is None:
        print(
            "thinness-ratchet: no 'thinness.sloc' key in .ci-baseline.json",
            file=sys.stderr,
        )
        return 1

    print(f"thinness-ratchet: {current} SLOC (baseline {base})")

    if current > base:
        print("\nTHINNESS RATCHET FAILED:", file=sys.stderr)
        print(
            f"  - inline backend logic grew: {current} > baseline {base} "
            f"(+{current - base} lines)",
            file=sys.stderr,
        )
        print(
            "\nTo thin the backend, move inline logic into procedures, "
            "custom_tools/, or a thin interpreter, then lower the baseline in "
            ".ci-baseline.json in the same PR. To accept new inline logic "
            "(discouraged), raise the baseline in the same PR.",
            file=sys.stderr,
        )
        return 1

    print("thinness-ratchet: within baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
