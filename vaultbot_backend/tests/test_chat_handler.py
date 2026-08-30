"""Behavioral tests for the top-level chat handler orchestration."""

from __future__ import annotations

import asyncio
import tempfile
from types import SimpleNamespace

import chat_handler
import pytest
from working_memory import TaskList

pytestmark = pytest.mark.unit


class _FakeWebSocket:
    def __init__(self) -> None:
        self.session_id = None
        self._cancelled = True
        self.working_memory = None


class _FakeSessionLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def log(self, event: str, payload: object = None) -> None:
        self.calls.append((event, payload))


def _services() -> SimpleNamespace:
    return SimpleNamespace(chat_checkpointer=None)


def _prep_result() -> tuple:
    loop = asyncio.get_running_loop()
    return (
        [{"role": "user", "content": "hello"}],
        [],
        "system prompt",
        [],
        [],
        [],
        [],
        loop.time(),
        loop,
        set(),
    )


def test_handle_chat_initializes_turn_and_short_circuits_trivial_turn(
    monkeypatch,
) -> None:
    prepare_calls = []

    async def fake_prepare(*args):
        prepare_calls.append(args)
        return None

    async def fail_if_called(*args, **kwargs):
        pytest.fail("agentic loop must not run for a trivial turn")

    monkeypatch.setattr(chat_handler, "_prepare_turn", fake_prepare)
    monkeypatch.setattr(chat_handler, "run_agentic_loop", fail_if_called)
    websocket = _FakeWebSocket()
    logger = _FakeSessionLogger()

    asyncio.run(chat_handler.handle_chat(_services(), websocket, "hello", logger))

    assert logger.calls[0] == ("chat_begin", {"user_message": "hello"})
    assert websocket._cancelled is False
    assert isinstance(websocket.working_memory, TaskList)
    assert len(prepare_calls) == 1


def test_handle_chat_runs_normal_turn_pipeline_once(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    observed_state = None

    async def fake_prepare(*args):
        calls.append("prepare")
        return _prep_result()

    async def fake_loop(*args):
        nonlocal observed_state
        calls.append("loop")
        observed_state = next(
            arg for arg in args if isinstance(arg, chat_handler.TurnState)
        )
        observed_state.final_answer = "draft answer"

    async def fake_finalize(*args):
        calls.append("finalize")
        assert "draft answer" in args
        return "final answer"

    async def fake_background(*args):
        calls.append("background")
        assert "final answer" in args

    monkeypatch.setattr(chat_handler, "_prepare_turn", fake_prepare)
    monkeypatch.setattr(chat_handler, "run_agentic_loop", fake_loop)
    monkeypatch.setattr(chat_handler, "_finalize_turn", fake_finalize)
    monkeypatch.setattr(chat_handler, "_run_background_tasks", fake_background)
    monkeypatch.setattr(chat_handler, "write_partial", lambda *args: None)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    asyncio.run(
        chat_handler.handle_chat(
            _services(), _FakeWebSocket(), "research this", _FakeSessionLogger()
        )
    )

    assert observed_state is not None
    assert calls == ["prepare", "loop", "finalize", "background"]


def test_handle_chat_clears_completed_plan_before_preparation(monkeypatch) -> None:
    working_memory = TaskList()
    working_memory.set_plan("old goal", ["done step"])
    working_memory.update_task("1", status="completed")
    websocket = _FakeWebSocket()
    websocket.working_memory = working_memory
    logger = _FakeSessionLogger()

    async def fake_prepare(*args):
        prepared_memory = next(arg for arg in args if isinstance(arg, TaskList))
        assert not prepared_memory.has_plan()
        return None

    monkeypatch.setattr(chat_handler, "_prepare_turn", fake_prepare)

    asyncio.run(chat_handler.handle_chat(_services(), websocket, "next", logger))

    assert not working_memory.has_plan()
    assert "wm_plan_cleared_completed" in [event for event, _ in logger.calls]
