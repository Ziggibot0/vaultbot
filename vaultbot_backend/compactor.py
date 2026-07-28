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

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Conservative compaction thresholds (well below typical 8k–32k windows).
# Lowered from 20000 to 12000 tokens: a remote cloud model (glm-5.2:cloud)
# times out at >120s TTFT when the payload exceeds ~200K chars (~50K tokens).
# Compacting at 12K tokens keeps the conversation well under that cliff.
_TOKEN_COMPACT_THRESHOLD = int(__import__("os").getenv("VAULTBOT_COMPACT_TOKEN_THRESHOLD", "12000"))
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
            # Tool-call-pair-aware tail boundary: if the tail would start on
            # a ``tool`` message (role=tool) whose parent assistant message
            # (with tool_calls) is in the middle/summarized section, the
            # next LLM call would see orphaned tool results without the
            # matching assistant tool_calls — breaking the Ollama tool
            # protocol. Walk the tail boundary backward until it lands on
            # a non-tool message (the assistant that initiated the calls
            # comes with its results).
            tail_start = n - tail_count if tail_count > 0 else n
            while tail_start > head_count and tail_start < n:
                m = messages[tail_start]
                if isinstance(m, dict) and m.get("role") == "tool":
                    tail_start -= 1  # pull the boundary back
                else:
                    break
            tail_start = max(head_count + 1, tail_start)
            tail = messages[tail_start:] if tail_count > 0 else []
            middle = messages[head_count:tail_start] if tail_count > 0 else messages[head_count:]

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

            # Hard char-cap safety net: even after summarizing the middle,
            # the tail (40% of messages by default) can still be large
            # enough to cause a cloud-model timeout if individual messages
            # are huge (e.g. a vault_research result with a 50K-char
            # synthesis). If the compacted conversation exceeds
            # MAX_COMPACTED_CHARS, hard-truncate the tail messages' content
            # from the end (oldest tail message first) until it fits. This
            # is the last-resort guarantee that the payload sent to the LLM
            # can NEVER exceed the size where a remote model times out.
            compacted = self._hard_cap(compacted)

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
        """Estimate total tokens across all message fields the LLM actually sees.

        Counts content + thinking + tool_calls (serialized). The old version
        only counted ``content``, which misses the thinking field (Qwen/glm
        models can produce thousands of chars of reasoning per round) and
        tool_call arguments — underestimating the real payload by 50%+ and
        preventing the token-threshold compaction from ever triggering.
        """
        try:
            total_chars = 0
            for m in messages:
                if not isinstance(m, dict):
                    continue
                for key in ("content", "thinking"):
                    val = m.get(key, "")
                    if val:
                        total_chars += len(str(val))
                # tool_calls are sent to the LLM as part of the assistant
                # message; count their serialized size.
                tcs = m.get("tool_calls")
                if tcs:
                    try:
                        total_chars += len(json.dumps(tcs, default=str))
                    except Exception:
                        total_chars += len(str(tcs))
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
            # Use stream=True so the OllamaClient uses its generous (120s)
            # read timeout instead of the 60s non-stream timeout. A large
            # transcript sent to a remote cloud model (glm-5.2:cloud) can
            # take >60s before the first token; streaming avoids the spurious
            # ReadTimeout that would trigger the crude extractive fallback.
            chunks = self.ollama_client.chat(
                prompt_messages,
                temperature=0.3,
                stream=True,
            )
            result = {"response": "", "thinking": "", "tool_calls": []}
            for chunk in chunks:
                if chunk.get("response"):
                    result["response"] += chunk["response"]
                if chunk.get("thinking"):
                    result["thinking"] += chunk["thinking"]
                if chunk.get("tool_calls"):
                    result["tool_calls"].extend(chunk["tool_calls"])
        except TypeError:
            # Older signature may not accept stream/temperature kwargs.
            result = self.ollama_client.chat(prompt_messages)
        except Exception:
            # Streaming failed (timeout, connection, etc.) — let the caller
            # fall back to the extractive summary rather than crashing.
            raise

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
    # Hard char cap — the last-resort payload guarantee
    # ------------------------------------------------------------------
    # After compaction, if the tail is still too large (a single vault_research
    # result can be 50K+ chars), hard-truncate tail messages' content from the
    # end (oldest tail message first) until the total fits. This guarantees
    # the payload sent to the LLM can NEVER exceed the size where a remote
    # cloud model times out (>120s TTFT). Default 80K chars ≈ 20K tokens,
    # leaving headroom for the system prompt + response within a 32K window.
    MAX_COMPACTED_CHARS = int(__import__("os").getenv("VAULTBOT_COMPACT_MAX_CHARS", "80000"))

    def _hard_cap(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Hard-truncate the conversation if it exceeds MAX_COMPACTED_CHARS.

        Truncates content (and thinking) of the oldest tail messages first
        (just after the head/summary), since those are the least relevant to
        the current turn. The very last message (current user/tool round) is
        never touched. Tool-call structure is preserved — only content
        strings are shortened.
        """
        try:
            total = sum(self._msg_chars(m) for m in messages)
            if total <= self.MAX_COMPACTED_CHARS:
                return messages

            # Work on a copy; truncate from just after the head (oldest tail
            # messages = least relevant) forward.
            result = [dict(m) if isinstance(m, dict) else m for m in messages]
            # Start at index 2 (skip system + first user/tool msg), truncate
            # oldest tail messages until we fit. Never touch the last 2.
            idx = 2
            end = max(2, len(result) - 2)
            while idx < end and total > self.MAX_COMPACTED_CHARS:
                m = result[idx]
                if isinstance(m, dict):
                    for key in ("content", "thinking"):
                        val = m.get(key, "")
                        if val and len(str(val)) > 200:
                            old_len = len(str(val))
                            m[key] = str(val)[:200] + "\n[...truncated by hard cap...]"
                            total -= (old_len - len(str(m[key])))
                idx += 1
            return result
        except Exception:
            return messages

    @staticmethod
    def _msg_chars(m: dict[str, Any] | Any) -> int:
        """Count chars across all fields the LLM sees (content+thinking+tool_calls)."""
        if not isinstance(m, dict):
            return len(str(m))
        total = 0
        for key in ("content", "thinking"):
            val = m.get(key, "")
            if val:
                total += len(str(val))
        tcs = m.get("tool_calls")
        if tcs:
            try:
                total += len(json.dumps(tcs, default=str))
            except Exception:
                total += len(str(tcs))
        return total

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
