"""Regression tests for the procedure-first routing contract.

Motivated by session 0000f516-abb8-42be-8765-d7571ae101e5: Route-Task's
schema fallback chained into Small-Model-Route, which globbed a STALE
procedures path (``vaultbot/`` instead of ``vaultbot-stuff/``), found zero
procedures, and left the big model to spelunk with raw ``code_run`` even
though fused retrieval had already surfaced the right procedure
(Review-PR-Procedure). These tests pin the fixes:

A. Code steps receive the runtime's procedure index (``procedures_index``)
   so meta-procedures never glob the vault for a hardcoded path.
B. The Route-Task schema fallback reuses the fused-retrieval hint
   (selection over generation) instead of re-asking a small model.
C. When a procedure is selected, raw "do it by hand" tools are withheld
   for the turn (VAULTBOT_PROCEDURE_FIRST=0 disables).
D. The Small-Model-Route note consumes the injected index.

No LLM calls anywhere in this file.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit

from agent_tools import (
    PROCEDURE_FIRST_GATED_TOOLS,
    gate_tools_for_procedure_first,
    procedure_first_enabled,
)
from chat_turn_prep import _route_fallback_payload
from procedure_compiler import Step
from procedure_step_executor import _run_code_step

# ── A/B: Route-Task schema fallback prefers the fused hint ───────────────


def _proc_index(*names: str, flagged: tuple[str, ...] = ()) -> dict:
    return {
        n: {
            "path": f"/vault/{n}.md",
            "frontmatter": {
                "type": "procedure",
                "status": "flagged" if n in flagged else "verified",
            },
        }
        for n in names
    }


def test_fallback_chain_uses_fused_hint():
    results = [{"file_path": "/vault/Review-PR-Procedure.md", "score": 0.5}]
    payload = _route_fallback_payload(
        results, _proc_index("Review-PR-Procedure"), "crank out the prs please"
    )
    assert payload["procedure_chain"] == ["Review-PR-Procedure"]
    assert payload["category"] == "unknown"
    assert payload["rationale_code"] == "schema_fallback"
    assert payload["confidence"] == 0.0


def test_fallback_chain_empty_when_retrieval_found_nothing():
    payload = _route_fallback_payload([], _proc_index("Anything"), "do the thing")
    assert payload["procedure_chain"] == []


def test_fallback_chain_empty_when_hint_below_threshold():
    # A weak retrieval score must not promote a procedure into the chain.
    results = [{"file_path": "/vault/Maybe.md", "score": 0.05}]
    payload = _route_fallback_payload(results, _proc_index("Maybe"), "do the thing")
    assert payload["procedure_chain"] == []


def test_fallback_skips_flagged_hint():
    results = [{"file_path": "/vault/Bad.md", "score": 0.9}]
    idx = _proc_index("Bad", flagged=("Bad",))
    payload = _route_fallback_payload(results, idx, "do the thing")
    assert payload["procedure_chain"] == []


def test_fallback_handles_missing_index():
    results = [{"file_path": "/vault/X.md", "score": 0.9}]
    payload = _route_fallback_payload(results, None, "do the thing")
    assert payload["procedure_chain"] == []


# ── C: procedure-first tool gating ────────────────────────────────────────


def _tool(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def test_gate_tools_removes_raw_execution_tools():
    tools = [
        _tool(n)
        for n in (
            "code_read",
            "code_run",
            "safe_write",
            "safe_replace",
            "md_safe_replace",
            "execute_procedure",
            "edit_lines",
            "vault_safe_write",
        )
    ]
    gated = gate_tools_for_procedure_first(tools)
    names = [t["function"]["name"] for t in gated]
    # Read/inspect and procedure-repair tools survive; raw execution goes.
    assert names == ["code_read", "execute_procedure", "edit_lines", "vault_safe_write"]


def test_gate_tools_covers_the_whole_gated_set():
    assert {
        "code_run",
        "safe_write",
        "safe_replace",
        "js_safe_write",
        "js_safe_replace",
        "md_safe_replace",
    } == PROCEDURE_FIRST_GATED_TOOLS
    # execute_procedure must NEVER be gated — it is the path we push toward.
    assert "execute_procedure" not in PROCEDURE_FIRST_GATED_TOOLS


def test_procedure_first_enabled_default_and_killswitch(monkeypatch):
    monkeypatch.delenv("VAULTBOT_PROCEDURE_FIRST", raising=False)
    assert procedure_first_enabled()
    for off in ("0", "false", "off", "no"):
        monkeypatch.setenv("VAULTBOT_PROCEDURE_FIRST", off)
        assert not procedure_first_enabled()
    monkeypatch.setenv("VAULTBOT_PROCEDURE_FIRST", "1")
    assert procedure_first_enabled()


# ── A/D: the code-step namespace carries the procedure index ─────────────


def test_code_step_receives_procedures_index():
    step = Step(
        number=1,
        instruction="report the injected library",
        step_type="code",
        code=(
            "result = json.dumps({\n"
            "    'count': len(procedures_index),\n"
            "    'names': sorted(p['name'] for p in procedures_index),\n"
            "})"
        ),
    )
    ok, out, err, _tb, _sub = _run_code_step(
        step,
        [],
        ".",
        {},
        procedures_index=[{"name": "Small-Model-Route"}, {"name": "Route-Task"}],
    )
    assert ok, err
    data = json.loads(out)
    assert data["count"] == 2
    assert data["names"] == ["Route-Task", "Small-Model-Route"]


def test_code_step_procedures_index_defaults_empty():
    step = Step(
        number=1,
        instruction="default",
        step_type="code",
        code="result = json.dumps({'count': len(procedures_index)})",
    )
    ok, out, err, _tb, _sub = _run_code_step(step, [], ".", {})
    assert ok, err
    assert json.loads(out)["count"] == 0


def test_small_model_route_note_uses_injected_index():
    """The meta-procedure must read procedures_index, never glob a path."""
    from paths import VAULT_ROOT

    note = (
        VAULT_ROOT / "vaultbot-stuff" / "System" / "Procedures" / "Small-Model-Route.md"
    )
    text = note.read_text(encoding="utf-8")
    assert "procedures_index" in text
    # The stale hardcoded folder name that caused the empty-library bug.
    assert '"vaultbot" / "System" / "Procedures"' not in text
    assert "proc_dir.glob" not in text
