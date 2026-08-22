#!/usr/bin/env python3
"""PowerShell BOM check (HARD GATE).

Windows PowerShell 5.1 mis-parses a ``.ps1`` file that contains non-ASCII
characters (box-drawing ``═``/``─``, em-dashes ``—``, etc.) unless the file
starts with a UTF-8 BOM (``EF BB BF``). Without the BOM, the parser emits
10+ false errors ("Missing closing '}' in statement block", "Unexpected
token") and the installer silently breaks.

The correct invariant is *conditional*, not blanket:

- A ``.ps1`` file that contains non-ASCII characters MUST start with a BOM.
- A pure-ASCII ``.ps1`` file MUST NOT carry a BOM (it's unnecessary and
  some tooling chokes on it).

This script enforces both directions for every committed ``.ps1`` file.
It is a byte-level proxy for the real failure (which only reproduces on a
Windows PowerShell 5.1 host, unavailable in the Linux CI runner), but it
catches the exact regression class: an edit tool stripping the BOM from a
Unicode ``.ps1``, or a BOM being added to an ASCII one.

Exit 0 = all clear. Exit 1 = blocked, with a list of offending files.

Stdlib only — no dependencies beyond Python 3.11+.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_BOM = b"\xef\xbb\xbf"


def _has_non_ascii(data: bytes) -> bool:
    """Return True if the file's bytes contain any non-ASCII character."""
    return any(b > 0x7F for b in data)


def main() -> int:
    result = subprocess.run(
        ["git", "ls-files", "*.ps1"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        print(
            f"check-ps1-bom: git ls-files failed: {result.stderr}",
            file=sys.stderr,
        )
        return 1  # can't verify = block (safe)

    candidates = [p.strip() for p in result.stdout.split("\n") if p.strip()]
    repo_root = Path.cwd()

    missing_bom: list[str] = []
    spurious_bom: list[str] = []
    total = 0

    for rel_path in candidates:
        abs_path = repo_root / rel_path
        if not abs_path.exists():
            continue
        total += 1
        data = abs_path.read_bytes()
        has_bom = data.startswith(_BOM)
        has_non_ascii = _has_non_ascii(data)

        if has_non_ascii and not has_bom:
            missing_bom.append(rel_path)
        elif has_bom and not has_non_ascii:
            spurious_bom.append(rel_path)

    print(f"check-ps1-bom: {total} .ps1 files scanned")
    print(f"  non-ASCII without BOM: {len(missing_bom)}")
    print(f"  ASCII with BOM:        {len(spurious_bom)}")
    if missing_bom:
        print("\n.ps1 files with non-ASCII chars but NO UTF-8 BOM:")
        for p in missing_bom:
            print(f"  - {p}")
    if spurious_bom:
        print("\n.ps1 files that are pure ASCII but carry a BOM:")
        for p in spurious_bom:
            print(f"  - {p}")

    if missing_bom or spurious_bom:
        print(
            "\nPS1 BOM CHECK FAILED: a .ps1 with non-ASCII characters must "
            "start with a UTF-8 BOM (EF BB BF); a pure-ASCII .ps1 must not "
            "carry one.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
