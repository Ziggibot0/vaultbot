from __future__ import annotations

import pytest
from chat_turn_prep import (
    _append_current_session_temporal_guard,
    _current_session_log_stems,
)

pytestmark = pytest.mark.unit


def test_current_session_log_stems_detects_active_session_paths() -> None:
    results = [
        {
            "file_path": (
                "C:/repo/myvault/vaultbot-stuff/Memory/Logs/sid-123/event-1.md"
            )
        },
        {
            "file_path": (
                "C:/repo/myvault/vaultbot-stuff/Memory/Logs/sid-123/event-2.md"
            )
        },
        {
            "file_path": (
                "C:/repo/myvault/vaultbot-stuff/Memory/Logs/other-sid/event-3.md"
            )
        },
    ]

    out = _current_session_log_stems(results, "sid-123")

    assert out == ["event-1", "event-2"]


def test_current_session_log_stems_handles_backslashes_and_duplicates() -> None:
    results = [
        {
            "file_path": (
                "C:\\repo\\myvault\\vaultbot-stuff\\Memory\\Logs\\SID-123\\event-1.md"
            )
        },
        {
            "file_path": (
                "C:\\repo\\myvault\\vaultbot-stuff\\Memory\\Logs\\sid-123\\event-1.md"
            )
        },
    ]

    out = _current_session_log_stems(results, "sid-123")

    assert out == ["event-1"]


def test_append_temporal_guard_only_when_stems_present() -> None:
    base = "VAULT CONTEXT"

    unchanged = _append_current_session_temporal_guard(base, [])
    guarded = _append_current_session_temporal_guard(base, ["event-1", "event-2"])

    assert unchanged == base
    assert "TEMPORAL GUARD (CURRENT SESSION EVIDENCE)" in guarded
    assert "event-1, event-2" in guarded
    assert "last time" in guarded
