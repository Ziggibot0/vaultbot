"""Tests for the plan-as-loop architecture (2026-08-02).

Verifies the framework-driven planning pattern (BabyAGI/LangGraph):
  1. The framework makes a planning call BEFORE the agentic loop — no gates,
     no blocking, no nudge counters. The model never has to CHOOSE to plan.
  2. The loop continues until all_done() (one nudge, then accept).
  3. Step summaries are produced and carried in working memory.

The chat loop decision logic is embedded in the large async handle_chat
function which needs the full Services stack to run, so the framework-plan
+ guard tests are AST/source inspections (same pattern as test_no_plan_after_work).
The planner, summarizer, and working-memory tests are real unit tests.
"""
from __future__ import annotations

import ast
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


# ── 1. Framework-driven planning (replaces the gate) ────────────────────

def test_framework_planner_imported():
    """framework_planner is imported and called before the loop."""
    src = _src()
    assert "from framework_planner import framework_plan" in src
    assert "framework_plan(" in src


def test_no_exec_tool_gate():
    """The old _EXEC_TOOLS gate is GONE — replaced by framework planning."""
    src = _src()
    assert "_EXEC_TOOLS" not in src, (
        "The _EXEC_TOOLS set must be removed — framework planning replaces it."
    )
    assert "plan_gate_blocked" not in src, (
        "The old plan_gate_blocked log must be removed."
    )
    assert "_exec_in_round" not in src, (
        "The old exec-in-round check must be removed."
    )
    assert "no_plan_text_gate" not in src, (
        "The old no-plan text gate must be removed."
    )


def test_framework_plan_called_before_loop():
    """The framework planning call happens before the while True loop."""
    src = _src()
    plan_idx = src.find("framework_plan(")
    loop_idx = src.find("while True:")
    assert plan_idx > 0, "framework_plan must be called"
    assert loop_idx > 0, "while True loop must exist"
    assert plan_idx < loop_idx, (
        "framework_plan must be called BEFORE the while True loop"
    )


def test_framework_plan_writes_to_working_memory():
    """The framework plan result is written to wm via set_plan."""
    src = _src()
    assert "wm.set_plan(" in src
    assert "framework_plan_set" in src, "must log framework_plan_set"


def test_framework_plan_fallback():
    """A failed planning call falls back to a 1-step plan (degraded)."""
    src = _src()
    assert "framework_plan_fallback" in src, "must log fallback"
    assert 'respond to the user' in src, "fallback must be a 1-step plan"


# ── 2. all_done() guard ──────────────────────────────────────────────────

def test_all_done_guard_present():
    """The loop rejects prose in ACT phase until all steps are done."""
    src = _src()
    assert "wm.has_plan() and not wm.all_done()" in src
    assert "phase_act_prose_rejected" in src, "must log phase_act_prose_rejected"


def test_all_done_guard_bounded():
    """The ACT-phase guard never accepts prose as final (no give-up path)."""
    src = _src()
    assert "_all_done_nudge_used" not in src, "one-nudge fallback must be removed"
    assert "all_done_nudge" not in src, "all_done_nudge must be removed"


# ── 3. Step consolidation ────────────────────────────────────────────────

def test_step_summarizer_imported():
    src = _src()
    assert "from step_summarizer import summarize_step" in src


def test_consolidation_on_update_task_completed():
    """The loop consolidates when update_task marks a step completed."""
    src = _src()
    assert "step_summary_built" in src
    assert "step_consolidated" in src
    assert "record_step_summary" in src
    assert "summarize_step(" in src


def test_final_synthesis_on_all_done():
    """When all steps are done, a final-synthesis nudge is injected."""
    src = _src()
    assert "plan_all_done" in src
    assert "Write your final" in src or "final answer" in src.lower()


# ── 4. System prompt documents the architecture ──────────────────────────

def test_prompt_says_framework_plans():
    """The system prompt tells the model the framework already planned."""
    src = _AGENT_TOOLS.read_text(encoding="utf-8")
    assert "YOUR PLAN" in src
    assert "framework already wrote a plan" in src


def test_prompt_no_plan_first_mandatory():
    """The old 'PLAN FIRST (mandatory)' instruction is gone."""
    src = _AGENT_TOOLS.read_text(encoding="utf-8")
    assert "PLAN FIRST (mandatory)" not in src
    assert "BLOCKS action tools" not in src


def test_prompt_documents_consolidation():
    src = _AGENT_TOOLS.read_text(encoding="utf-8")
    assert "CONSOLIDATION" in src
    assert "key facts the next step needs" in src


def test_prompt_documents_final_synthesis():
    src = _AGENT_TOOLS.read_text(encoding="utf-8")
    assert "FINAL SYNTHESIS" in src
    assert "closing summary" in src


# ── 5. working_memory step summaries (real unit tests) ───────────────────

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
        "tasks": [{"id": "1", "content": "a", "status": "completed", "notes": ""}],
        "step_summaries": {"1": "restored summary"},
    }
    wm.restore_snapshot(snap)
    assert wm.summary_for_step("1") == "restored summary"
    assert wm.all_done()


def test_tasklist_clear_wipes_summaries():
    wm = TaskList()
    wm.set_plan(goal="g", items=["a"])
    wm.record_step_summary("1", "s")
    wm.clear()
    assert wm.summary_for_step("1") == ""
    assert wm.step_summaries == {}


# ── 6. step_summarizer (real unit tests) ──────────────────────────────────

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
    assert "Step completed" in summary
    # No LLM call for a trivial step
    assert len(client.calls) == 0


def test_summarize_step_failure_returns_fallback():
    """If the LLM call raises, a fallback summary is returned (degraded)."""
    class _BoomClient:
        def chat(self, **kwargs):
            raise RuntimeError("boom")
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


# ── 7. framework_planner (real unit tests) ───────────────────────────────

class _FakePlanClient:
    """Minimal LLM client double for planning calls."""
    def __init__(self, response='{"goal":"greet","steps":["say hi"]}'):
        self._response = response
        self.calls = []

    def chat(self, messages, tools=None, temperature=0.7, stream=False):
        self.calls.append({"messages": messages, "stream": stream})
        return {"response": self._response}


def test_framework_plan_returns_goal_and_steps():
    client = _FakePlanClient('{"goal":"answer greeting","steps":["greet user"]}')
    result = framework_plan(client, "hi")
    assert result is not None
    goal, steps = result
    assert goal == "answer greeting"
    assert steps == ["greet user"]
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