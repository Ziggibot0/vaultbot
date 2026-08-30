"""Focused tests for the extracted vault_research tool handler."""

import asyncio
from types import SimpleNamespace

import chat_research_tool as research_tool
import pytest

pytestmark = pytest.mark.unit


class _FakeLogger:
    def __init__(self):
        self.events = []
        self.exceptions = []

    def log(self, event, data=None):
        self.events.append((event, data))

    def log_exception(self, exc, context=""):
        self.exceptions.append((exc, context))


def _service(**overrides):
    values = {
        "vault_graph": SimpleNamespace(refresh=lambda: None),
        "research_engine": SimpleNamespace(
            max_rounds=4,
            max_follow_ups=3,
            progress_callback=None,
            research=lambda *args: None,
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_subagent_success_refreshes_graph_and_logs_events(monkeypatch):
    logger = _FakeLogger()
    refreshes = []
    brief = {
        "status": "ok",
        "topic": "graph databases",
        "source_count": 2,
        "note_path": "Research/graph-databases.md",
    }

    async def fake_heartbeat(*args):
        return brief

    monkeypatch.setattr(research_tool, "subagent_enabled", lambda: True)
    monkeypatch.setattr(research_tool, "run_with_heartbeat", fake_heartbeat)
    service = _service(
        vault_graph=SimpleNamespace(refresh=lambda: refreshes.append(True))
    )

    result = asyncio.run(
        research_tool.execute_vault_research(
            service, {"topic": "graph databases"}, logger
        )
    )

    assert result == brief
    assert refreshes == [True]
    assert [event for event, _ in logger.events] == [
        "subagent_research_invoked",
        "subagent_research_complete",
    ]
    assert logger.events[-1][1]["status"] == "ok"
    assert logger.events[-1][1]["source_count"] == 2


def test_subagent_empty_status_gets_error(monkeypatch):
    logger = _FakeLogger()

    async def fake_heartbeat(*args):
        return {"status": "empty", "source_count": 0}

    monkeypatch.setattr(research_tool, "subagent_enabled", lambda: True)
    monkeypatch.setattr(research_tool, "run_with_heartbeat", fake_heartbeat)

    result = asyncio.run(
        research_tool.execute_vault_research(
            _service(), {"topic": "missing sources"}, logger
        )
    )

    assert result["status"] == "empty"
    assert result["error"] == "no web sources found"


def test_subagent_exception_returns_error(monkeypatch):
    logger = _FakeLogger()

    async def failing_heartbeat(*args):
        raise RuntimeError("subprocess stopped")

    monkeypatch.setattr(research_tool, "subagent_enabled", lambda: True)
    monkeypatch.setattr(research_tool, "run_with_heartbeat", failing_heartbeat)

    result = asyncio.run(
        research_tool.execute_vault_research(
            _service(), {"topic": "failure handling"}, logger
        )
    )

    assert result == {
        "status": "error",
        "error": "subagent research failed: subprocess stopped",
        "subagent": True,
    }
    assert logger.exceptions[0][1] == "subagent_research"


def test_in_process_exception_restores_engine_state_and_logs_done(monkeypatch):
    logger = _FakeLogger()
    original_callback = object()
    engine = SimpleNamespace(
        max_rounds=7,
        max_follow_ups=5,
        progress_callback=original_callback,
        research=lambda *args: None,
    )

    async def failing_heartbeat(*args):
        raise RuntimeError("research failed")

    monkeypatch.setattr(research_tool, "subagent_enabled", lambda: False)
    monkeypatch.setattr(research_tool, "run_with_heartbeat", failing_heartbeat)

    with pytest.raises(RuntimeError, match="research failed"):
        asyncio.run(
            research_tool.execute_vault_research(
                _service(research_engine=engine),
                {"topic": "state restoration", "depth": "quick"},
                logger,
                websocket=object(),
            )
        )

    assert engine.max_rounds == 7
    assert engine.max_follow_ups == 5
    assert engine.progress_callback is original_callback
    done_events = [
        data for event, data in logger.events if event == "agent_research_done"
    ]
    assert len(done_events) == 1
    assert done_events[0]["source_count"] == 0
