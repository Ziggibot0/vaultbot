"""Tests for the user-facing notification helpers (notify_problem, notify_info).

These verify that:
  - notify_problem sends a ``type:"problem"`` WS event with a valid
    ``Diagnosis.to_dict()`` payload (no raw_for_log leaked).
  - notify_problem accepts a raw exception and classifies it via
    ``classify_error``.
  - notify_problem accepts a pre-built Diagnosis.
  - notify_info sends a ``type:"system_info"`` event.
  - Both rate-limit: the same message within 60s is suppressed.
  - A dead websocket / missing manager never raises.

Run: pytest tests/test_notify_helpers.py -v
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from chat_helpers import notify_info, notify_problem
from error_types import Diagnosis, ProblemCategory


def _mock_svc(manager=None, session_logger=None):
    """Build a minimal Services-like mock."""
    svc = MagicMock()
    svc.manager = manager or MagicMock()
    svc.session_logger = session_logger or MagicMock()
    return svc


# A dummy websocket object — notify_problem checks `websocket is not None`
# before sending, so tests need a truthy value.
_WS = object()


@pytest.fixture(autouse=True)
def _clear_dedup():
    """Clear the rate-limit dedup dict between tests."""
    from chat_helpers import _notify_dedup
    _notify_dedup.clear()
    yield
    _notify_dedup.clear()


def _capture_sent(manager):
    """Return the last message sent via send_personal_message."""
    manager.send_personal_message.assert_called()
    call_args = manager.send_personal_message.call_args
    return json.loads(call_args.args[0])


def test_notify_problem_with_raw_exception():
    """notify_problem classifies a raw exception into a Diagnosis."""
    manager = MagicMock()
    manager.send_personal_message = AsyncMock()
    svc = _mock_svc(manager=manager)

    exc = RuntimeError("connection refused")
    asyncio.run(notify_problem(svc, websocket=_WS, exc_or_diagnosis=exc,
                               context={"stage": "test"}))

    msg = _capture_sent(manager)
    assert msg["type"] == "problem"
    assert "diagnosis" in msg
    diag = msg["diagnosis"]
    assert diag["category"] == "ollama_down"
    assert "raw_for_log" not in diag  # never leaked by default


def test_notify_problem_with_prebuilt_diagnosis():
    """notify_problem passes a pre-built Diagnosis through unchanged."""
    manager = MagicMock()
    manager.send_personal_message = AsyncMock()
    svc = _mock_svc(manager=manager)

    pre = Diagnosis(
        category=ProblemCategory.GENERIC,
        user_message="custom message",
        remedy_hint="do the thing",
    )
    asyncio.run(notify_problem(svc, websocket=_WS, exc_or_diagnosis=pre))

    msg = _capture_sent(manager)
    assert msg["type"] == "problem"
    assert msg["diagnosis"]["user_message"] == "custom message"
    assert msg["diagnosis"]["remedy_hint"] == "do the thing"


def test_notify_problem_user_message_override():
    """notify_problem can override the user_message of a classified error."""
    manager = MagicMock()
    manager.send_personal_message = AsyncMock()
    svc = _mock_svc(manager=manager)

    asyncio.run(notify_problem(svc, websocket=_WS,
                               exc_or_diagnosis=RuntimeError("oops"),
                               context={"stage": "test"},
                               user_message="overridden message"))

    msg = _capture_sent(manager)
    assert msg["diagnosis"]["user_message"] == "overridden message"


def test_notify_info_sends_system_info():
    """notify_info sends a type:system_info event."""
    manager = MagicMock()
    manager.send_personal_message = AsyncMock()
    svc = _mock_svc(manager=manager)

    asyncio.run(notify_info(svc, websocket=_WS, message="degradation notice"))

    msg = _capture_sent(manager)
    assert msg["type"] == "system_info"
    assert msg["content"] == "degradation notice"


def test_notify_problem_rate_limited():
    """The same user_message within 60s is suppressed."""
    manager = MagicMock()
    manager.send_personal_message = AsyncMock()
    svc = _mock_svc(manager=manager)

    pre = Diagnosis(category=ProblemCategory.GENERIC, user_message="dup")
    asyncio.run(notify_problem(svc, websocket=_WS, exc_or_diagnosis=pre))
    assert manager.send_personal_message.call_count == 1

    # Second call with the same message -> suppressed.
    asyncio.run(notify_problem(svc, websocket=_WS, exc_or_diagnosis=pre))
    assert manager.send_personal_message.call_count == 1  # still 1


def test_notify_problem_dead_websocket_never_raises():
    """A dead websocket (send raises) must never cascade."""
    manager = MagicMock()
    manager.send_personal_message = AsyncMock(side_effect=ConnectionError("dead"))
    svc = _mock_svc(manager=manager)

    # Should not raise.
    asyncio.run(notify_problem(svc, websocket=None,
                               exc_or_diagnosis=RuntimeError("test")))


def test_notify_problem_missing_manager_never_raises():
    """A None manager must never raise."""
    svc = MagicMock()
    svc.manager = None
    svc.session_logger = MagicMock()

    # Should not raise.
    asyncio.run(notify_problem(svc, websocket=None,
                               exc_or_diagnosis=RuntimeError("test")))


def test_notify_problem_logs_to_session_logger():
    """The problem is logged to session_logger even if the WS send fails."""
    slog = MagicMock()
    manager = MagicMock()
    manager.send_personal_message = AsyncMock(side_effect=ConnectionError("dead"))
    svc = _mock_svc(manager=manager, session_logger=slog)

    asyncio.run(notify_problem(svc, websocket=None,
                               exc_or_diagnosis=RuntimeError("test"),
                               context={"stage": "test"}))

    slog.log.assert_called()
    call_args = slog.log.call_args
    assert call_args.args[0] == "problem_notified"


def test_notify_info_rate_limited():
    """The same info message within 60s is suppressed."""
    manager = MagicMock()
    manager.send_personal_message = AsyncMock()
    svc = _mock_svc(manager=manager)

    asyncio.run(notify_info(svc, websocket=_WS, message="dup info"))
    asyncio.run(notify_info(svc, websocket=_WS, message="dup info"))
    assert manager.send_personal_message.call_count == 1