"""Tests for step_gate_runtime.py — runtime logic with a fake LLM client.

Covers the execute_procedure loop: linear text pass, validation fail +
stop, code step execution, condition skip, branch jump, structured
validation (at_least / contains / matches + fallback), the condition
evaluator, and the recursion guards via the run_procedure CLI.  No
Ollama, no network; code steps use a real subprocess with the venv
python available in tests.

See [[Procedure-Subprocess-Architecture]].
"""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

from procedure_compiler import compile_from_text
from subprocess_utils import run as _subprocess_run
from step_gate_runtime import (
    _count_thing,
    _evaluate_condition,
    _parse_validation,
    _validate_step,
    execute_procedure,
)


# ── Fake LLM client (for v1 text steps) ─────────────────────────────────


@dataclass
class _FakeResp:
    response: str


class FakeLLMClient:
    """Returns canned responses per call index. Records calls."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    def chat(self, messages, temperature=0.3, stream=False, **kwargs):
        i = min(self.calls, len(self._responses) - 1)
        out = self._responses[i]
        self.calls += 1
        return {"response": out}


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_procedure(
    steps_text: str, name: str = "Test-Proc", allowed_tools: list[str] | None = None
) -> Any:
    fm = "type: procedure\n"
    if allowed_tools:
        fm += "allowed_tools:\n" + "".join(f"  - {t}\n" for t in allowed_tools)
    text = f"---\n{fm}---\n## Steps\n{steps_text}"
    return compile_from_text(name, text)


def _run(proc, client=None, vault_path="."):
    if client is None:
        client = FakeLLMClient(["ok"])
    return asyncio.run(
        execute_procedure(
            procedure=proc,
            context="",
            llm_client=client,
            vault_path=vault_path,
        )
    )


# ── Condition evaluator ─────────────────────────────────────────────────


def test_condition_count_lt():
    outputs = [(1, "[[A]] [[B]] [[C]]")]
    ok, reason = _evaluate_condition("if < 3 notes", [], outputs)
    assert not ok  # 3 notes, not < 3


def test_condition_count_le():
    outputs = [(1, "[[A]] [[B]]")]
    ok, _ = _evaluate_condition("<= 2 notes", [], outputs)
    assert ok


def test_condition_contains():
    outputs = [(1, "the claim is supported")]
    ok, _ = _evaluate_condition('contains "supported"', [], outputs)
    assert ok
    ok, _ = _evaluate_condition('contains "refuted"', [], outputs)
    assert not ok


def test_condition_unparseable_skips():
    outputs = [(1, "anything")]
    ok, reason = _evaluate_condition("a weird unrecognised predicate", [], outputs)
    assert not ok
    assert "unparseable" in reason


# ── _count_thing ────────────────────────────────────────────────────────


def test_count_thing_wikilinks():
    assert _count_thing("[[A]] and [[B|C]]", "notes") == 2
    assert _count_thing("no links here", "notes") == 0


def test_count_thing_urls():
    assert _count_thing("see https://x.com and http://y.com", "sources") == 2


def test_count_thing_items():
    text = "- one\n- two\n1. three\n2. four"
    assert _count_thing(text, "items") == 4


# ── Structured validation ───────────────────────────────────────────────


def test_validate_at_least_pass():
    ok, err = _validate_step("[[A]] [[B]] [[C]]", "at_least 2 notes")
    assert ok
    assert err is None


def test_validate_at_least_fail():
    ok, err = _validate_step("[[A]]", "at_least 2 notes")
    assert not ok
    assert "found 1" in err


def test_validate_contains_pass():
    ok, _ = _validate_step("the claim is supported", 'contains "supported"')
    assert ok


def test_validate_contains_fail():
    ok, _ = _validate_step("the claim is refuted", 'contains "supported"')
    assert not ok


def test_validate_matches_pass():
    ok, _ = _validate_step("error code 42", r"matches /\d+/")
    assert ok


def test_validate_matches_invalid_regex():
    ok, err = _validate_step("anything", "matches /(/")
    assert not ok
    assert "invalid regex" in err


def test_validate_fallback_word_overlap():
    # Free text that isn't a known form → word-overlap fallback.
    ok, _ = _validate_step("the note mentions sources", "mention sources")
    assert ok  # "sources" present


def test_validate_none_always_passes():
    ok, err = _validate_step("anything", None)
    assert ok
    assert err is None


def test_parse_validation_unknown_returns_none():
    assert _parse_validation("just some free text") is None


# ── execute_procedure loop ──────────────────────────────────────────────


def test_linear_text_pass():
    proc = _make_procedure(
        '1. First step [validate: contains "ok"]\n'
        '2. Second step [validate: contains "ok"]'
    )
    result = _run(proc, FakeLLMClient(["ok", "ok"]))
    assert result.overall_passed
    assert result.failed_step is None
    assert len(result.steps) == 2


def test_validation_fail_stops():
    proc = _make_procedure(
        '1. First step [validate: contains "ok"]\n'
        '2. Second step [validate: contains "ok"]'
    )
    result = _run(proc, FakeLLMClient(["wrong", "ok"]))
    assert not result.overall_passed
    assert result.failed_step == 1
    assert len(result.steps) == 1  # stopped at step 1


def test_condition_skips_step():
    # Step 1 produces no notes; step 2 has condition "< 3 notes" → run.
    proc = _make_procedure(
        '1. Step one [validate: contains "done"]\n'
        '2. Step two [condition: if < 3 notes] [validate: contains "done"]'
    )
    # Step 1 returns "done" (no wikilinks → 0 notes, < 3 → condition met).
    result = _run(proc, FakeLLMClient(["done", "done"]))
    assert result.overall_passed
    assert len(result.steps) == 2


def test_condition_not_met_skips_step():
    # Step 1 output has 5 wikilinks; step 2 condition "< 3 notes" → skip.
    proc = _make_procedure(
        '1. Step one [validate: contains "done"]\n'
        '2. Step two [condition: if < 3 notes] [validate: contains "done"]'
    )
    step1_out = "done [[A]] [[B]] [[C]] [[D]] [[E]]"
    result = _run(proc, FakeLLMClient([step1_out, "done"]))
    assert result.overall_passed
    # Step 2 was skipped (recorded as a skip, not executed).
    assert "skipped" in result.steps[1].output


def test_branch_jump():
    # Step 1 branches to step 3 on pass; step 2 should be skipped.
    proc = _make_procedure(
        '1. Step one [validate: contains "ok"] [branch: step 3]\n'
        '2. Should be skipped [validate: contains "ok"]\n'
        '3. Step three [validate: contains "ok"]'
    )
    # Only steps 1 and 3 execute (step 2 is skipped via branch).
    result = _run(proc, FakeLLMClient(["ok", "ok"]))
    assert result.overall_passed
    # Steps executed: 1 and 3 (step 2 skipped via branch).
    executed_numbers = {s.step_number for s in result.steps}
    assert 1 in executed_numbers
    assert 3 in executed_numbers
    assert 2 not in executed_numbers


def test_branch_to_missing_step_falls_through():
    # Branch target 99 doesn't exist → log + fall through to next step.
    proc = _make_procedure(
        '1. Step one [validate: contains "ok"] [branch: step 99]\n'
        '2. Step two [validate: contains "ok"]'
    )
    result = _run(proc, FakeLLMClient(["ok", "ok"]))
    assert result.overall_passed
    assert {s.step_number for s in result.steps} == {1, 2}


def test_empty_procedure_returns_pass():
    """An empty procedure body (no content) compiles 0 steps and passes.

    This is the legitimate case — a procedure note with type: procedure
    but no steps yet. It's not a failure, just empty.
    """
    proc = _make_procedure("")
    result = _run(proc)
    assert result.overall_passed
    assert result.steps == []


def test_content_but_zero_steps_fails_loud():
    """A procedure with body content that parses 0 steps fails LOUD.

    This is the format-mismatch case (random prose with no numbered list
    and no ### Step N: headers). The diagnosis must explain WHY so the
    caller can fix it. This is the fix for the 60-round loop where
    execute_procedure returned 0 steps with no explanation.

    NOTE: ### Step N: headers now DO parse as steps (the compiler was
    updated to recognize them). This test uses plain prose with no
    step markers at all to trigger the 0-steps path.
    """
    proc = _make_procedure("## Steps\n\nJust some prose with no step markers at all.\n")
    result = _run(proc)
    assert not result.overall_passed
    assert result.failed_step == 0
    assert "0 STEPS" in result.final_output or "0 steps" in result.final_output.lower()
    assert "numbered" in result.final_output.lower()


def test_code_step_executes(tmp_path):
    # A code step that sets `result`.  Uses a simple Python expression
    # that doesn't need vault_search (no allowed_tools).
    proc = _make_procedure(
        "1. ```python\nresult = 2 + 2\n```\n",
        allowed_tools=[],
    )
    result = _run(proc, vault_path=str(tmp_path))
    assert result.overall_passed
    assert "4" in result.final_output


def test_child_procedures_field_default_empty():
    proc = _make_procedure('1. Step [validate: contains "ok"]')
    result = _run(proc, FakeLLMClient(["ok"]))
    assert result.child_procedures == []


# ── Recursion guards (run_procedure CLI) ────────────────────────────────
#
# These exercise run_procedure.py as a subprocess.  They need the venv
# python (or fall back to sys.executable) and a fixture procedure on
# disk.  Skipped if the backend isn't importable in the test environment.


@pytest.fixture
def _fixture_procedures(tmp_path):
    """Write a parent + child procedure to tmp_path and return the dir."""
    parent = tmp_path / "Parent-Proc.md"
    parent.write_text(
        "---\ntype: procedure\nallowed_tools:\n  - run_procedure\n---\n"
        "## Steps\n"
        "1. ```python\nresult = run_procedure('Child-Proc')\n```\n",
        encoding="utf-8",
    )
    child = tmp_path / "Child-Proc.md"
    child.write_text(
        "---\ntype: procedure\n---\n## Steps\n1. [llm: Say hello.]\n",
        encoding="utf-8",
    )
    return tmp_path


def test_run_procedure_cycle_detection(_fixture_procedures, monkeypatch):
    """A procedure that calls itself should be caught as a cycle."""
    import os
    from subprocess_utils import run as _subprocess_run
    import sys as _sys

    cycle_proc = _fixture_procedures / "Cycle-Proc.md"
    cycle_proc.write_text(
        "---\ntype: procedure\nallowed_tools:\n  - run_procedure\n---\n"
        "## Steps\n1. ```python\nresult = run_procedure('Cycle-Proc')\n```\n",
        encoding="utf-8",
    )

    backend = Path(__file__).parent.parent.resolve()
    env = {**os.environ, "PYTHONPATH": str(backend)}
    r = _subprocess_run(
        [
            _sys.executable,
            str(backend / "run_procedure.py"),
            "--procedure-name",
            "Cycle-Proc",
            "--vault-path",
            str(_fixture_procedures),
            "--call-stack",
            "[]",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    out = json.loads(r.stdout)
    # The cycle is caught in the CHILD subprocess (run_procedure.py sees
    # the procedure is already in call_stack) and surfaced as a loud step
    # failure in the parent.  Either form is acceptable:
    #   - top-level {"cycle_detected": true} (if the top-level IS the child)
    #   - top-level step failed (the child's cycle error raised in the parent)
    assert out.get("cycle_detected") is True or out.get("overall_passed") is False, (
        f"expected cycle detection or step failure, got: {out}"
    )


def test_run_procedure_depth_limit(_fixture_procedures):
    """Exceeding max-depth should be caught loudly."""
    import os
    import sys as _sys

    backend = Path(__file__).parent.parent.resolve()
    env = {**os.environ, "PYTHONPATH": str(backend)}
    r = _subprocess_run(
        [
            _sys.executable,
            str(backend / "run_procedure.py"),
            "--procedure-name",
            "Parent-Proc",
            "--vault-path",
            str(_fixture_procedures),
            "--call-stack",
            '["A", "B", "C"]',  # already at depth 3
            "--max-depth",
            "3",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    out = json.loads(r.stdout)
    assert out.get("depth_exceeded") is True
