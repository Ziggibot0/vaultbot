"""Verify the curated pip fallback matches the uv runtime lock."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = ROOT / "vaultbot_backend" / "requirements.txt"
ALLOWED_EXTRAS = {"pytest"}


def _parse_requirements(text: str) -> dict[str, set[tuple[str, str]]]:
    parsed: dict[str, set[tuple[str, str]]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "-e ")):
            continue
        requirement = Requirement(line)
        key = canonicalize_name(requirement.name)
        pin = (str(requirement.specifier), str(requirement.marker or ""))
        parsed.setdefault(key, set()).add(pin)
    return parsed


def main() -> int:
    exported = subprocess.run(
        ["uv", "export", "--format", "requirements-txt", "--no-dev", "--no-hashes"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    expected = _parse_requirements(exported)
    actual = _parse_requirements(REQUIREMENTS.read_text(encoding="utf-8"))

    missing = sorted(set(expected) - set(actual))
    stale = sorted(set(actual) - set(expected) - ALLOWED_EXTRAS)
    mismatched = sorted(
        name
        for name in expected.keys() & actual.keys()
        if expected[name] != actual[name]
    )
    if not (missing or stale or mismatched):
        print("requirements.txt matches uv.lock")
        return 0

    if missing:
        print(f"Missing from requirements.txt: {', '.join(missing)}")
    if stale:
        print(f"Not present in uv runtime lock: {', '.join(stale)}")
    for name in mismatched:
        print(
            f"Pin mismatch for {name}: expected {expected[name]}, found {actual[name]}"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
