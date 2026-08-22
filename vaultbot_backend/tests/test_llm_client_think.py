"""Regression tests for OpenAICompatibleClient.chat — the ``think=False``
reasoning toggle (issue #137, live-run follow-up).

The original #137 fix added ``max_predict`` + ``timeout`` to the injected
``llm_generate`` wrapper, but the small model (Lemonade ``Qwen3-8B-Hybrid``,
a reasoning model) still spent its whole budget on chain-of-thought before
answering — a one-line entailment judgment took ~13s per claim and the
per-call 30s timeout was the only thing stopping a full hang.

The fix: ``OpenAICompatibleClient.chat`` now maps ``think=False`` to
``enable_thinking: false`` in the request payload, which Lemonade (and other
reasoning-model backends) honor to skip reasoning. This test guards against
a future refactor that drops that mapping.
"""

from __future__ import annotations

import pytest
from llm_client import OpenAICompatibleClient

pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"verdict": "supported", "reasoning": "ok"}',
                        "reasoning": "",
                    }
                }
            ]
        }


class TestThinkDisablesReasoning:
    """issue #137 — think=False must disable reasoning on OpenAI-compat backends."""

    def test_think_false_sets_enable_thinking_false(self, monkeypatch):
        captured: dict = {}

        def fake_post(url, **kwargs):
            captured["json"] = kwargs.get("json")
            return _FakeResponse(kwargs.get("json") or {})

        monkeypatch.setattr("llm_client.requests.post", fake_post)

        client = OpenAICompatibleClient(
            base_url="http://localhost:13305",
            api_key="",
            llm_model="Qwen3-8B-Hybrid",
        )
        client.chat(
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            think=False,
            max_predict=256,
        )

        assert captured["json"]["enable_thinking"] is False
        assert captured["json"]["max_tokens"] == 256

    def test_think_none_omits_enable_thinking(self, monkeypatch):
        captured: dict = {}

        def fake_post(url, **kwargs):
            captured["json"] = kwargs.get("json")
            return _FakeResponse(kwargs.get("json") or {})

        monkeypatch.setattr("llm_client.requests.post", fake_post)

        client = OpenAICompatibleClient(
            base_url="http://localhost:13305",
            api_key="",
            llm_model="Qwen3-8B-Hybrid",
        )
        client.chat(messages=[{"role": "user", "content": "hi"}], stream=False)

        # think=None (default) must NOT inject the field — big-cartridge
        # synthesis keeps reasoning.
        assert "enable_thinking" not in captured["json"]
