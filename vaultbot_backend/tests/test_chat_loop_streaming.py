"""Focused tests for live prose streaming (answer_chunk).

History: the provenance gates buffered model prose until answer_done, which
made the UI show thinking/tool activity for an entire turn with zero words.
The gates are removed (v1.5.4 removed the truth-gap swap, v1.5.6 removed the
grounding retry), so prose streamslive again — the UI already renders
answer_chunk deltas and replaces them with the final answer at answer_done.
"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from chat_loop_state import TurnState
from chat_loop_streaming import stream_llm_round

pytestmark = pytest.mark.unit


def _make_svc(chunks):
    class Client:
        def chat(self, messages, tools, stream):
            yield from chunks
            yield {"done": True}

    manager = SimpleNamespace(send_personal_message=AsyncMock())
    svc = SimpleNamespace(ollama_client=Client(), manager=manager)
    logger = SimpleNamespace(log=lambda *args, **kwargs: None)
    return svc, logger


def _run(svc, logger):
    state = TurnState()
    state._model_conversation = [{"role": "user", "content": "question"}]
    state._last_partial_write_s = time.time()

    async def run_stream():
        return await stream_llm_round(
            svc,
            object(),
            logger,
            asyncio.get_running_loop(),
            "question",
            [],
            state,
        )

    return asyncio.run(run_stream())


def _payloads(manager):
    return [
        json.loads(call.args[0])
        for call in manager.send_personal_message.call_args_list
    ]


def test_answer_chunks_are_streamed_live():
    svc, logger = _make_svc(
        [
            {"response": "Hello ", "thinking": "", "tool_calls": []},
            {"response": "world.", "thinking": "", "tool_calls": []},
        ]
    )
    manager = svc.manager

    text, _, _, _, _ = _run(svc, logger)

    assert text == "Hello world."
    payloads = _payloads(manager)
    chunks = [p for p in payloads if p.get("type") == "answer_chunk"]
    assert [p["content"] for p in chunks] == ["Hello ", "world."]


def test_stream_failure_never_kills_the_llm_stream():
    svc, logger = _make_svc([{"response": "Hi.", "thinking": "", "tool_calls": []}])

    async def boom(*args, **kwargs):
        raise RuntimeError("socket closed")

    svc.manager.send_personal_message = boom

    text, _, _, _, _ = _run(svc, logger)
    assert text == "Hi."


def test_tool_call_rounds_emit_no_answer_chunks_for_tool_only_output():
    svc, logger = _make_svc(
        [
            {"response": "", "thinking": "", "tool_calls": [{"id": "1"}]},
            {"response": "done", "thinking": "", "tool_calls": []},
        ]
    )
    manager = svc.manager

    _run(svc, logger)

    chunks = [p for p in _payloads(manager) if p.get("type") == "answer_chunk"]
    assert [p["content"] for p in chunks] == ["done"]
