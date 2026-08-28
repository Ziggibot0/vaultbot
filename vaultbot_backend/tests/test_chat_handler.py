"""Tests for chat_handler.handle_chat — the top-level chat entry point.

Stubs out the heavy neighbours (_prepare_turn, run_agentic_loop,
_finalize_turn, _run_background_tasks) so the test exercises handle_chat's
own orchestration logic without touching FAISS, Ollama, WebSocket, or vault
files.  Each test follows Arrange → Act → Assert.

The faiss shim is installed at module level (same technique as
test_fused_retrieval.py) so the import chain vault_indexer → faiss is
satisfied without the broken native extension.

Leaf-module imports only — ``import main`` is hard-fenced by conftest.py.
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# FAISS shim — vault_indexer (imported transitively by chat_handler) needs
# the faiss module to be present at import time even though these tests
# never touch a real index.
# ---------------------------------------------------------------------------
if "faiss" not in sys.modules:

    class _StubIndexFlatL2:
        def __init__(self, dim: int = 4, *args, **kwargs):
            self.d = dim
            self.ntotal = 0

    class _StubIndexIDMap2:
        def __init__(self, inner=None, *args, **kwargs):
            self.inner = inner
            self.d = getattr(inner, "d", 4)
            self.ntotal = 0

        def add_with_ids(self, *args, **kwargs):
            return None

    _faiss_stub = types.ModuleType("faiss")
    _faiss_stub.IndexFlatL2 = _StubIndexFlatL2
    _faiss_stub.IndexIDMap2 = _StubIndexIDMap2
    _faiss_stub.read_index = lambda *a, **k: None
    _faiss_stub.write_index = lambda *a, **k: None
    _faiss_stub.normalize_L2 = lambda v: None
    sys.modules["faiss"] = _faiss_stub

import chat_handler  # noqa: E402 — must follow the faiss shim

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    """Minimal WebSocket substitute — no network, no handshake."""

    def __init__(self):
        # None → no ChatLoopCheckpointer, no filesystem side effects
        self.session_id = None
        self._cancelled = False
        self.working_memory = None  # set by handle_chat on first use

    async def send_text(self, data: str) -> None:  # noqa: ARG002
        pass

    async def receive_text(self) -> str:
        return ""


class _FakeSessionLogger:
    """Captures log() calls so tests can assert on them."""

    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def log(self, event: str, payload: object = None) -> None:
        self.calls.append((event, payload))

    def log_tool_call(self, **kwargs) -> None:  # noqa: ARG002
        pass


def _make_services() -> SimpleNamespace:
    """Minimal Services namespace; only fields accessed before _prepare_turn."""
    return SimpleNamespace(
        chat_checkpointer=None,  # disables checkpoint resume path
    )


# ---------------------------------------------------------------------------
# Helper: builds the 10-element tuple that _prepare_turn returns on success.
# ---------------------------------------------------------------------------
def _fake_prep_result():
    loop = asyncio.get_event_loop()
    return (
        [{"role": "user", "content": "hi"}],  # conversation
        [],  # results
        "system prompt",  # _system_prompt
        [],  # all_tools
        [],  # custom_schemas
        [],  # procedures_in_context
        [],  # retrieved_paths
        loop.time(),  # chat_start_time
        loop,  # loop
        set(),  # allowed_citations
    )


# ---------------------------------------------------------------------------
# Test 1: handle_chat logs "chat_begin" as the very first action
# ---------------------------------------------------------------------------
def test_handle_chat_logs_chat_begin(monkeypatch):
    # Arrange: patch _prepare_turn to return None (trivial-turn shortcut).
    async def _fake_prepare(svc, ws, msg, sl, wm, cp, hist):  # noqa: PLR0913
        return None

    monkeypatch.setattr(chat_handler, "_prepare_turn", _fake_prepare)

    logger = _FakeSessionLogger()
    ws = _FakeWebSocket()
    svc = _make_services()

    # Act
    asyncio.run(chat_handler.handle_chat(svc, ws, "hello", logger))

    # Assert: "chat_begin" is always the first logged event.
    assert logger.calls, "no log calls recorded"
    first_event = logger.calls[0][0]
    assert first_event == "chat_begin"


# ---------------------------------------------------------------------------
# Test 2: trivial turn — _prepare_turn returns None → run_agentic_loop skipped
# ---------------------------------------------------------------------------
def test_handle_chat_trivial_turn_skips_loop(monkeypatch):
    # Arrange
    loop_called = []

    async def _fake_prepare(svc, ws, msg, sl, wm, cp, hist):  # noqa: PLR0913
        return None  # signals trivial turn

    async def _fake_loop(*args, **kwargs):
        loop_called.append(True)

    monkeypatch.setattr(chat_handler, "_prepare_turn", _fake_prepare)
    monkeypatch.setattr(chat_handler, "run_agentic_loop", _fake_loop)

    logger = _FakeSessionLogger()
    ws = _FakeWebSocket()
    svc = _make_services()

    # Act
    asyncio.run(chat_handler.handle_chat(svc, ws, "trivial", logger))

    # Assert: agentic loop was never entered.
    assert not loop_called, "run_agentic_loop must not be called on a trivial turn"


# ---------------------------------------------------------------------------
# Test 3: normal turn — _prepare_turn succeeds → run_agentic_loop IS called
# ---------------------------------------------------------------------------
def test_handle_chat_normal_turn_calls_loop(monkeypatch, tmp_path):
    # Arrange
    import tempfile

    loop_called = []

    async def _fake_prepare(svc, ws, msg, sl, wm, cp, hist):  # noqa: PLR0913
        return _fake_prep_result()

    async def _fake_loop(*args, **kwargs):
        loop_called.append(True)

    async def _fake_finalize(*args, **kwargs):
        return "The answer."

    async def _fake_bg(*args, **kwargs):
        pass

    monkeypatch.setattr(chat_handler, "_prepare_turn", _fake_prepare)
    monkeypatch.setattr(chat_handler, "run_agentic_loop", _fake_loop)
    monkeypatch.setattr(chat_handler, "_finalize_turn", _fake_finalize)
    monkeypatch.setattr(chat_handler, "_run_background_tasks", _fake_bg)
    # Prevent write_partial from touching the real filesystem.
    monkeypatch.setattr(
        chat_handler, "write_partial", lambda *a, **k: None, raising=False
    )
    # Redirect any tempfile.gettempdir() calls inside chat_handler to tmp_path.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    logger = _FakeSessionLogger()
    ws = _FakeWebSocket()
    svc = _make_services()

    # Act
    asyncio.run(chat_handler.handle_chat(svc, ws, "tell me about X", logger))

    # Assert: agentic loop was entered exactly once.
    assert len(loop_called) == 1, (
        "run_agentic_loop must be called once on a normal turn"
    )


# ---------------------------------------------------------------------------
# Test 4: working memory is initialised on a fresh WebSocket
# ---------------------------------------------------------------------------
def test_handle_chat_initialises_working_memory(monkeypatch):
    # Arrange: websocket has no working_memory yet.
    async def _fake_prepare(svc, ws, msg, sl, wm, cp, hist):  # noqa: PLR0913
        return None

    monkeypatch.setattr(chat_handler, "_prepare_turn", _fake_prepare)

    from working_memory import TaskList

    logger = _FakeSessionLogger()
    ws = _FakeWebSocket()
    ws.working_memory = None  # explicitly unset
    svc = _make_services()

    # Act
    asyncio.run(chat_handler.handle_chat(svc, ws, "hi", logger))

    # Assert: handle_chat installed a TaskList on the websocket.
    assert isinstance(ws.working_memory, TaskList)


# ---------------------------------------------------------------------------
# Test 5: completed working-memory plan is cleared before the turn starts
# ---------------------------------------------------------------------------
def test_handle_chat_clears_completed_plan(monkeypatch):
    # Arrange: websocket carries a fully-done plan from a prior turn.
    async def _fake_prepare(svc, ws, msg, sl, wm, cp, hist):  # noqa: PLR0913
        return None

    monkeypatch.setattr(chat_handler, "_prepare_turn", _fake_prepare)

    from working_memory import TaskList

    wm = TaskList()
    wm.set_plan("old goal", ["step one"])
    wm.update_task("1", status="completed")
    assert wm.has_plan() and wm.all_done()  # guard

    logger = _FakeSessionLogger()
    ws = _FakeWebSocket()
    ws.working_memory = wm
    svc = _make_services()

    # Act
    asyncio.run(chat_handler.handle_chat(svc, ws, "new question", logger))

    # Assert: the old completed plan is gone.
    assert not ws.working_memory.has_plan(), "completed plan must be cleared"

    # A "wm_plan_cleared_completed" event must have been logged.
    events = [e for e, _ in logger.calls]
    assert "wm_plan_cleared_completed" in events
