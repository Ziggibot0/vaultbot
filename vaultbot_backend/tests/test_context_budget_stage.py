"""Regression tests for the "idles at 'budgeting context'" symptom.

Root cause map (see chat_turn_prep.py silent-zone audit, fresh-laptop case):
1. The ``budgeting context`` progress event had NO closing event when the
   context fit (the common case) — it was the last label before a silent
   stretch of preflight code, so any slow/hung call after it read as
   "budgeting is slow / idling".
2. The token-usage meter called ``context_window()`` — a BLOCKING
   requests.post (15s timeout) — directly on the event loop. When the boot
   probe failed (fresh laptop, Ollama not up yet), the success cache
   stayed empty and EVERY turn re-probed, freezing the loop and the UI at
   the last label: "budgeting context".
3. chat_turn_finalize.py had a sibling copy of the same blocking meter.
4. ``context_window()`` cached successes but not failures, so a dead
   Ollama re-paid the full connect timeout from every caller each turn.

These tests pin the fix's contract: the budget stage ALWAYS closes with a
``context_budgeted`` event, the meter NEVER blocks the turn longer than a
small bound no matter how slow the probe is, both meters route through
the shared helper, and failed probes are negative-cached.
"""

import asyncio
import inspect
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

# RED-phase note: helpers not implemented yet — importing lazily so each
# test fails on ITS missing behavior, not on collection.
from context_budgeter import ContextBudgeter

pytestmark = pytest.mark.unit


class _RecordingManager:
    """Fake ConnectionManager: records every send_personal_message payload."""

    def __init__(self):
        self.calls = []

    async def send_personal_message(self, message, websocket, session_logger=None):
        self.calls.append(message)


class _Log:
    """Fake session logger: records (event_name, data) tuples."""

    def __init__(self):
        self.events = []

    def log(self, name, data=None, **kwargs):
        self.events.append((name, data))


def _recording_svc(budgeter=None, client=None) -> Any:
    """Services-like fake: recording manager + budgeter + ollama_client."""
    manager = _RecordingManager()
    return SimpleNamespace(
        manager=manager,
        session_logger=None,  # send_progress reads svc.session_logger directly
        context_budgeter=budgeter or ContextBudgeter(model_context_limit=32768),
        ollama_client=client
        or SimpleNamespace(
            llm_model="qwen-test",
            context_window=lambda model=None: 32768,
        ),
    )


def _stages(svc):
    """Extract the sequence of progress 'stage' values sent to the UI."""
    return [json.loads(m).get("stage") for m in svc.manager.calls]


def _payloads(svc):
    return [json.loads(m) for m in svc.manager.calls]


# ── Defect 1: budget stage never closes when it doesn't truncate ──────────


def test_budget_stage_always_closes_with_done_event():
    """The 'budgeting context' label must always be followed by
    'context_budgeted' — even when no truncation happened (the common
    case). Otherwise the UI's activity line freezes on the last pre-LLM
    label for the whole silent stretch that follows (prompt build, token
    meter, model load), which is exactly the 'idling at budgeting context'
    symptom."""
    from chat_turn_prep import _apply_context_budget

    svc = _recording_svc()
    context = "short context"  # fits — no truncation

    out = asyncio.run(
        _apply_context_budget(
            svc, object(), _Log(), context, ["turn-history-placeholder"]
        )
    )

    stages = _stages(svc)
    assert stages[0] == "budgeting context"
    assert "context_budgeted" in stages, (
        "budget stage must ALWAYS emit a closing event; got: "
        f"{stages}. Without it the UI idles at 'budgeting context'."
    )
    assert out == context  # untruncated context passes through unchanged


def test_budget_stage_reports_truncation_details():
    """When truncation happens, the done-event carries original/budgeted
    token counts so the UI can show how much was dropped."""
    from chat_turn_prep import _apply_context_budget

    svc = _recording_svc(
        budgeter=ContextBudgeter(
            model_context_limit=32768,
            system_prompt_overhead=30000,
            response_reserve=4096,
        )
    )
    context = "x" * 200_000  # way over budget → truncation

    out = asyncio.run(_apply_context_budget(svc, object(), _Log(), context, []))

    events = [p for p in _payloads(svc) if p.get("stage") == "context_budgeted"]
    assert events, "truncation must emit a context_budgeted event"
    detail = events[0]["detail"]
    assert detail["truncated"] is True
    assert detail["original_tokens"] > detail["budgeted_tokens"]
    assert len(out) < len(context)


