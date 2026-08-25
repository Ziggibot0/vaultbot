"""CI regression test: procedure notes must not reference stale vault layout paths.

The `vaultbot/` → `vaultbot-stuff/` rename (and the two-root layout where
backend source lives under FRAMEWORK_ROOT, not the vault) stranded ~72
procedure notes whose code blocks resolved against directories that no
longer exist — every one silently failed. Session 0000f516 showed the blast
radius: Small-Model-Route found 0 of 219 procedures because it globbed
`vault_path / "vaultbot" / "System" / "Procedures"`.

This test AST-parses every code block in every procedure note and fails if
any code block:
  - joins a path through the literal string "vaultbot" (the removed folder),
  - uses the literal string "FRAMEWORK_ROOT" as a path segment (it is an
    injected *variable*, not a folder name),
  - references `vaultbot/System`, `vaultbot/Knowledge`, `vaultbot/Memory`,
    or `vaultbot/baseline` string paths.

It also scans prose for the stale `vaultbot/System/` write-target strings
that tell the model to publish procedures into a directory that no longer
exists. A new stale path anywhere fails CI, so a future layout rename can't
silently strand procedures again.
"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.unit

from paths import VAULT_ROOT

_PROC_DIR = VAULT_ROOT / "vaultbot-stuff" / "System" / "Procedures"

# Stale path segments that must not appear anywhere in a procedure note.
# "vaultbot/System" etc. are the pre-rename vault-relative paths.
_STALE_PROSE_STRINGS = (
    "vaultbot/System/",
    "vaultbot/Knowledge/",
    "vaultbot/Memory/",
    "vaultbot/baseline/",
)

# Code-join anti-patterns (checked inside ```python blocks only):
#   ... / "vaultbot" / ...          -> the removed folder as a join segment
#   ... / "FRAMEWORK_ROOT" / ...    -> FRAMEWORK_ROOT is a variable, not a string
_STALE_CODE_PATTERNS = (
    re.compile(r'/\s*"vaultbot"\s*/'),
    re.compile(r'"FRAMEWORK_ROOT"\s*/'),
)


def _python_blocks(text: str) -> list[str]:
    """Extract fenced ```python blocks from a markdown note."""
    blocks: list[str] = []
    for m in re.finditer(r"```python\s*\n(.*?)```", text, re.DOTALL):
        blocks.append(m.group(1))
    return blocks


def _procedure_notes() -> list:
    if not _PROC_DIR.is_dir():
        return []
    return sorted(_PROC_DIR.glob("*.md"))


def test_procedure_dir_exists():
    assert _PROC_DIR.is_dir(), f"procedures dir missing: {_PROC_DIR}"
    assert _procedure_notes(), "no procedure notes found"


@pytest.mark.parametrize("note", _procedure_notes(), ids=lambda p: p.stem)
def test_no_stale_vault_paths(note):
    text = note.read_text(encoding="utf-8", errors="replace")
    failures: list[str] = []

    # Prose-level stale path strings (any context — they mislead the model
    # about where things live even outside code blocks).
    for s in _STALE_PROSE_STRINGS:
        if s in text:
            failures.append(f"stale prose path '{s}'")

    # Code-block anti-patterns.
    for i, block in enumerate(_python_blocks(text), 1):
        for pat in _STALE_CODE_PATTERNS:
            m = pat.search(block)
            if m:
                line = block[: m.start()].count("\n") + 1
                failures.append(f"code block {i} line {line}: {m.group(0).strip()}")

    assert not failures, (
        f"{note.name} references the pre-rename vault layout:\n  "
        + "\n  ".join(failures)
    )
