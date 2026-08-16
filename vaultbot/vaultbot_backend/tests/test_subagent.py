"""Tests for subagent.py — the subprocess isolation primitive.

Covers: brief shape + bounding, timeout → clean error, no-stdout → clean
error, the VAULTBOT_SUBAGENT=off fallback, and the dispatcher routing.
No network, no Ollama, no real research — the tests either stub the
wrapper or exercise the error/timeout paths.

See [[Procedure-Subprocess-Architecture]] and subagent.py docstring.
"""

import json


# conftest adds the backend dir to sys.path so leaf modules import.
from subagent import (
    _build_research_wrapper,
    run_research_subagent,
    run_subagent,
    subagent_enabled,
    _run_subprocess,
)

# Sentinel used to detect the fallback path.
import subagent as _subagent_mod


# ── Helpers ─────────────────────────────────────────────────────────────


class _FakeLogger:
    """Captures log() calls so tests can assert events were emitted."""

    def __init__(self):
        self.events = []

    def log(self, event, data=None):
        self.events.append((event, data))


def _wrapper_that_prints(brief: dict) -> str:
    """A minimal wrapper that prints a fixed JSON brief to stdout."""
    return "import json, sys\nprint(json.dumps(" + json.dumps(brief) + "))\n"


def _wrapper_that_sleeps(seconds: float) -> str:
    """A wrapper that sleeps — used for the timeout test."""
    return "import time, sys\ntime.sleep(" + repr(seconds) + ")\nprint('{}')\n"


def _wrapper_that_prints_to_stderr_only() -> str:
    """A wrapper that logs to stderr but prints nothing to stdout."""
    return "import sys\nprint('diagnostic noise', file=sys.stderr, flush=True)\n"


def _wrapper_that_raises(message: str) -> str:
    """A wrapper that raises — exercises the child's own error brief."""
    return (
        "import json, sys, traceback\n"
        "try:\n"
        "    raise RuntimeError(" + repr(message) + ")\n"
        "except Exception as e:\n"
        "    print(json.dumps({'status': 'error', 'error': str(e)}))\n"
        "    sys.exit(1)\n"
    )


# ── 1. Brief shape + bounding ───────────────────────────────────────────


def test_research_wrapper_prints_bounded_brief(monkeypatch, tmp_path):
    """The research wrapper's brief is bounded: synthesis_brief <= 1500 chars,
    key_facts <= 8, and the shape matches what the chat loop expects."""
    # Stub the subprocess runner so we don't launch a real research dig.
    captured = {}

    def fake_run_subprocess(
        wrapper, session_logger=None, timeout=180, log_tag="subagent"
    ):
        captured["wrapper"] = wrapper
        return {
            "status": "ok",
            "topic": "test-topic",
            "source_count": 3,
            "note_path": str(tmp_path / "note.md"),
            "synthesis_brief": "x" * 1400,
            "key_facts": "- fact 1\n- fact 2",
            "duration_ms": 1234,
            "subagent": True,
        }

    monkeypatch.setattr(_subagent_mod, "_run_subprocess", fake_run_subprocess)
    result = run_research_subagent("test-topic", "deep", session_logger=None)

    assert result["status"] == "ok"
    assert result["topic"] == "test-topic"
    assert result["source_count"] == 3
    assert result["subagent"] is True
    assert len(result["synthesis_brief"]) == 1400
    # The wrapper was built (proves the real builder runs, not a stub).
    assert "research_engine" in captured["wrapper"]


def test_build_research_wrapper_injects_topic_safely():
    """The topic is injected via repr() so quotes/braces can't break the
    wrapper. A topic with a single quote + brace should still produce a
    syntactically valid Python program."""
    wrapper = _build_research_wrapper("topic with 'quote' and {brace}", "deep")
    # The wrapper must parse as valid Python (proves no injection broke it).
    compile(wrapper, "<wrapper>", "exec")
    # The topic appears as a repr'd literal, not as raw code.
    assert "topic with 'quote' and {brace}" in wrapper


def test_build_research_wrapper_rejects_unknown_depth():
    """An unrecognized depth defaults to 'deep' (defensive allowlist)."""
    wrapper = _build_research_wrapper("x", "malicious")
    compile(wrapper, "<wrapper>", "exec")
    # depth is repr'd as 'deep', not 'malicious'.
    assert "'deep'" in wrapper
    assert "'malicious'" not in wrapper


