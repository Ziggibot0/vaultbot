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


class _RecordingLogger:
    def __init__(self):
        self.invocations = []

    def log(self, event, data=None):
        pass

    def log_tool_call(self, *args, **data):
        pass

    def log_message(self, direction, payload):
        pass

    def log_exception(self, exc=None, context=None):
        pass

    def add_token_usage(self, prompt_tokens, completion_tokens):
        pass

    def log_llm_invocation(self, **data):
        self.invocations.append(data)


class _StreamingResponse(_FakeResponse):
    def iter_lines(self):
        yield b'data: {"choices":[{"delta":{"content":"hello"}}]}'
        yield b'data: {"choices":[{"delta":{"content":" world"}}]}'


class TestInvocationOutcomes:
    def test_failed_request_emits_one_invocation(self, monkeypatch):
        logger = _RecordingLogger()

        def fail_post(url, **kwargs):
            raise RuntimeError("offline")

        monkeypatch.setattr("llm_client.requests.post", fail_post)
        client = OpenAICompatibleClient(
            base_url="http://localhost:13305",
            api_key="",
            llm_model="test-model",
            session_logger=logger,
        )

        with pytest.raises(RuntimeError, match="offline"):
            client.chat(messages=[{"role": "user", "content": "hello"}])

        assert len(logger.invocations) == 1
        assert logger.invocations[0]["outcome"] == "failed"
        assert logger.invocations[0]["token_source"] == "estimated"

    def test_closed_stream_emits_one_cancelled_invocation(self, monkeypatch):
        logger = _RecordingLogger()
        monkeypatch.setattr(
            "llm_client.requests.post",
            lambda url, **kwargs: _StreamingResponse(kwargs.get("json") or {}),
        )
        client = OpenAICompatibleClient(
            base_url="http://localhost:13305",
            api_key="",
            llm_model="test-model",
            session_logger=logger,
        )

        stream = client.chat(
            messages=[{"role": "user", "content": "hello"}], stream=True
        )
        assert next(stream)["response"] == "hello"
        stream.close()

        assert len(logger.invocations) == 1
        assert logger.invocations[0]["outcome"] == "cancelled"


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
