"""Per-turn mutable state for the agentic chat loop.

Extracted from ``chat_handler.py`` — ``TurnState`` is a pure data container
that groups the ~18 mutable locals previously scattered across the
``handle_chat`` body. It has NO behavior — it exists so the agentic loop
can be extracted into ``chat_agentic_loop.py`` without passing 18 separate
parameters.

The loop mutates this object in place across rounds; ``handle_chat`` reads
the final values (``final_answer``, ``thinking_text``, etc.) after the loop
returns.

This is a leaf module in the chat-handler family (see ``chat_context.py``,
``chat_preflight.py``, ``chat_helpers.py`` for the established pattern).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TurnState:
    """Mutable per-turn state shared across the agentic loop's rounds.

    All fields are plain data. The loop mutates them in place; nothing here
    performs I/O or touches ``Services``.
    """

    # Accumulated answer text (the FINAL synthesis round only).
    final_answer: str = ""
    # Interim narration streamed during tool-calling rounds ("Let me
    # check X..."). Kept separate from final_answer so the persisted chat
    # history and chat notes read as a clean synthesis instead of a
    # scratchpad (issue #388). Still captured in partial files and
    # checkpoints so a crash loses nothing.
    interim_text: str = ""
    # Accumulated reasoning text (the model's thinking stream).
    thinking_text: str = ""
    # Total streamed chunks across all rounds (for the llm_generate log).
    total_chunks: int = 0
    # Current round index (incremented at the end of each loop iteration).
    round_idx: int = 0

    # Tool history for the chat-loop checkpoint (survives a crash mid-turn).
    _turn_tool_history: list = field(default_factory=list)
    # Number of rounds that executed at least one tool call.
    _tool_rounds_executed: int = 0
    # Double-silent failsafe: model emitted nothing on the prior round.
    _double_silent_once: bool = False

    # Working-memory signature cache (sentinel so the first refresh fires).
    _last_step_rag_key: Any = field(default_factory=object)

    # Per-turn token cost accumulator (prompt/completion/rounds).
    _turn_token_totals: dict = field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "rounds": 0,
        }
    )

    # Seen-content tracker: {file_path: {"source", "lines", "round"}}.
    _seen_content: dict = field(default_factory=dict)

    # Allowed-citations set (closed-set citation enforcement):
    # {note_stem: {"file_path": str, "snippet": str}}. Built per-turn from
    # the retrieved vault context (the `### [[Name]]` headers in the
    # rendered context). Updated when the model calls vault_search /
    # vault_read_note mid-loop so tool-retrieved notes are also valid
    # citation targets. The grounding gate in finalize_turn checks the
    # answer's [[wikilinks]] against this set; wikilinks NOT in it are
    # treated as ungrounded even if the note exists in the graph.
    _allowed_citations: dict = field(default_factory=dict)

    # Grounding is OBSERVATIONAL (see chat_turn_finalize): the closed-set
    # gate, its retry loop, and its reprimand plumbing were removed. The
    # drafted answer from the first agentic pass is the answer the user
    # gets; scoring now only drives the trust badge / Sources block.
    _grounding_retry_count: int = 0

    # Findings ledger (anti-amnesia): 1-line entries per round.
    _findings: list = field(default_factory=list)

    # Failed-write streak (3 consecutive = break the loop).
    _turn_failed_write_count: int = 0
    # Consecutive thought-only rounds (5 = inject nudge, 7 = break).
    _consecutive_thought_rounds: int = 0
    # The last tool observed in a single-tool same-result streak.
    _last_tool_name: str = ""
    # Consecutive rounds that repeated the same single tool with the same result.
    _consecutive_same_tool: int = 0

    # Partial-answer crash-protection file path.
    partial_path: Path | None = None
    # Debounce timestamp for partial writes (at most once per second).
    _last_partial_write_s: float = 0.0

    # The conversation actually sent to the LLM on the LAST round (after
    # tool-history sanitization). Recomputed each round, but the final value
    # is read after the loop by finalize_turn for token-cost estimation.
    _model_conversation: list = field(default_factory=list)
    # (temporal/recency + coaching-turn user-message flags were removed with
    # the lexical classifiers they fed — repo rule: no keyword-list intents.)
