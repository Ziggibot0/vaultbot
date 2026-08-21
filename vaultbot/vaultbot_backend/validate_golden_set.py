"""Validate golden_set.json against the committed vault notes.

Pre-flight for the golden-gate CI workflow: confirms every ``expected_notes``
stem and every graph entry's ``seed_notes`` stem resolves to a note that is
actually committed to the repo (via ``git ls-files``). A phantom expected note
or a gitignored graph seed would otherwise score 0 recall silently in CI.

Run it:
    python validate_golden_set.py                 # validate against git ls-files
    python validate_golden_set.py --golden path   # custom golden-set path

Exit code 0 = valid, 1 = problems found, 2 = could not enumerate notes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Make leaf modules importable when run as a script from the backend dir.
_BACKEND = Path(__file__).parent.resolve()
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from golden_eval import load_golden_set, validate_golden_set  # noqa: E402


def _committed_note_stems(repo_root: Path) -> set[str]:
    """Return the set of committed .md note identifiers via ``git ls-files``.

    Falls back to an empty set (with a warning) if git is unavailable, so the
    validator degrades to ``checked=False`` rather than crashing.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.md"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"WARNING: could not enumerate committed notes via git: {e}")
        return set()
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate golden_set.json integrity.")
    ap.add_argument(
        "--golden",
        default=str(_BACKEND / "golden_set.json"),
        help="path to golden_set.json (default: alongside this script)",
    )
    ap.add_argument(
        "--repo-root",
        default=str(_BACKEND.parent.parent),
        help="repo root for `git ls-files` (default: two levels up from backend)",
    )
    args = ap.parse_args()

    golden = load_golden_set(args.golden)
    if not golden:
        print("VALIDATION ERROR: golden set is empty or unreadable.")
        return 2

    stems = _committed_note_stems(Path(args.repo_root))
    result = validate_golden_set(golden_set=golden, available_stems=stems)

    if not result["checked"]:
        print("VALIDATION SKIPPED: no committed-note list available.")
        return 2

    if result["valid"]:
        print(f"Golden set valid: {len(golden)} entries, all notes committed.")
        return 0

    print(f"Golden set INVALID: {len(result['problems'])} problem(s):")
    for p in result["problems"]:
        print(f"  - {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
