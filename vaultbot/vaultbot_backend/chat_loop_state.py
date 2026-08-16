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

    # Accumulated answer text (streamed chunks + non-final round text).
    final_answer: str = ""
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
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "rounds": 0}
    )

    # Seen-content tracker: {file_path: {"source", "lines", "round"}}.
    _seen_content: dict = field(default_factory=dict)

    # Findings ledger (anti-amnesia): 1-line entries per round.
    _findings: list = field(default_factory=list)

    # Go-find-out escalation: consecutive vault_search rounds with all-seen.
    _consecutive_all_seen: int = 0
    _go_find_out_fired: bool = False
    # Last vault_search query (used as the go-find-out research topic).
    _last_search_query: str = ""
    # Go-find-out system message, injected after tool results are appended.
    _go_find_out_msg: str = ""

    # Failed-write streak (3 consecutive = break the loop).
    _turn_failed_write_count: int = 0
    # Consecutive thought-only rounds (5 = inject nudge, 7 = break).
    _consecutive_thought_rounds: int = 0

    # Partial-answer crash-protection file path.
    partial_path: Path | None = None
    # Debounce timestamp for partial writes (at most once per second).
    _last_partial_write_s: float = 0.0

    # The conversation actually sent to the LLM on the LAST round (after
    # tool-history sanitization). Recomputed each round, but the final value
    # is read after the loop by finalize_turn for token-cost estimation.
    _model_conversation: list = field(default_factory=list)
