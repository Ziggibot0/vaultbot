"""Procedure suggestion gate — the "autofill" nudge.

When the chat model emits a raw tool call (``vaultbot_sync``,
``code_run``, ``github_issues``, …), this gate checks whether the
**retrieval-selected preflight hint procedure** starts with that same
tool. If so, the gate returns a *suggestion* instead of executing the raw
call: the model is told which procedure retrieval already picked for this
task and is given the option to call ``execute_procedure("X")`` or reply
``proceed`` to run its raw call as-is.

Candidate selection is score-driven end to end: the hint comes from FUSED
retrieval ranking the procedure library against the user message (see
``chat_turn_prep`` / ``chat_preflight.deterministic_procedure_hint``), and
the gate only confirms that the hinted procedure's first step uses the
tool in question — a structural property of the procedure, not a lexical
match on the user's words. There are deliberately NO keyword/trigger-word
heuristics here: word overlap between the user message and procedure
triggers produced false suggestions (e.g. suggesting Analyze-Session-Log
for a git-status ``code_run``), and Sean's standing rule is deterministic
contracts + scored retrieval, never magic words.

This is a nudge, not a hard block. The escape hatch (``proceed``) keeps the
gate from dead-locking on edge cases where the procedure genuinely doesn't
fit. The gate fires at most **once per (tool_name) per session** — the
second call to the same tool passes through unchanged so the model can't
loop on the nudge itself. Overriding the nudge is logged so trigger tuning
can close the gap.

Motivated by session ``eb8143f7``: the model reached for raw ``code_run``
+ ``vaultbot_sync`` ~10 times to sync the repo, even though
``Git-Sync-Upstream`` exists and does exactly that in one
``execute_procedure`` call. The gate makes the procedure visible at the
exact moment the model reaches for the tool it would wrap.

See:
  - ``procedure_first_tool_index.py`` — the index this consumes.
  - ``chat_tool_dispatch.execute_agent_tool`` — the call site.
  - ``chat_turn_prep.prepare_turn`` — computes and stashes the hint.
  - [[Session-eb8143f7]] — the loop this fixes.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

# Tools the model calls that we should suggest procedures for. ``code_run``
# is included because procedures that shell out via subprocess surface as
# ``code_run`` (see ``procedure_first_tool_index._extract_first_tools``).
_SUGGESTIBLE_TOOLS: frozenset[str] = frozenset(
    {
        "vaultbot_sync",
        "github_issues",
        "code_run",
        "vault_search",
        "vault_read_note",
        "vault_research",
        "gh_pr_create",
        "gh_pr_merge",
        "git_rollback",
    }
)


def check_procedure_suggestion(
    tool_name: str,
    hint_stem: str,
    first_tool_index: dict[str, dict[str, Any]],
    already_suggested: set[str] | None = None,
) -> dict[str, Any] | None:
    """Return a suggestion dict, or ``None`` to let the raw call proceed.

    Fires only when ``hint_stem`` — the procedure that scored retrieval
    already selected for this turn — starts with the tool the model just
    reached for. No hint (or a hint whose first step uses different tools)
    means no suggestion: the gate never invents candidates from word
    overlap.

    The suggestion dict is returned to the model as a synthetic tool result
    (see ``execute_agent_tool``), shaped so the model can act on it without
    re-reading the procedure.

    Args:
        tool_name: The tool the model just called.
        hint_stem: The preflight fused-score procedure hint for this turn
            ("" when retrieval found no matching procedure).
        first_tool_index: The compiled {stem: {first_tools, …}}.
        already_suggested: Per-session set of ``tool_name``s already nudged.
            A tool is nudged at most once per session so the model can't
            loop on the suggestion. Pass a live set to enable de-dup; pass
            ``None`` to disable de-dup (tests).
    """
    if tool_name not in _SUGGESTIBLE_TOOLS:
        return None
    if not hint_stem:
        return None
    if already_suggested is not None and tool_name in already_suggested:
        return None

    entry = first_tool_index.get(hint_stem)
    if not entry:
        return None
    if tool_name not in entry.get("first_tools", set()):
        return None
    # Skip flagged procedures — they're blocked from execution.
    if entry.get("status", "").lower() == "flagged":
        return None

    # Record the nudge so the same tool isn't nudged again this session.
    # Mutating the caller's set here keeps the call site one-liner clean
    # and makes de-dup impossible to forget.
    if already_suggested is not None:
        already_suggested.add(tool_name)

    description = entry.get("description", "")
    msg = (
        f"Retrieval already selected a procedure for this task, and it "
        f"starts with the same tool you just called ({tool_name}). "
        f"Procedure: {hint_stem}."
    )
    if description:
        msg += f" {description}"
    msg += (
        " Call execute_procedure with this procedure name to run the full "
        "guided sequence, or reply 'proceed' to run your raw "
        f"{tool_name} call as-is. Use procedures first — they save energy."
    )

    return {
        "procedure_suggestion": hint_stem,
        "first_tool": tool_name,
        "description": description,
        "message": msg,
        "proceed_keyword": "proceed",
    }


def _normalize_name(name: str) -> str:
    """Lowercase and strip non-alphanumerics so mangled names compare equal."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def check_procedure_name_suggestion(
    proc_name: str,
    proc_index: dict[str, dict[str, Any]],
    k: int = 5,
) -> dict[str, Any] | None:
    """Return a top-k suggestion dict when ``proc_name`` doesn't resolve.

    Fires when the model calls ``execute_procedure`` with a name that isn't
    an exact stem in ``proc_index`` (typo, extra spaces, wrong case, or a
    hallucination). Ranks known procedures by normalized name similarity
    (difflib) and returns the top ``k`` exact stems so the model *selects*
    a valid name instead of re-generating one from memory. Returns ``None``
    on an exact match or when no candidate clears the similarity floor.
    Mirrors ``check_procedure_suggestion``'s dict shape
    (``procedure_suggestion``, ``candidates``, ``message``,
    ``proceed_keyword``).
    """
    if not proc_name or not proc_index or proc_name in proc_index:
        return None

    norm_target = _normalize_name(proc_name)

    scored: list[tuple[float, str, dict[str, Any]]] = []
    for stem, entry in proc_index.items():
        fm = entry.get("frontmatter") or {}
        if str(fm.get("status", "")).strip().lower() == "flagged":
            continue
        name_sim = difflib.SequenceMatcher(
            None, norm_target, _normalize_name(stem)
        ).ratio()
        scored.append((name_sim, stem, entry))

    if not scored:
        return None

    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:k]
    if top[0][0] < 0.4:
        return None

    candidates = [stem for _, stem, _ in top]
    best_stem = candidates[0]
    best_desc = (top[0][2].get("frontmatter") or {}).get("description", "")

    msg = (
        f"No procedure named '{proc_name}' exists. Closest matches: "
        + ", ".join(candidates)
        + ". Call execute_procedure with one of these exact names, or reply "
        "'proceed' to abandon the procedure and answer directly."
    )

    return {
        "procedure_suggestion": best_stem,
        "candidates": candidates,
        "description": best_desc,
        "message": msg,
        "proceed_keyword": "proceed",
    }
