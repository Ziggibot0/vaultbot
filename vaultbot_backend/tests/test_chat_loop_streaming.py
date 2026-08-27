"""Focused tests for buffering unverified model prose."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from chat_loop_state import TurnState
from chat_loop_streaming import stream_llm_round

pytestmark = pytest.mark.unit


def test_answer_chunks_are_buffered_until_finalization():
    class Client:
        def chat(self, messages, tools, stream):
            yield {"response": "Unsupported draft.", "thinking": "", "tool_calls": []}
            yield {"done": True}

    manager = SimpleNamespace(send_personal_message=AsyncMock())
    svc = SimpleNamespace(ollama_client=Client(), manager=manager)
    logger = SimpleNamespace(log=lambda *args, **kwargs: None)
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

    text, _, _, _, _ = asyncio.run(run_stream())

    payloads = [
        json.loads(call.args[0]) for call in manager.send_personal_message.calls
    ]
    assert text == "Unsupported draft."
    assert all(payload.get("type") != "answer_chunk" for payload in payloads)
