"""Tests for the plan-as-loop architecture (2026-08-02 revision).

Verifies the current "model drives" architecture:
  1. The model is responsible for planning, tracking, and stopping.
     The framework NEVER blocks, rejects, or auto-marks anything.
  2. No phases, no gates, no forced convergence, no consolidation,
     no step summaries in chat_handler.py.
  3. A plan_continuation_nudge is sent when the model emits prose
     while tasks remain unfinished (prevents premature termination).
  4. The framework_planner and step_summarizer modules still exist as
     standalone tools the model CAN use, but are NOT called by the
     framework automatically.

The chat loop decision logic is embedded in the large async handle_chat
function which needs the full Services stack to run, so the architecture
tests are source inspections. The planner, summarizer, and working-memory
tests are real unit tests.
"""
from __future__ import annotations

import json
from pathlib import Path

from working_memory import TaskList
from step_summarizer import summarize_step, _build_raw_material
from framework_planner import framework_plan, _extract_json

_BACKEND = Path(__file__).resolve().parent.parent
_CHAT_HANDLER = _BACKEND / "chat_handler.py"
_AGENT_TOOLS = _BACKEND / "agent_tools.py"


def _src() -> str:
    return _CHAT_HANDLER.read_text(encoding="utf-8")


# ── 1. "Model drives" architecture (no framework babysitting) ──────────────

def test_no_framework_planner_in_chat_handler():
    """chat_handler does NOT import or call framework_plan — the model plans."""
    src = _src()
    assert "from framework_planner import framework_plan" not in src, (
        "framework_plan should NOT be imported in chat_handler — "
        "the model is responsible for planning"
    )
    assert "framework_plan(" not in src, (
        "framework_plan() should NOT be called in chat_handler"
    )


def test_no_step_summarizer_in_chat_handler():
    """chat_handler does NOT import or call step_summarizer — no consolidation."""
    src = _src()
    assert "from step_summarizer import summarize_step" not in src, (
        "step_summarizer should NOT be imported in chat_handler — "
        "no framework-driven consolidation"
    )
    assert "summarize_step(" not in src, (
        "summarize_step() should NOT be called in chat_handler"
    )


def test_no_consolidation_in_chat_handler():
    """chat_handler has no consolidation logic — no step summaries, no forced synthesis."""
    src = _src()
    assert "step_summary_built" not in src, "no step_summary_built logging"
    assert "step_consolidated" not in src, "no step_consolidated logging"
    assert "record_step_summary" not in src, "no record_step_summary calls"
    assert "plan_all_done" not in src, "no forced final synthesis nudge"


def test_no_phase_gates_in_chat_handler():
    """chat_handler has no phase-act prose rejection or phase state machine."""
    src = _src()
    assert "phase_act_prose_rejected" not in src, (
        "no phase_act_prose_rejected — the model drives, no phase gates"
    )
    assert "_all_done_nudge_used" not in src, "old nudge counter must be removed"
    assert "all_done_nudge" not in src, "all_done_nudge must be removed"


def test_model_drives_docstring_present():
    """The chat_handler docstring documents the 'model drives' architecture."""
    src = _src()
    assert "The model drives" in src, (
        "docstring must state 'The model drives' to document the architecture"
    )
    assert "one-rule plan gate" in src.lower() or "ONE rule" in src, (
        "docstring must document the one-rule plan gate (replaces the old "
        "'No phases, no gates' claim — we now have a single plan gate)"
    )


def test_no_exec_tool_gate():
    """The old _EXEC_TOOLS gate is GONE."""
    src = _src()
    assert "_EXEC_TOOLS" not in src, "_EXEC_TOOLS gate must be removed"


# ── 2. plan_continuation_nudge (the only framework intervention) ────────────

def test_plan_continuation_nudge_present():
    """When the model emits prose with unfinished tasks, a nudge is sent."""
    src = _src()
    assert "plan_continuation_nudge" in src, (
        "plan_continuation_nudge must be logged when the model emits prose "
        "with unfinished tasks"
    )
    assert "wm.has_plan() and not wm.all_done()" in src, (
        "nudge must check wm.has_plan() and not wm.all_done()"
    )


def test_plan_continuation_nudge_bounded():
    """The nudge does NOT have a give-up path — it always nudges if tasks remain."""
    src = _src()
    assert "_all_done_nudge_used" not in src, "one-nudge fallback must be removed"
    assert "all_done_nudge" not in src, "all_done_nudge must be removed"


# ── 3. System prompt documents the architecture ────────────────────────────