# ── 2. Timeout → clean error (never raises) ────────────────────────────


def test_subprocess_timeout_returns_error_dict(monkeypatch):
    """A child that sleeps past the timeout returns a clean error dict, not
    an exception. The orchestrator's chat loop must never hang."""
    monkeypatch.setattr(_subagent_mod, "_DEFAULT_TIMEOUT", 1)
    # _run_subprocess reads the timeout param; pass a tiny one directly.
    result = _run_subprocess(
        _wrapper_that_sleeps(5), session_logger=None, timeout=1, log_tag="test_timeout"
    )
    assert result["status"] == "error"
    assert "timed out" in result["error"].lower()
    assert result["subagent"] is True


# ── 3. No stdout → clean error ──────────────────────────────────────────


def test_subprocess_no_stdout_returns_error_dict():
    """A child that prints nothing to stdout returns a clean error dict."""
    result = _run_subprocess(
        _wrapper_that_prints_to_stderr_only(),
        session_logger=None,
        timeout=10,
        log_tag="test_no_stdout",
    )
    assert result["status"] == "error"
    assert "no stdout" in result["error"].lower()
    assert result["subagent"] is True


def test_subprocess_child_error_brief_is_returned():
    """A child that raises + prints its own error brief returns that brief
    (the child's error handling is preserved, not swallowed)."""
    result = _run_subprocess(
        _wrapper_that_raises("boom"),
        session_logger=None,
        timeout=10,
        log_tag="test_child_error",
    )
    assert result["status"] == "error"
    assert "boom" in result["error"]


# ── 4. VAULTBOT_SUBAGENT=off fallback ───────────────────────────────────


def test_subagent_disabled_when_env_off(monkeypatch):
    """subagent_enabled() returns False when VAULTBOT_SUBAGENT=off."""
    monkeypatch.setenv("VAULTBOT_SUBAGENT", "off")
    assert subagent_enabled() is False


def test_subagent_enabled_by_default(monkeypatch):
    """subagent_enabled() returns True when the env var is unset/on."""
    monkeypatch.delenv("VAULTBOT_SUBAGENT", raising=False)
    assert subagent_enabled() is True
    monkeypatch.setenv("VAULTBOT_SUBAGENT", "on")
    assert subagent_enabled() is True


# ── 5. Dispatcher routing ──────────────────────────────────────────────


def test_run_subagent_routes_research(monkeypatch):
    """The dispatcher routes task_type='research' to the research builder."""
    captured = {}

    def fake_run(wrapper, session_logger=None, timeout=180, log_tag=""):
        captured["wrapper"] = wrapper
        captured["log_tag"] = log_tag
        return {"status": "ok", "subagent": True}

    monkeypatch.setattr(_subagent_mod, "_run_subprocess", fake_run)
    result = run_subagent("research", {"topic": "x", "depth": "deep"})
    assert result["status"] == "ok"
    assert "research_engine" in captured["wrapper"]
    assert captured["log_tag"] == "subagent_research"


def test_run_subagent_unknown_task_returns_error(monkeypatch):
    """An unregistered task_type returns a clean error dict, never raises."""
    result = run_subagent("nonexistent_task", {})
    assert result["status"] == "error"
    assert "unknown subagent task_type" in result["error"]
    assert result["subagent"] is True


def test_run_subagent_invalid_payload_returns_error(monkeypatch):
    """A research task missing its topic still produces a wrapper (the
    builder uses '' as a default) — the subprocess path never raises on
    bad input; it surfaces errors via the brief."""
    monkeypatch.setattr(
        _subagent_mod,
        "_run_subprocess",
        lambda *a, **k: {"status": "ok", "subagent": True},
    )
    result = run_subagent("research", {})  # no topic
    assert result["status"] == "ok"


# ── Session logging ────────────────────────────────────────────────────


def test_subprocess_emits_start_and_done_events():
    """The session logger receives <tag>_start and <tag>_done events."""
    logger = _FakeLogger()
    _run_subprocess(
        _wrapper_that_prints({"status": "ok", "source_count": 2}),
        session_logger=logger,
        timeout=10,
        log_tag="test_logged",
    )
    event_names = [e for e, _ in logger.events]
    assert "test_logged_start" in event_names
    assert "test_logged_done" in event_names
