"""
Context compaction for long-running chat sessions — the OpenHands Condenser pattern
(arXiv:2511.03690).

When the conversation history grows too long, summarize the *middle* while keeping
the head (system prompt + opening messages) and tail (recent messages) verbatim.
This prevents context overflow without losing the thread of the conversation.

This compacts *before* the effective-context cliff described by Liu et al.
("Lost in the Middle: How Language Models Use Long Contexts", arXiv:2307.03172):
models degrade well before hitting the window limit, so we summarize early at a
conservative threshold rather than waiting until the very end of the context.

Pure stdlib + existing project imports. No new dependencies.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Conservative compaction thresholds (well below typical 8k–32k windows).
_TOKEN_COMPACT_THRESHOLD = 20000
_CHARS_PER_TOKEN = 4  # rough estimate


class Compactor:
    """Summarize the middle of a long conversation to keep it within budget.

    Implements the OpenHands Condenser pattern: head + [compacted summary] + tail.
    Never raises — on any failure it returns the original messages unchanged so
    the chat loop cannot be broken by compaction.
    """

    def __init__(
        self,
        ollama_client: Any | None = None,
        session_logger: Any | None = None,
        max_messages: int = 80,
        keep_head: int = 4,
        keep_tail_ratio: float = 0.4,
        summary_max_tokens: int = 500,
    ) -> None:
        self.ollama_client = ollama_client
        self.session_logger = session_logger
        self.max_messages = max_messages
        self.keep_head = max(0, int(keep_head))
        self.keep_tail_ratio = float(keep_tail_ratio)
        self.summary_max_tokens = int(summary_max_tokens)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def should_compact(self, messages: list[dict[str, Any]]) -> bool:
        """Return True if the conversation is long enough to warrant compaction."""
        try:
            if not messages:
                return False
            if len(messages) > self.max_messages:
                return True
            if self.estimate_tokens(messages) > _TOKEN_COMPACT_THRESHOLD:
                return True
            return False
        except Exception as exc:  # never crash the chat loop
            logger.debug("should_compact error: %s", exc)
            return False

    def compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Compact the conversation if needed; otherwise return it unchanged.

        On any failure, the original message list is returned unmodified.
        """
        try:
            if not messages:
                return messages
            if not self.should_compact(messages):
                return messages

            n = len(messages)
            before_tokens = self.estimate_tokens(messages)

            # Split into head / middle / tail.
            head_count = min(self.keep_head, n)
            tail_count = max(0, int(round(n * self.keep_tail_ratio)))
            # Ensure head + tail don't overlap or eat the whole list.
            if head_count + tail_count >= n:
                # Not enough middle to summarize — nothing useful to compact.
                return messages
            head = messages[:head_count]
            tail = messages[n - tail_count:] if tail_count > 0 else []
            middle = messages[head_count:n - tail_count] if tail_count > 0 else messages[head_count:]

            if not middle:
                return messages

            # Summarize the middle.
            try:
                if self.ollama_client is not None:
                    summary = self._summarize_with_llm(middle)
                else:
                    summary = self._extractive_summary(middle)
            except Exception as exc:
                logger.warning("summarization failed, falling back to extractive: %s", exc)
                try:
                    self._log_exc(exc, context="summarize_middle")
                except Exception:
                    pass
                summary = self._extractive_summary(middle)

            summary_msg = {
                "role": "system",
                "content": "[Compacted history summary]: " + summary,
            }
            compacted = head + [summary_msg] + tail
            after_tokens = self.estimate_tokens(compacted)

            self._log_compaction(
                before_count=n,
                after_count=len(compacted),
                before_tokens=before_tokens,
                after_tokens=after_tokens,
                middle_count=len(middle),
            )
            return compacted
        except Exception as exc:
            logger.error("compaction failed, returning original messages: %s", exc)
            try:
                self._log_exc(exc, context="compact")
            except Exception:
                pass
            return messages

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Estimate total tokens as sum(len(content) / 4) across all messages."""
        try:
            total_chars = 0
            for m in messages:
                content = m.get("content", "") if isinstance(m, dict) else ""
                if content is None:
                    content = ""
                total_chars += len(str(content))
            return total_chars // _CHARS_PER_TOKEN
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    _SUMMARY_PROMPT = (
        "Summarize the following conversation history concisely. Preserve: the user's "
        "original goal, key decisions made, important tool results/facts learned, and any "
        "open questions. Be specific and brief (max 500 tokens). Return only the summary, "
        "no preamble."
    )

    def _summarize_with_llm(self, middle_messages: list[dict[str, Any]]) -> str:
        """Build a prompt, call ollama_client.chat, return the summary text.

        Catches all errors; on failure the caller falls back to extractive.
        """
        # Render the middle messages into a compact transcript for the LLM.
        transcript_lines: list[str] = []
        for m in middle_messages:
            role = m.get("role", "user") if isinstance(m, dict) else "user"
            content = m.get("content", "") if isinstance(m, dict) else ""
            if content is None:
                content = ""
            transcript_lines.append(f"{role}: {content}")
        transcript = "\n".join(transcript_lines)

        prompt_messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self._SUMMARY_PROMPT,
            },
            {
                "role": "user",
                "content": transcript,
            },
        ]

        try:
            result = self.ollama_client.chat(
                prompt_messages,
                temperature=0.3,
                stream=False,
            )
        except TypeError:
            # Older signature may not accept stream/temperature kwargs.
            result = self.ollama_client.chat(prompt_messages)

        # Robustly extract text from the LLM response across client shapes.
        if isinstance(result, dict):
            # OllamaClient.chat returns {"response": ..., "thinking": ..., "tool_calls": ...}
            if result.get("response"):
                return str(result["response"]).strip()
            msg = result.get("message")
            if isinstance(msg, dict) and msg.get("content"):
                return str(msg["content"]).strip()
            if result.get("content"):
                return str(result["content"]).strip()
        if isinstance(result, str):
            return result.strip()
        # Last resort — let the caller fall back to extractive.
        raise RuntimeError("LLM returned no usable summary content")

    def _extractive_summary(self, middle_messages: list[dict[str, Any]]) -> str:
        """Simple extractive fallback: keep role + first 150 chars of each message."""
        try:
            lines: list[str] = []
            for m in middle_messages:
                role = m.get("role", "user") if isinstance(m, dict) else "user"
                content = m.get("content", "") if isinstance(m, dict) else ""
                if content is None:
                    content = ""
                content = str(content)
                snippet = content[:150].replace("\n", " ")
                lines.append(f"[{role}] {snippet}")
            return "\n".join(lines)
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------
    def _log_compaction(
        self,
        before_count: int,
        after_count: int,
        before_tokens: int,
        after_tokens: int,
        middle_count: int,
    ) -> None:
        try:
            if self.session_logger is None:
                logger.info(
                    "compactor: %d -> %d messages, %d -> %d est tokens (middle=%d)",
                    before_count, after_count, before_tokens, after_tokens, middle_count,
                )
                return
            log = self.session_logger.log if hasattr(self.session_logger, "log") else None
            if log is not None:
                log("compaction", {
                    "before_count": before_count,
                    "after_count": after_count,
                    "before_tokens": before_tokens,
                    "after_tokens": after_tokens,
                    "middle_count": middle_count,
                })
            else:
                logger.info(
                    "compactor: %d -> %d messages, %d -> %d est tokens (middle=%d)",
                    before_count, after_count, before_tokens, after_tokens, middle_count,
                )
        except Exception:
            pass

    def _log_exc(self, exc: Exception | None, context: str | None = None) -> None:
        try:
            if self.session_logger is not None and hasattr(self.session_logger, "log_exception"):
                self.session_logger.log_exception(exc, context=context)
            elif exc is not None:
                logger.debug("compactor error in %s: %s", context, exc)
        except Exception:
            pass


__all__ = ["Compactor"]
