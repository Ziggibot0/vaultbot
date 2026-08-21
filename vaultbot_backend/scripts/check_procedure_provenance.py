#!/usr/bin/env python3
"""Procedure provenance/rationale check (HARD GATE, issues #63 + #64).

Scans every committed .md file under vaultbot/System/Procedures/ and blocks
any procedure that lacks provenance (``sources:`` / ``depends_on:`` /
``## Related`` wikilinks) or a ``## Why This Exists`` rationale section.

This was originally a report-only soft gate while the ~200 pre-existing
procedures were backfilled. The backfill is complete (0 missing provenance,
0 missing rationale), so this is now a hard gate: any new procedure that
ships without provenance or rationale fails CI.

Exit 0 = all clear. Exit 1 = blocked, with a list of offending files.

Stdlib only — no dependencies beyond 3.11+.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# The script lives at vaultbot/vaultbot_backend/scripts/, so the backend
# package is two levels up. Add it to sys.path so we can import the
# authoritative validator (no duplicated provenance logic).
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from procedure_validator import validate_procedure_text  # noqa: E402

_PROVENANCE_RE = re.compile(r"no provenance", re.IGNORECASE)
_RATIONALE_RE = re.compile(r"why this exists", re.IGNORECASE)


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


def main() -> int:
    result = subprocess.run(
        ["git", "ls-files", "System/Procedures/"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        print(
            f"check-procedure-provenance: git ls-files failed: {result.stderr}",
            file=sys.stderr,
        )
        return 1  # can't verify = block (safe)

    candidates = [p.strip() for p in result.stdout.split("\n") if p.strip()]
    to_check = [p for p in candidates if p.endswith(".md")]

    repo_root = Path.cwd()
    no_provenance: list[str] = []
    no_rationale: list[str] = []
    total = 0

    for rel_path in to_check:
        abs_path = repo_root / rel_path
        if not abs_path.exists():
            continue
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — can't read = can't verify = block (safe)
            no_provenance.append(rel_path)
            no_rationale.append(rel_path)
            continue
        if not _is_procedure(text):
            continue
        total += 1
        warnings = validate_procedure_text(text).get("warnings", [])
        if any(_PROVENANCE_RE.search(w) for w in warnings):
            no_provenance.append(rel_path)
        if any(_RATIONALE_RE.search(w) for w in warnings):
            no_rationale.append(rel_path)

    print(f"check-procedure-provenance: {total} procedures scanned")
    print(f"  missing provenance: {len(no_provenance)}")
    print(f"  missing rationale:  {len(no_rationale)}")
    if no_provenance:
        print("\nProcedures with no provenance (sources/depends_on/## Related):")
        for p in no_provenance:
            print(f"  - {p}")
    if no_rationale:
        print("\nProcedures with no '## Why This Exists' section:")
        for p in no_rationale:
            print(f"  - {p}")

    if no_provenance or no_rationale:
        print(
            "\nPROCEDURE PROVENANCE CHECK FAILED: add a '## Why This Exists' "
            "section and provenance (sources:/depends_on:/## Related wikilinks) "
            "to each listed procedure.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