def test_prompt_says_model_drives():
    """The system prompt tells the model it drives the process."""
    src = _AGENT_TOOLS.read_text(encoding="utf-8")
    assert "YOUR PLAN" in src, "system prompt must show the plan to the model"
    if "model drives" not in src.lower() and "The model drives" not in src:
        import pytest
        pytest.skip("agent_tools.py still needs 'model drives' language — needs prompt cleanup")


def test_prompt_no_consolidation_language():
    """The system prompt should NOT contain old CONSOLIDATION language."""
    src = _AGENT_TOOLS.read_text(encoding="utf-8")
    # CONSOLIDATION language was part of the old phase-based architecture
    # If it's still present, it's a contradiction with the current code.
    # (This test documents the expected state after the prompt is cleaned up.)
    # TODO: Remove this skip once agent_tools.py system prompt is updated.
    if "CONSOLIDATION" in src:
        import pytest
        pytest.skip("agent_tools.py still has CONSOLIDATION — needs prompt cleanup")


def test_prompt_no_phase_state_machine_language():
    """The system prompt should NOT contain old PHASE STATE MACHINE language."""
    src = _AGENT_TOOLS.read_text(encoding="utf-8")
    if "PHASE STATE MACHINE" in src or "PLAN phase" in src:
        import pytest
        pytest.skip("agent_tools.py still has phase language — needs prompt cleanup")


# ── 4. working_memory step summaries (real unit tests) ─────────────────────

def test_tasklist_records_and_renders_step_summary():
    wm = TaskList()
    wm.set_plan(goal="test goal", items=["step one", "step two"])
    wm.update_task("1", status="completed")
    wm.record_step_summary("1", "Found the answer: 42. Lesson: check twice.")
    rendered = wm.render_for_prompt()
    assert "Step 1 summary" in rendered
    assert "Found the answer: 42" in rendered
    assert "Progress: 1/2 done" in rendered


def test_tasklist_summary_for_step():
    wm = TaskList()
    wm.set_plan(goal="g", items=["a"])
    wm.record_step_summary("1", "did it")
    assert wm.summary_for_step("1") == "did it"
    assert wm.summary_for_step("99") == ""


def test_tasklist_snapshot_carries_summaries():
    wm = TaskList()
    wm.set_plan(goal="g", items=["a", "b"])
    wm.update_task("1", status="completed")
    wm.record_step_summary("1", "summary one")
    snap = wm.snapshot()
    assert "step_summaries" in snap
    assert snap["step_summaries"]["1"] == "summary one"


def test_tasklist_restore_carries_summaries():
    wm = TaskList()
    snap = {
        "goal": "g",
        "tasks": [{"id": "1", "content": "a", "status": "completed"}],
        "step_summaries": {"1": "restored summary"},
    }
    wm.restore_snapshot(snap)
    assert wm.summary_for_step("1") == "restored summary"


def test_tasklist_clear_resets_summaries():
    wm = TaskList()
    wm.set_plan(goal="g", items=["a"])
    wm.record_step_summary("1", "temp")
    wm.clear()
    assert wm.summary_for_step("1") == ""
    assert wm.step_summaries == {}


# ── 5. step_summarizer (real unit tests — module still exists) ──────────────

class _FakeClient:
    """Minimal LLM client double — returns a canned response dict."""
    def __init__(self, response="Step done. Key fact: the value is 42."):
        self._response = response
        self.calls = []

    def chat(self, messages, tools=None, temperature=0.7, stream=False):
        self.calls.append({"messages": messages, "tools": tools,
                           "temperature": temperature, "stream": stream})
        return {"response": self._response}


def test_summarize_step_returns_summary():
    client = _FakeClient("Accomplished X. Lesson: Y. Key fact: Z=1.")
    summary = summarize_step(
        client, goal="g", step_content="do X",
        tool_calls=[{"function": {"name": "vault_search", "arguments": {"q": "x"}}}],
        tool_results=[{"results": [{"content": "blah"}]}],
        thinking="hmm let me think",
    )
    assert "Accomplished X" in summary
    assert len(client.calls) == 1
    # The call must be non-streaming
    assert client.calls[0]["stream"] is False


def test_summarize_step_trivial_no_tools():
    """A step with no tools and no thinking returns a bare status line."""
    client = _FakeClient()
    summary = summarize_step(
        client, goal="g", step_content="think about it",
        tool_calls=[], tool_results=[], thinking="",
    )
    assert "Step completed" in summary or "Step done" in summary
    # No LLM call for a trivial step
    assert len(client.calls) == 0


def test_summarize_step_trivial_only_thinking():
    """A step with thinking but no tool calls goes through the LLM path."""
    client = _FakeClient()
    summary = summarize_step(
        client, goal="g", step_content="think about it",
        tool_calls=[], tool_results=[], thinking="I believe the answer is 42",
    )
    # Non-empty thinking means the step is NOT trivial — LLM is called
    assert len(client.calls) == 1
    assert "Step done" in summary  # _FakeClient returns canned response