def test_budget_stage_failure_still_closes_and_passes_context_through():
    """If the budgeter itself raises, the context must pass through, a
    console failure must fire, and the stage must still close."""
    from chat_turn_prep import _apply_context_budget

    class BrokenBudgeter:
        def budget(self, context, history):
            raise RuntimeError("boom")

    svc = _recording_svc(budgeter=BrokenBudgeter())
    context = "irreplaceable vault context"

    out = asyncio.run(_apply_context_budget(svc, object(), _Log(), context, []))

    assert out == context
    kinds = [p.get("type") for p in _payloads(svc)]
    assert "console_error" in kinds, "budget failure must surface to the console"
    assert "context_budgeted" in _stages(svc), "stage must close even on failure"


# ── Defect 2: token meter blocks the event loop ───────────────────────────


class _SlowProbeClient:
    """Simulates a hung Ollama /api/show — the fresh-laptop case (boot
    probe failed → success cache empty → per-turn re-probe)."""

    llm_model = "qwen-test"

    def __init__(self, probe_seconds: float = 3.5):
        self.probe_seconds = probe_seconds
        self.calls = 0

    def context_window(self, model=None):
        self.calls += 1
        time.sleep(self.probe_seconds)
        return 32768


def test_context_usage_meter_never_blocks_the_turn():
    """The context-usage meter is a UI nicety. It must NEVER stall the turn
    (or the event loop) waiting on a hung probe: run it off-loop, cap the
    wait at a small bound, and skip the meter on timeout."""
    from chat_turn_prep import _emit_context_usage

    client = _SlowProbeClient(probe_seconds=3.5)
    svc = _recording_svc(client=client)
    conversation = [{"role": "system", "content": "x" * 40_000}]

    async def run():
        t0 = time.monotonic()
        await _emit_context_usage(svc, object(), _Log(), conversation)
        return time.monotonic() - t0

    elapsed = asyncio.run(run())

    assert elapsed < 3.0, (
        f"meter blocked the turn for {elapsed:.1f}s — a hung /api/show probe "
        "freezes the UI at the last progress label (the 'budgeting context' "
        "idle). Cap the probe wait and skip the meter on timeout."
    )
    assert client.calls == 1  # the probe was attempted, off-loop


def test_context_usage_meter_emits_usage_when_probe_is_fast():
    """Happy path: a fast (cached) probe still emits the context_usage
    event so the token meter in the UI keeps working."""
    from chat_turn_prep import _emit_context_usage

    svc = _recording_svc()
    conversation = [{"role": "system", "content": "x" * 400}]

    asyncio.run(_emit_context_usage(svc, object(), _Log(), conversation))

    usage = [p for p in _payloads(svc) if p.get("type") == "context_usage"]
    assert usage, "meter must emit context_usage when the probe is fast"
    assert usage[0]["used_tokens"] == 100
    assert usage[0]["context_window"] == 32768


def test_context_usage_meter_timeout_is_logged_not_swallowed():
    """A probe timing out must be LOGGED (with a reason), not silently
    skipped — the operator must be able to see why the meter is missing."""
    from chat_turn_prep import _emit_context_usage

    client = _SlowProbeClient(probe_seconds=3.5)
    svc = _recording_svc(client=client)
    log = _Log()
    conversation = [{"role": "system", "content": "hi"}]

    asyncio.run(_emit_context_usage(svc, object(), log, conversation))

    names = [n for n, _ in log.events]
    assert "context_usage_emit_failed" in names
    (_, data) = next(e for e in log.events if e[0] == "context_usage_emit_failed")
    assert "timeout" in str(data.get("error", "")).lower()


def test_context_usage_meter_failure_is_logged_not_raised():
    """A raising probe must not break the turn — log and move on."""
    from chat_turn_prep import _emit_context_usage

    class ExplodingProbeClient:
        llm_model = "qwen-test"

        def context_window(self, model=None):
            raise RuntimeError("ollama down")

    svc = _recording_svc(client=ExplodingProbeClient())
    log = _Log()
    conversation = [{"role": "system", "content": "hi"}]

    # Must not raise.
    asyncio.run(_emit_context_usage(svc, object(), log, conversation))

    assert any(n == "context_usage_emit_failed" for n, _ in log.events)


