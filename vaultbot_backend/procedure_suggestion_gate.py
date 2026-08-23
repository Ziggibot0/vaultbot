"""Procedure suggestion gate — the "autofill" nudge.

When the chat model emits a raw tool call (``vaultbot_sync``,
``code_run``, ``github_issues``, …), this gate checks whether a procedure
exists whose **first step calls the same tool** AND whose
``trigger``/``when_to_use``/``tags`` match the current user message. If so,
the gate returns a *suggestion* instead of executing the raw call: the model
is told which procedure matches and is given the option to call
``execute_procedure("X")`` or reply ``proceed`` to run its raw call as-is.

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
  - [[Session-eb8143f7]] — the loop this fixes.
"""

from __future__ import annotations

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

# Minimum keyword-overlap score for a trigger to count as a match. A bare
# ``code_run`` match is a weak signal (many procedures shell out), so we
# require the trigger text to overlap the user message by at least one
# word. This stops the gate from firing on every ``code_run`` the model
# emits.
_MIN_TRIGGER_OVERLAP = 1

# Word splitter — non-alphanumeric runs. Lowercases for case-insensitive
# overlap. Short stopwords are dropped so "the repo is synced" doesn't
# match a procedure whose trigger is "the daily check".
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "to", "of", "in", "on", "at", "for", "with", "and", "or", "not",
        "this", "that", "it", "its", "i", "you", "we", "they", "me", "my",
        "your", "our", "do", "does", "did", "so", "as", "if", "then",
        "please", "can", "could", "would", "should", "will", "just", "now",
        "up", "down", "out", "into", "from", "by",
    }
)


def _words(text: str) -> set[str]:
    """Tokenise into lowercase word tokens, dropping stopwords + length<=2."""
    toks = {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 2}
    toks -= _STOPWORDS
    return toks


def _trigger_overlap(user_words: set[str], triggers: list[str]) -> int:
    """Max word-overlap count between the user message and any trigger text."""
    if not user_words or not triggers:
        return 0
    best = 0
    for trig in triggers:
        tw = _words(trig)
        if not tw:
            continue
        overlap = len(user_words & tw)
        if overlap > best:
            best = overlap
    return best


def check_procedure_suggestion(
    tool_name: str,
    user_message: str,
    first_tool_index: dict[str, dict[str, Any]],
    already_suggested: set[str] | None = None,
) -> dict[str, Any] | None:
    """Return a suggestion dict, or ``None`` to let the raw call proceed.

    The suggestion dict is returned to the model as a synthetic tool result
    (see ``execute_agent_tool``). It is shaped so the model can act on it
    without re-reading the procedure:

    .. code-block:: python

        {
            "procedure_suggestion": "Git-Sync-Upstream",
            "first_tool": "vaultbot_sync",
            "description": "Syncs local repo with upstream main.",
            "message": "Procedure 'Git-Sync-Upstream' starts with the same
                        tool you just called (vaultbot_sync) and matches this
                        task. Call execute_procedure('Git-Sync-Upstream') to
                        run it, or reply 'proceed' to run vaultbot_sync as-is.",
            "proceed_keyword": "proceed",
        }

    Args:
        tool_name: The tool the model just called.
        user_message: The current user turn's text (trigger match cue).
        first_tool_index: The compiled {stem: {first_tools, triggers, …}}.
        already_suggested: Per-session set of ``tool_name``s already nudged.
            A tool is nudged at most once per session so the model can't
            loop on the suggestion. Pass a live set to enable de-dup; pass
            ``None`` to disable de-dup (tests).
    """
    if tool_name not in _SUGGESTIBLE_TOOLS:
        return None
    if already_suggested is not None and tool_name in already_suggested:
        return None

    user_words = _words(user_message) if user_message else set()

    # Find procedures whose first step calls ``tool_name`` AND whose
    # trigger text overlaps the user message. When multiple match, pick
    # the one with the highest trigger overlap (most specific).
    best_stem: str | None = None
    best_score = -1
    best_entry: dict[str, Any] | None = None
    for stem, entry in first_tool_index.items():
        if tool_name not in entry.get("first_tools", set()):
            continue
        # Skip flagged procedures — they're blocked from execution.
        if entry.get("status", "").lower() == "flagged":
            continue
        score = _trigger_overlap(user_words, entry.get("triggers", []))
        # For ``code_run`` (weak first-tool signal) require trigger overlap
        # >= the minimum. For a direct tool like ``vaultbot_sync`` the
        # first-tool match is already strong — allow a zero-overlap match
        # (the tool itself is the cue) but still prefer overlap.
        if tool_name == "code_run" and score < _MIN_TRIGGER_OVERLAP:
            continue
        if score > best_score:
            best_score = score
            best_stem = stem
            best_entry = entry

    if best_stem is None or best_entry is None:
        return None

    # Record the nudge so the same tool isn't nudged again this session.
    # Mutating the caller's set here keeps the call site one-liner clean
    # and makes de-dup impossible to forget.
    if already_suggested is not None:
        already_suggested.add(tool_name)

    description = best_entry.get("description", "")
    msg = (
        f"A procedure matches this task and starts with the same tool you "
        f"just called ({tool_name}). "
        f"Procedure: {best_stem}."
    )
    if description:
        msg += f" {description}"
    msg += (
        " Call execute_procedure with this procedure name to run the full "
        "guided sequence, or reply 'proceed' to run your raw "
        f"{tool_name} call as-is. Use procedures first — they save energy."
    )

    return {
        "procedure_suggestion": best_stem,
        "first_tool": tool_name,
        "description": description,
        "message": msg,
        "proceed_keyword": "proceed",
    }