"""Tests for the procedure suggestion gate — the "autofill" nudge.

Pure-logic tests: no I/O, no LLM, no vault. Builds a synthetic first-tool
index and verifies the gate fires only when the retrieval-selected
preflight hint procedure starts with the tool the model just called,
including the once-per-session de-dup and flagged-procedure skipping.
Candidate selection is score-driven (the hint comes from FUSED retrieval
upstream) — there are deliberately no keyword/trigger-overlap heuristics
to test, because there are none in the gate.

Motivated by session ``eb8143f7``: the model reached for raw ``vaultbot_sync``
+ ``code_run`` to sync the repo instead of calling
``execute_procedure("Git-Sync-Upstream")``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from procedure_first_tool_index import _extract_first_tools, build_first_tool_index
from procedure_suggestion_gate import (
    check_procedure_name_suggestion,
    check_procedure_suggestion,
)

# ── _extract_first_tools ─────────────────────────────────────────────────


def test_extract_custom_tools_import_run_as():
    code = "from custom_tools.vaultbot_sync import run as _sync\nr = _sync({})"
    assert "vaultbot_sync" in _extract_first_tools(code)


def test_extract_custom_tools_import_bare():
    code = "from custom_tools import github_issues\nr = github_issues({})"
    assert "github_issues" in _extract_first_tools(code)


def test_extract_subprocess_surfaces_as_code_run():
    code = "import subprocess\nr = subprocess.run(['git','status'])"
    assert "code_run" in _extract_first_tools(code)


def test_extract_dispatch_call_comment():
    code = "# Dispatch: call vault_search\nresult = _dispatch_ns['x']"
    assert "vault_search" in _extract_first_tools(code)


def test_extract_run_procedure():
    code = 'run_procedure("Child-Proc")'
    assert "run_procedure" in _extract_first_tools(code)


def test_extract_empty_code_returns_empty():
    assert _extract_first_tools("") == set()
    assert _extract_first_tools(None) == set()  # type: ignore[arg-type]


# ── check_procedure_suggestion ───────────────────────────────────────────


def _idx_entry(first_tools, triggers, description="", status=""):
    return {
        "first_tools": set(first_tools),
        "triggers": triggers,
        "description": description,
        "status": status,
        "allowed_tools": list(first_tools),
    }


def test_gate_fires_when_hint_starts_with_called_tool():
    idx = {
        "Git-Sync-Upstream": _idx_entry(
            {"vaultbot_sync"},
            ["sync the local repo with upstream main"],
            description="Syncs local repo with upstream main.",
        )
    }
    sug = check_procedure_suggestion("vaultbot_sync", "Git-Sync-Upstream", idx)
    assert sug is not None
    assert sug["procedure_suggestion"] == "Git-Sync-Upstream"
    assert sug["first_tool"] == "vaultbot_sync"
    assert "execute_procedure" in sug["message"]
    assert sug["proceed_keyword"] == "proceed"


def test_gate_no_fire_when_tool_not_suggestible():
    idx = {
        "X": _idx_entry({"plan_task"}, ["plan a task"]),
    }
    sug = check_procedure_suggestion("plan_task", "X", idx)
    assert sug is None  # plan_task is not in _SUGGESTIBLE_TOOLS


def test_gate_no_fire_when_hint_uses_a_different_tool():
    idx = {
        "Git-Sync-Upstream": _idx_entry({"vaultbot_sync"}, ["sync"]),
    }
    sug = check_procedure_suggestion("github_issues", "Git-Sync-Upstream", idx)
    assert sug is None


def test_gate_no_fire_when_no_hint():
    # Retrieval found no matching procedure this turn -> the gate has no
    # candidate and must stay silent (no lexical fallback heuristics).
    idx = {
        "Git-Status-Check": _idx_entry({"code_run"}, ["check the daily standup"]),
    }
    sug = check_procedure_suggestion("code_run", "", idx)
    assert sug is None


def test_gate_no_fire_when_hint_not_in_index():
    idx = {
        "Git-Sync-Upstream": _idx_entry({"code_run"}, ["sync upstream repo"]),
    }
    sug = check_procedure_suggestion("code_run", "Nonexistent-Proc", idx)
    assert sug is None


def test_gate_fires_for_code_run_when_hint_shells_out():
    # Procedures that shell out via subprocess surface as code_run in the
    # first-tool index — the gate fires on the structural match alone.
    idx = {
        "Git-Sync-Upstream": _idx_entry({"code_run"}, ["sync upstream repo stash"]),
    }
    sug = check_procedure_suggestion("code_run", "Git-Sync-Upstream", idx)
    assert sug is not None
    assert sug["procedure_suggestion"] == "Git-Sync-Upstream"


def test_gate_skips_flagged_procedures():
    idx = {
        "Bad-Proc": _idx_entry({"vaultbot_sync"}, ["sync"], status="flagged"),
    }
    sug = check_procedure_suggestion("vaultbot_sync", "Bad-Proc", idx)
    assert sug is None  # flagged procedures are blocked from execution


def test_gate_dedup_once_per_session():
    idx = {
        "Git-Sync-Upstream": _idx_entry({"vaultbot_sync"}, ["sync upstream repo"]),
    }
    already: set[str] = set()
    sug1 = check_procedure_suggestion(
        "vaultbot_sync", "Git-Sync-Upstream", idx, already_suggested=already
    )
    assert sug1 is not None
    assert "vaultbot_sync" in already
    # Second call same session -> no nudge (passes through).
    sug2 = check_procedure_suggestion(
        "vaultbot_sync", "Git-Sync-Upstream", idx, already_suggested=already
    )
    assert sug2 is None


def test_gate_no_fire_when_index_empty():
    sug = check_procedure_suggestion("vaultbot_sync", "Git-Sync-Upstream", {})
    assert sug is None


# ── build_first_tool_index (integration with the compiler) ──────────────


def test_build_index_from_synthetic_proc_index(tmp_path):
    """Compile a real procedure note and verify the index entry."""
    proc = tmp_path / "Git-Sync-Upstream.md"
    proc.write_text(
        "---\n"
        "type: procedure\n"
        "status: experimental\n"
        'description: "Syncs local repo with upstream main."\n'
        'when_to_use: "When you need to sync with upstream before work."\n'
        "tags:\n"
        "  - git\n"
        "  - sync\n"
        "allowed_tools:\n"
        "  - code_run\n"
        "---\n\n"
        "# Git-Sync-Upstream\n\n"
        "## Steps\n\n"
        "### Step 1: Stash, sync, restore\n\n"
        "```python\n"
        "import subprocess\n"
        "from custom_tools.vaultbot_sync import run as _sync\n"
        'r = _sync({"target": "main"})\n'
        "```\n",
        encoding="utf-8",
    )
    proc_index = {
        "Git-Sync-Upstream": {
            "path": str(proc),
            "frontmatter": {
                "type": "procedure",
                "status": "experimental",
                "description": "Syncs local repo with upstream main.",
                "when_to_use": "When you need to sync with upstream before work.",
                "tags": ["git", "sync"],
                "allowed_tools": ["code_run"],
            },
        }
    }
    idx = build_first_tool_index(proc_index)
    assert "Git-Sync-Upstream" in idx
    entry = idx["Git-Sync-Upstream"]
    assert "vaultbot_sync" in entry["first_tools"]
    assert "code_run" in entry["first_tools"]  # subprocess surfaces as code_run
    assert entry["status"] == "experimental"
    assert entry["description"] == "Syncs local repo with upstream main."
    assert "sync" in entry["triggers"]  # from tags
    # Gate should now fire end-to-end on the retrieval-selected hint.
    sug = check_procedure_suggestion("vaultbot_sync", "Git-Sync-Upstream", idx)
    assert sug is not None
    assert sug["procedure_suggestion"] == "Git-Sync-Upstream"


def test_build_index_skips_procedures_with_no_first_tool():
    # An llm-first-step procedure is skipped (gate can't match by tool).
    proc_index = {
        "Think-Only": {
            "path": "",  # no file -> compile returns None -> skipped
            "frontmatter": {},
        }
    }
    idx = build_first_tool_index(proc_index)
    assert idx == {}


# ── check_procedure_name_suggestion ─────────────────────────────────────


def _proc_index_entry(description="", status="", trigger=None, when_to_use=""):
    fm: dict = {}
    if description:
        fm["description"] = description
    if status:
        fm["status"] = status
    if trigger:
        fm["trigger"] = trigger
    if when_to_use:
        fm["when_to_use"] = when_to_use
    return {"path": "", "frontmatter": fm}


def test_name_suggestion_returns_none_on_exact_match():
    idx = {"Triage-GitHub-Issues": _proc_index_entry()}
    sug = check_procedure_name_suggestion("Triage-GitHub-Issues", idx)
    assert sug is None


def test_name_suggestion_recovers_mangled_name():
    idx = {
        "Triage-GitHub-Issues": _proc_index_entry(
            description="Triage open GitHub issues by urgency.",
            trigger=["triage github issues"],
        ),
        "Solve-GitHub-Issue": _proc_index_entry(
            description="Solve a GitHub issue and open a PR.",
            trigger=["solve github issue"],
        ),
    }
    sug = check_procedure_name_suggestion("Triage- GitHub- Issues", idx)
    assert sug is not None
    assert sug["procedure_suggestion"] == "Triage-GitHub-Issues"
    assert "Triage-GitHub-Issues" in sug["candidates"]
    assert "proceed" in sug["message"]
    assert sug["proceed_keyword"] == "proceed"


def test_name_suggestion_returns_top_k_candidates():
    idx = {
        f"Proc-{i}": _proc_index_entry(description=f"procedure number {i}")
        for i in range(10)
    }
    # "Proc- 3" (extra space) is NOT an exact match, but normalizes to the
    # same key as "Proc-3" -> should rank it first among the top-k.
    sug = check_procedure_name_suggestion("Proc- 3", idx, k=5)
    assert sug is not None
    assert len(sug["candidates"]) == 5
    assert sug["candidates"][0] == "Proc-3"


def test_name_suggestion_skips_flagged():
    idx = {
        "Triage-GitHub-Issues": _proc_index_entry(status="flagged"),
    }
    sug = check_procedure_name_suggestion("Triage-GitHub-Issues", idx)
    # Exact name matches but it's flagged -> still returns None (no suggestion
    # for a blocked procedure; the caller's status gate handles the block).
    assert sug is None


def test_name_suggestion_no_fire_when_too_dissimilar():
    idx = {
        "Triage-GitHub-Issues": _proc_index_entry(description="triage issues"),
    }
    # A completely unrelated hallucinated name -> below similarity floor.
    sug = check_procedure_name_suggestion("Zzzz-Qqqq-Wwww", idx)
    assert sug is None


def test_name_suggestion_no_fire_when_index_empty():
    sug = check_procedure_name_suggestion("Triage-GitHub-Issues", {})
    assert sug is None


def test_name_suggestion_no_fire_when_name_empty():
    idx = {"Triage-GitHub-Issues": _proc_index_entry()}
    sug = check_procedure_name_suggestion("", idx)
    assert sug is None
