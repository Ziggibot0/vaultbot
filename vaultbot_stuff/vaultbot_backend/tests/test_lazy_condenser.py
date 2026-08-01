"""Tests for the lazy-condenser LLM response parsing fix.

The lazy condenser calls ``get_small_client_or_big().chat()`` and must read
the response from the ``"response"`` key (the LLMClient.chat contract).  The
old code read ``resp["message"]["content"]`` which is the raw Ollama
/api/chat shape, not what LLMClient returns — every condense silently
returned empty text, making the entire feature dead.

These tests stub the LLM client and verify the condenser extracts the text
correctly from the real response shape.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch



class _StubClient:
    """A minimal LLM client stub that returns a fixed response dict.

    Mimics the LLMClient.chat() contract:
    stream=False -> {"response": str, "thinking": str, "tool_calls": list}
    """

    def __init__(self, response_text: str):
        self._response_text = response_text

    def chat(self, messages, temperature=0.2, stream=False):
        return {
            "response": self._response_text,
            "thinking": "",
            "tool_calls": [],
        }


def _make_long_note(tmp_path: Path) -> Path:
    """Create a fixture note long enough to pass CONDENSE_MIN_CHARS."""
    note_path = tmp_path / "Long-Note.md"
    body = "This is a long note with content. " * 400  # ~14K chars
    note_path.write_text(
        f"# Long Note\n\n{body}\n\n---\n**Navigation:** [[Home]]\n",
        encoding="utf-8",
    )
    return note_path


def test_condense_extracts_response_key_from_chat(tmp_path):
    """The condenser must read resp["response"], not resp["message"]["content"].

    This is the exact bug that killed the feature: the old code read
    ["message"]["content"] which KeyError'd on the LLMClient.chat return
    shape ({"response": ...}), so text="" on every condense call.
    """
    from lazy_condenser import LazyCondenser

    note_path = _make_long_note(tmp_path)
    # Must be >= CONDENSE_FLOOR_CHARS (1500) or the auto-mode fallback
    # will discard it as "condensed too short" and use extractive instead.
    condensed_text = (
        "This is the condensed version. " * 60  # ~1800 chars, above the floor
        + "It preserves the [[Home]] wikilink."
    )
    stub = _StubClient(condensed_text)

    # The import is deferred (inside _llm_condense), so patch llm_client.
    with patch("llm_client.get_small_client_or_big", return_value=stub):
        condenser = LazyCondenser(
            vault_path=str(tmp_path), ollama_client=MagicMock())
        result = condenser.condense_note(str(note_path))

    # The condensed text must be extracted correctly — not empty.
    assert result["condensed"] is True, \
        f"condense should succeed, got: {result}"
    # The note should now contain the condensed text, not the original long body.
    new_text = note_path.read_text(encoding="utf-8")
    assert "condensed version" in new_text, \
        "the LLM's response text should be written to the note"


def test_condense_returns_empty_on_empty_response(tmp_path):
    """When the LLM returns an empty response, the condenser should
    handle it gracefully (not crash with KeyError on ["message"])."""
    from lazy_condenser import LazyCondenser

    note_path = _make_long_note(tmp_path)
    stub = _StubClient("")  # empty response

    with patch("llm_client.get_small_client_or_big", return_value=stub):
        condenser = LazyCondenser(
            vault_path=str(tmp_path), ollama_client=MagicMock())
        result = condenser.condense_note(str(note_path))

    # Must not crash — empty response handled gracefully.
    assert result is not None
    assert result["error"] is None or result["condensed"] is False, \
        f"empty response should not crash, got: {result}"