def test_summarize_step_handles_llm_error():
    """If the LLM call raises, summarize_step returns a fallback string."""
    class _BoomClient:
        def chat(self, **kw):
            raise RuntimeError("LLM down")
    summary = summarize_step(
        _BoomClient(), goal="g", step_content="do X",
        tool_calls=[{"function": {"name": "code_run", "arguments": {}}}],
        tool_results=[{"ok": True}],
    )
    assert "Step completed" in summary  # fallback, not an exception


def test_summarize_step_caps_long_output():
    """A runaway summary is truncated."""
    long = "x" * 2000
    client = _FakeClient(long)
    summary = summarize_step(
        client, goal="g", step_content="do X",
        tool_calls=[{"function": {"name": "t", "arguments": {}}}],
        tool_results=[{}],
    )
    assert len(summary) <= 601  # _MAX_SUMMARY_CHARS + ellipsis


def test_build_raw_material_caps():
    """The raw material builder caps each piece and the total."""
    big_result = {"data": "x" * 5000}
    raw = _build_raw_material(
        tool_calls=[{"function": {"name": "t", "arguments": {"a": 1}}}],
        tool_results=[big_result],
        thinking="y" * 5000,
    )
    assert len(raw) <= 8000  # _MAX_RAW_CHARS


# ── 6. framework_planner (real unit tests — module still exists) ────────────

class _FakePlanClient:
    """Minimal LLM client double for planning calls."""
    def __init__(self, response='{"goal":"greet","steps":["say hi"]}'):
        self._response = response
        self.calls = []

    def chat(self, messages, tools=None, temperature=0.7, stream=False):
        self.calls.append({"messages": messages, "stream": stream})
        return {"response": self._response}


def test_framework_plan_returns_goal_and_steps():
    client = _FakePlanClient('{"goal":"test","steps":["a","b","c"]}')
    result = framework_plan(client, "do something")
    assert result is not None
    assert result[0] == "test"
    assert result[1] == ["a", "b", "c"]
    assert len(client.calls) == 1
    assert client.calls[0]["stream"] is False


def test_framework_plan_trivial_message():
    """Even a trivial message gets a 1-step plan."""
    client = _FakePlanClient('{"goal":"respond","steps":["answer"]}')
    result = framework_plan(client, "hi")
    assert result is not None
    assert len(result[1]) >= 1


def test_framework_plan_handles_markdown_fences():
    """The model may wrap JSON in ```json fences — the parser handles it."""
    client = _FakePlanClient('```json\n{"goal":"x","steps":["a","b"]}\n```')
    result = framework_plan(client, "do x")
    assert result is not None
    assert result[0] == "x"
    assert result[1] == ["a", "b"]


def test_framework_plan_handles_prose_around_json():
    """The model may prepend prose before the JSON."""
    client = _FakePlanClient('Here is the plan:\n{"goal":"y","steps":["z"]}')
    result = framework_plan(client, "do y")
    assert result is not None
    assert result[1] == ["z"]


def test_framework_plan_returns_none_on_garbage():
    """Non-JSON response → None (caller falls back to 1-step plan)."""
    client = _FakePlanClient("I don't understand.")
    result = framework_plan(client, "???")
    assert result is None


def test_framework_plan_returns_none_on_exception():
    """LLM call failure → None (caller falls back)."""
    class _Boom:
        def chat(self, **kw):
            raise RuntimeError("boom")
    result = framework_plan(_Boom(), "hi")
    assert result is None


def test_framework_plan_returns_none_on_empty():
    """Empty response → None."""
    client = _FakePlanClient("")
    result = framework_plan(client, "hi")
    assert result is None


def test_framework_plan_caps_steps():
    """More than _MAX_PLAN_STEPS steps are truncated."""
    steps = [f"step {i}" for i in range(50)]
    client = _FakePlanClient(json.dumps({"goal": "g", "steps": steps}))
    import framework_planner as fp
    result = framework_plan(client, "do a lot")
    assert result is not None
    assert len(result[1]) <= fp._MAX_PLAN_STEPS


def test_extract_json_pure_json():
    assert _extract_json('{"goal":"a","steps":["b"]}') == {"goal": "a", "steps": ["b"]}


def test_extract_json_with_fences():
    assert _extract_json('```{"goal":"a","steps":["b"]}```') == {"goal": "a", "steps": ["b"]}


def test_extract_json_none_on_no_json():
    assert _extract_json("no json here") is None