# ── Defect 3: sibling meter copy in chat_turn_finalize.py ──────────────────


def test_both_meters_route_through_the_shared_nonblocking_helper():
    """chat_turn_prep.py AND chat_turn_finalize.py must both use the shared
    _emit_context_usage helper — no inline blocking context_window() calls
    left in the chat family (checked via AST so docstrings don't match)."""
    import ast

    import chat_turn_finalize
    import chat_turn_prep

    def _inline_context_window_calls(module) -> list[str]:
        """Return file:line of any ollama_client.context_window call node."""
        with open(module.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "context_window":
                    hits.append(f"{module.__name__}:{node.lineno}")
        return hits

    for mod in (chat_turn_prep, chat_turn_finalize):
        src = inspect.getsource(mod)
        assert "_emit_context_usage(" in src, (
            f"{mod.__name__} must route its token meter through the shared "
            "_emit_context_usage helper"
        )
        inline = _inline_context_window_calls(mod)
        assert not inline, (
            f"{mod.__name__} still has inline blocking context_window() "
            f"calls at {inline} — that is the event-loop stall"
        )


# ── Defect 4: failed probes are not negative-cached ──────────────────────


class _FailingPost:
    """Fake requests.Session whose post always fails."""

    def __init__(self, fail_first=10**9):
        self.calls = 0
        self.fail_first = fail_first

    def post(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise ConnectionError("ollama down")
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"model_info": {"arch.context_length": 4096}},
        )


def test_context_window_failure_is_negative_cached():
    """A failed probe must not be retried from every caller on every turn —
    that multiplies a dead Ollama's connect timeout across the turn. Cache
    the failure and re-raise immediately within the TTL."""
    from ollama_client import OllamaClient

    model = "neg-cache-test-model-a"
    post = _FailingPost(fail_first=10**9)
    client = OllamaClient(base_url="http://localhost:11434", llm_model=model)
    setattr(client, "_session", post)  # fake transport for the probe

    with pytest.raises(ConnectionError):
        client.context_window(model)
    assert post.calls == 1

    # Second call within the TTL: suppressed WITHOUT a new HTTP attempt.
    with pytest.raises(RuntimeError, match="negative-cached"):
        client.context_window(model)
    assert post.calls == 1, "failed probe must be negative-cached (no re-probe)"


def test_context_window_success_clears_negative_cache(monkeypatch):
    """Once Ollama comes back, the next call after the TTL re-probes and
    succeeds — and a success clears the failure cache."""
    from ollama_client import OllamaClient

    monkeypatch.setenv("VAULTBOT_CTX_PROBE_FAIL_TTL", "0")
    model = "neg-cache-test-model-b"
    post = _FailingPost(fail_first=1)  # fails once, then succeeds
    client = OllamaClient(base_url="http://localhost:11434", llm_model=model)
    setattr(client, "_session", post)  # fake transport for the probe

    with pytest.raises(ConnectionError):
        client.context_window(model)
    assert post.calls == 1

    # TTL=0 → immediate re-probe allowed → succeeds and caches the result.
    assert client.context_window(model) == 4096
    assert post.calls == 2

    # Success cache hit — no further HTTP.
    assert client.context_window(model) == 4096
    assert post.calls == 2


# ── Budgeter innocence: budget() itself can never be the hang ─────────────


def test_budgeter_is_fast_on_huge_context():
    """The budgeter is pure string math — pin that it stays that way. This
    documents why the idle can never be inside budget() and protects the
    "doesn't add much time" claim from the original PR."""
    budgeter = ContextBudgeter(model_context_limit=131072)
    context = "note body. " * 200_000  # ~2.8M chars

    t0 = time.monotonic()
    for _ in range(50):
        result = budgeter.budget(context, [])
    elapsed = time.monotonic() - t0

    assert elapsed < 2.0, f"budget() took {elapsed:.2f}s for 50 runs — regression"
    assert result["truncated"] is True
    assert result["chars_dropped"] > 0
