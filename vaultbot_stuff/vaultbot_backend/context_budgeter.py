"""
Context Budgeter: ensure retrieved vault context fits within the LLM's token budget.

Pure deterministic -- no LLM calls. Estimates token counts from character
length (~4 chars/token for English), calculates available budget after
reserving space for system prompt overhead, chat history, and response,
and truncates the context string if it exceeds the budget.

The context from build_abstract_context is structured with highest-priority
info first (L2 MOC -> L1 concept cards -> L0 drill-down). Truncating from
the end drops the lowest-priority detail first, preserving the reasoning
highway and topic orientation that a small model needs most.

This is the scaling safety net from [[Context-Budgeting-for-Vault-Growth]]:
as the vault grows past hundreds of notes, retrieved subgraphs get larger.
Without a budget, the system degrades silently -- the model gets flooded
with context and suffers "lost in the middle" effects. The budgeter keeps
the most important information and drops the rest, deterministically.

Integration point in main.py:
    After build_abstract_context() returns the context string,
    BEFORE build_system_prompt_briefing() bakes it into the system prompt.

    budgeted = context_budgeter.budget(context, websocket.conversation_history)
    context = budgeted["context"]
    session_logger.log("context_budget", budgeted)

Configurable via environment variables:
    VAULTBOT_CONTEXT_LIMIT   - model's context window size (default 32768)
    VAULTBOT_CONTEXT_OVERHEAD - system prompt overhead reserve (default 4096)
    VAULTBOT_CONTEXT_RESPONSE - response reserve (default 4096)
"""
from __future__ import annotations

import os
from typing import Any

from config import TUNABLES


class ContextBudgeter:
    """Rank and truncate retrieved vault context to fit within a token budget.

    Pure deterministic. No LLM calls. No external dependencies.
    """

    CHARS_PER_TOKEN = TUNABLES.chars_per_token  # rough estimate for English text

    def __init__(
        self,
        model_context_limit: int = 0,
        system_prompt_overhead: int = 0,
        response_reserve: int = 0,
    ):
        """Initialize the budgeter.

        Args accept explicit values for testing. When left at 0 (default),
        they read from environment variables with sensible fallbacks.
        """
        self.model_context_limit = model_context_limit or int(
            os.getenv("VAULTBOT_CONTEXT_LIMIT", "32768")
        )
        # The overhead must reserve space for the NON-context portion of the
        # system prompt: identity files (~3K tokens) + build_system_prompt_briefing
        # boilerplate (~2K) + gaps summary (~1K) + 24 tool schemas (~5K) +
        # headroom. The old default of 4096 vastly undercounted this, letting
        # the vault context grow to ~94K chars (~23K tokens) so the system
        # prompt alone hit 113K chars — larger than the compactor's 80K cap.
        # The compactor then shredded recent history to 200-char fragments
        # while leaving the bloated system prompt intact, and the agent
        # gravitated to the days-old goal in the untouched identity block
        # ("redoing a prompt from days ago"). 16000 tokens reserves enough
        # that the vault context shrinks to ~50K chars, keeping the whole
        # system prompt under the 80K compactor cap and leaving room for
        # live history + response within a 32K-token window.
        self.system_prompt_overhead = system_prompt_overhead or int(
            os.getenv("VAULTBOT_CONTEXT_OVERHEAD", "16000")
        )
        self.response_reserve = response_reserve or int(
            os.getenv("VAULTBOT_CONTEXT_RESPONSE", "4096")
        )

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count from character length.

        Uses ~4 chars/token which is a reasonable estimate for English text
        with markdown formatting. Not exact, but deterministic and good enough
        for budgeting -- being slightly conservative is better than overflow.
        """
        if not text:
            return 0
        return max(1, len(text) // self.CHARS_PER_TOKEN)

    def budget(
        self,
        context: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Ensure context fits within the token budget.

        Args:
            context: The context string from build_abstract_context.
            conversation_history: Prior conversation turns (dicts with
                "content" key). Used to calculate how much space the chat
                history consumes in the context window.

        Returns:
            {
                "context": str,          # possibly truncated
                "original_tokens": int,  # token count before budgeting
                "budgeted_tokens": int,  # token count after budgeting
                "budget": int,           # available tokens for context
                "truncated": bool,       # was truncation needed?
                "chars_dropped": int,    # characters removed
            }
        """
        context_tokens = self.estimate_tokens(context)

        # Estimate chat history token cost
        history_tokens = 0
        if conversation_history:
            for turn in conversation_history:
                if isinstance(turn, dict):
                    content = turn.get("content", "")
                else:
                    content = str(turn)
                history_tokens += self.estimate_tokens(content)

        # Calculate available budget for context
        available = (
            self.model_context_limit
            - self.system_prompt_overhead
            - history_tokens
            - self.response_reserve
        )
        available = max(0, available)  # never negative

        # Hard cap on vault context: even on a 128K+ model where the
        # computed budget is 100K+ tokens, flooding the model with a
        # massive retrieved-context block causes long prompt-processing
        # times (the user's "2000 t/s but still slow" symptom). The hard
        # token cap in chat_handler guarantees the TOTAL conversation
        # stays under ~60K tokens; this vault-context cap ensures we
        # don't build a huge context just to have the hard cap prune it
        # aggressively. 30K tokens (~120K chars) is generous for the L1
        # highway + L0 drill-down. Override via VAULTBOT_MAX_CONTEXT_TOKENS.
        _max_context_tokens = int(os.getenv(
            "VAULTBOT_MAX_CONTEXT_TOKENS", "30000"))
        if _max_context_tokens > 0 and available > _max_context_tokens:
            available = _max_context_tokens

        # If context fits, no action needed
        if context_tokens <= available:
            return {
                "context": context,
                "original_tokens": context_tokens,
                "budgeted_tokens": context_tokens,
                "budget": available,
                "truncated": False,
                "chars_dropped": 0,
            }

        # Truncate: keep the first `available * CHARS_PER_TOKEN` chars.
        # The beginning has L2 MOC + L1 concept cards (highest priority).
        # The end has L0 drill-down detail (lowest priority to drop).
        max_chars = available * self.CHARS_PER_TOKEN
        truncated = context[:max_chars]

        return {
            "context": truncated,
            "original_tokens": context_tokens,
            "budgeted_tokens": self.estimate_tokens(truncated),
            "budget": available,
            "truncated": True,
            "chars_dropped": len(context) - len(truncated),
        }
