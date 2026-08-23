"""Build a {stem: {first_tools, triggers, description, status}} index by
compiling each procedure note once (parse-only — no LLM, no execution).

The procedure suggestion gate (``procedure_suggestion_gate.py``) uses this to
nudge the chat model toward ``execute_procedure("X")`` when the model reaches
for a raw tool that a procedure's *first step* already calls.  Without this
nudge the model improvises the procedure's logic by hand (see session
``eb8143f7``: ``Git-Sync-Upstream`` exists and wraps ``vaultbot_sync`` with
stash/unstash, but the model called ``code_run`` + ``vaultbot_sync`` raw ~10
times instead of one ``execute_procedure`` call).

The index is cheap to build (a single parse pass over every procedure note,
the same work ``procedure_tracker.get_procedure_index`` already does for the
stem map) and is cached on ``Services`` at startup.  It is rebuilt on demand
when a procedure is created/edited mid-session (the gate falls back to a
refresh-on-miss, mirroring ``dispatch_procedure_core``'s stem-index pattern).

See:
  - ``procedure_suggestion_gate.py`` — the gate that consumes this index.
  - ``procedure_compiler.compile_procedure`` — the parse used here.
  - [[Session-eb8143f7]] — the loop that motivated this module.
"""

from __future__ import annotations

import re
from typing import Any

from procedure_compiler import compile_procedure

# ── Tool-call patterns inside procedure code steps ──────────────────────
# Procedure code steps call tools in a handful of recognisable shapes.
# We scan step 1's code for these and collect every tool name found. The
# gate matches when the model's ``tool_name`` is in this set.
#
#   from custom_tools.<tool> import run as _<x>   ->  "<tool>"
#   from custom_tools import <tool>              ->  "<tool>"
#   _x = <tool>(...)                             ->  "<tool>"  (bare call)
#   run_procedure("Name")                        ->  "run_procedure"
#   dispatch DSL "call: <tool>"                  ->  "<tool>"  (via comment)
#
# ``code_run`` is special: a procedure that shells out via subprocess is
# treated as calling ``code_run`` (the model's escape hatch for the same
# shell work).  We also surface ``subprocess`` presence as ``code_run`` so a
# model reaching for ``code_run`` is nudged toward the procedure that does
# the same shell work deterministically.
_CUSTOM_TOOLS_IMPORT_RE = re.compile(
    r"from\s+custom_tools\.([a-zA-Z_][a-zA-Z0-9_]*)\s+import\s+run\s+as\s+\w+"
)
_CUSTOM_TOOLS_IMPORT_BARE_RE = re.compile(
    r"from\s+custom_tools\s+import\s+([a-zA-Z_][a-zA-Z0-9_]*)"
)
_DISPATCH_CALL_COMMENT_RE = re.compile(
    r"#\s*Dispatch:\s+call\s+([a-zA-Z_][a-zA-Z0-9_]*)"
)
_RUN_PROCEDURE_RE = re.compile(r"run_procedure\s*\(")
_SUBPROCESS_RE = re.compile(r"\bsubprocess\b")
_VAULT_SEARCH_RE = re.compile(r"\bvault_search\s*\(")
_VAULT_READ_NOTE_RE = re.compile(r"\bvault_read_note\s*\(")
_VAULTBOT_SYNC_RE = re.compile(r"\bvaultbot_sync\b")
_GITHUB_ISSUES_RE = re.compile(r"\bgithub_issues\b")


def _extract_first_tools(code: str) -> set[str]:
    """Return the set of tool names a code step calls.

    Recognises the import/call shapes documented above. Returns an empty
    set when the step has no code (text/llm steps) or calls no recognised
    tool — those procedures can't be suggested by first-tool match and are
    skipped by the gate.
    """
    if not code:
        return set()
    tools: set[str] = set()
    tools.update(_CUSTOM_TOOLS_IMPORT_RE.findall(code))
    tools.update(_CUSTOM_TOOLS_IMPORT_BARE_RE.findall(code))
    tools.update(_DISPATCH_CALL_COMMENT_RE.findall(code))
    if _RUN_PROCEDURE_RE.search(code):
        tools.add("run_procedure")
    # A subprocess shell-out is the procedure equivalent of the model's
    # ``code_run`` tool. Surface it as code_run so a model reaching for
    # code_run to do git/subprocess work is nudged toward the procedure
    # that does the same work deterministically.
    if _SUBPROCESS_RE.search(code):
        tools.add("code_run")
    if _VAULT_SEARCH_RE.search(code):
        tools.add("vault_search")
    if _VAULT_READ_NOTE_RE.search(code):
        tools.add("vault_read_note")
    if _VAULTBOT_SYNC_RE.search(code):
        tools.add("vaultbot_sync")
    if _GITHUB_ISSUES_RE.search(code):
        tools.add("github_issues")
    return tools


def _fm_list(frontmatter: dict[str, Any], key: str) -> list[str]:
    """Pull a list frontmatter value as a list of stripped strings."""
    v = frontmatter.get(key)
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    return [s] if s else []


def _fm_str(frontmatter: dict[str, Any], key: str) -> str:
    """Pull a scalar frontmatter value as a stripped string."""
    v = frontmatter.get(key)
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return " ".join(str(x) for x in v).strip()
    return str(v).strip().strip('"').strip("'")


def build_first_tool_index(
    proc_index: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Compile every procedure in ``proc_index`` once and return a
    ``{stem: {first_tools, triggers, description, status, allowed_tools}}`` map.

    ``proc_index`` is the stem -> {path, frontmatter} map built by
    ``procedure_tracker.get_procedure_index``. This function does NOT rewalk
    the vault — it reuses the existing index and only parses the few
    procedure bodies needed to extract step 1's tool calls.

    Pure and side-effect-free; the caller owns caching.
    """
    out: dict[str, dict[str, Any]] = {}
    for stem, entry in proc_index.items():
        path = entry.get("path", "")
        fm = entry.get("frontmatter") or {}
        if not path:
            continue
        proc = compile_procedure(path)
        if proc is None or not proc.steps:
            continue
        first_step = proc.steps[0]
        first_tools: set[str] = set()
        if first_step.step_type == "code" and first_step.code:
            first_tools = _extract_first_tools(first_step.code)
        # An LLM-first-step procedure can't be suggested by first-tool
        # match (the model isn't calling a tool in step 1). Skip it — the
        # gate would never fire for it.
        if not first_tools:
            continue
        triggers: list[str] = []
        # ``trigger`` and ``when_to_use`` are the matching signal. Both are
        # free-text; we lowercase + split on non-word chars for cheap
        # keyword overlap with the user message (no embeddings needed at
        # dispatch time — the gate must be fast, it runs on every tool
        # call).
        for key in ("trigger", "when_to_use", "description"):
            val = _fm_str(fm, key)
            if val:
                triggers.append(val.lower())
        triggers.extend(t.lower() for t in _fm_list(fm, "tags"))
        out[stem] = {
            "first_tools": first_tools,
            "triggers": triggers,
            "description": _fm_str(fm, "description"),
            "status": _fm_str(fm, "status"),
            "allowed_tools": _fm_list(fm, "allowed_tools"),
        }
    return out


def refresh_first_tool_index(
    proc_index: dict[str, dict[str, Any]],
    cached: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Rebuild the index only for stems whose frontmatter mtime changed.

    For now this is a full rebuild — the procedure set is small (~150 notes)
    and a full parse pass is <100ms. A diff-based refresh is a future
    optimisation; correctness first.
    """
    _ = cached  # reserved for future incremental refresh
    return build_first_tool_index(proc_index)