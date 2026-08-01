"""
Vault write guard — protects sacred/locked notes from LLM edits.

Two rules the user can rely on, enforced at the write boundary so NO
LLM-driven path (note_creator, graph_ops, vault_maintenance, autonomous
researcher, custom tools) can violate them:

  1. Date-only notes are SACRED. A file whose stem is just a date
     (2026-07-25, 2026-7-5, 07-25-2026, 2026/07/25) is the user's personal
     journal space. The LLM must never create, edit, append to, or delete
     these. They are for the user to pour their thoughts into.

  2. LOCKED signal. Any note containing the marker `LOCKED` (on its own
     line, case-insensitive, as a pragma) is read-only to the LLM. The user
     adds `LOCKED` to a document to freeze it; the LLM can still READ it for
     context but cannot modify it. Removing the marker (by the user, in
     Obsidian) unfreezes it.

The guard is a single function `assert_writable(path)` called by every
write path. It raises `VaultWriteForbidden` if the target is sacred/locked,
so the caller can catch it and return a clean error to the LLM instead of
silently failing. There's also `is_writable(path) -> bool` for callers that
want a boolean check.

Date detection is deliberately broad (multiple formats, with/without
leading zeros, dashes/slashes/dots) so the user can name their journal
however they like and it's still protected.
"""

from __future__ import annotations

import re
from pathlib import Path


class VaultWriteForbidden(Exception):
    """Raised when an LLM-driven write targets a sacred/locked note.

    Carries the reason so the caller can surface it to the LLM/user.
    """

    def __init__(self, path: Path, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"write forbidden for {path.name}: {reason}")


# The LOCKED pragma. Matched on its own line, case-insensitive, allowing a
# leading # or <!-- comment marker so it can sit naturally in markdown.
# Examples that trigger: "LOCKED", "# LOCKED", "<!-- LOCKED -->".
_LOCKED_RE = re.compile(r"^\s*(?:<!--\s*)?(?:#\s*)?LOCKED(?:\s*-->)?\s*$",
                        re.IGNORECASE | re.MULTILINE)

# A date-only stem. Matches common date formats as the ENTIRE filename
# (excluding extension), with optional leading zeros:
#   2026-07-25, 2026-7-5, 07-25-2026, 25-07-2026, 2026/07/25, 2026.07.25,
#   07-25-26, 2026-07. Also matches a bare 4-digit year only if it looks
#   like a journal year file? No — a bare year is too broad (could be a
#   topic). Require at least month + day granularity.
_DATE_STEM_RE = re.compile(
    r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$"   # M-D-Y or Y-M-D or Y-M-D with seps
    , re.IGNORECASE,
)


def is_date_only_stem(stem: str) -> bool:
    """True if the filename stem is just a date (sacred journal space)."""
    if not stem:
        return False
    s = stem.strip()
    # The regex covers Y-M-D, M-D-Y, D-M-Y with - / . separators.
    if _DATE_STEM_RE.match(s):
        # Sanity: at least one component must be >= 4 digits (a year) OR
        # the first component is >= 4 digits, to avoid matching things like
        # "1-2-3". A real date has a 4-digit year somewhere, or is a
        # month-day-year with a 2-digit year. Accept any that has a
        # 4-digit component (year) — the common journal case.
        parts = re.split(r"[-/.]", s)
        if any(len(p) == 4 and p.isdigit() for p in parts):
            return True
        # Also accept 2-digit-year forms like 07-25-26 only if all parts are
        # numeric and at least one is a plausible month/day (1-31). This is
        # permissive on purpose — the cost of a false positive (protecting
        # a non-journal file) is low; the cost of a false negative (editing
        # the user's journal) is high.
        if all(p.isdigit() for p in parts) and len(parts) == 3:
            return True
    return False


def is_locked(path: Path) -> bool:
    """True if the note contains a LOCKED pragma (read-only to the LLM)."""
    try:
        if not path.exists() or not path.is_file():
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
        return bool(_LOCKED_RE.search(text))
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return False


def is_writable(path: Path) -> bool:
    """Check whether the LLM is allowed to write to this path.

    Returns False for date-only journal files and LOCKED notes. Returns
    True for everything else (including non-existent files the LLM may
    create, unless their stem is a date).
    """
    try:
        p = Path(path)
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return False
    # Rule 1: date-only stems are sacred (applies to create AND edit).
    if is_date_only_stem(p.stem):
        return False
    # Rule 2: existing files with a LOCKED pragma are read-only.
    if p.exists() and is_locked(p):
        return False
    return True


def assert_writable(path: Path) -> None:
    """Raise VaultWriteForbidden if the LLM may not write to this path.

    Call this at the top of every LLM-driven write path. The exception
    carries a human-readable reason so the caller can return it to the LLM.
    """
    p = Path(path)
    if is_date_only_stem(p.stem):
        raise VaultWriteForbidden(
            p, "date-only notes are the user's sacred journal space; "
               "the LLM may not create or edit them.")
    if p.exists() and is_locked(p):
        raise VaultWriteForbidden(
            p, "this note is LOCKED (read-only to the LLM). "
               "The user must remove the LOCKED marker to allow edits.")


def writable_check(path: Path) -> str | None:
    """Soft check returning a reason string if unwritable, else None.

    For callers that prefer a None/error-string pattern over exceptions.
    """
    try:
        assert_writable(path)
        return None
    except VaultWriteForbidden as e:
        return e.reason
