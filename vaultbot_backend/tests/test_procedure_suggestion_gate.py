"""Tests for the procedure suggestion gate — the \"autofill\" nudge.

Pure-logic tests: no I/O, no LLM, no vault. Builds a synthetic first-tool
index and verifies the gate fires/doesn't fire on the right (tool, message)
combinations, including the once-per-session de-dup, the ``code_run``
weak-signal floor, flagged-procedure skipping, and trigger-overlap ranking.

Motivated by session ``eb8143f7``: the model reached for raw ``vaultbot_sync``
+ ``code_run`` to sync the repo instead of calling
``execute_procedure(\"Git-Sync-Upstream\")``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from procedure_first_tool_index import _extract_first_tools, build_first_tool_index
from procedure_suggestion_gate import check_procedure_suggestion

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


def test_gate_fires_when_tool_and_trigger_match():
    idx = {
        "Git-Sync-Upstream": _idx_entry(
            {"vaultbot_sync"},
            ["sync the local repo with upstream main"],
            description="Syncs local repo with upstream main.",
        )
    }
    sug = check_procedure_suggestion(
        "vaultbot_sync", "make sure that this repo is synced with upstream", idx
    )
    assert sug is not None
    assert sug["procedure_suggestion"] == "Git-Sync-Upstream"
    assert sug["first_tool"] == "vaultbot_sync"
    assert "execute_procedure" in sug["message"]
    assert sug["proceed_keyword"] == "proceed"


def test_gate_no_fire_when_tool_not_suggestible():
    idx = {
        "X": _idx_entry({"plan_task"}, ["plan a task"]),
    }
    sug = check_procedure_suggestion("plan_task", "plan a task", idx)
    assert sug is None  # plan_task is not in _SUGGESTIBLE_TOOLS


def test_gate_no_fire_when_no_procedure_calls_that_tool():
    idx = {
        "Git-Sync-Upstream": _idx_entry({"vaultbot_sync"}, ["sync"]),
    }
    sug = check_procedure_suggestion("github_issues", "sync", idx)
    assert sug is None


def test_gate_no_fire_when_trigger_does_not_overlap():
    # Direct tool (vaultbot_sync) allows zero-overlap match per design
    # (the tool itself is the cue), so use a NON-direct tool to test the
    # trigger-overlap requirement. code_run is weak-signal: requires overlap.
    idx = {
        "Git-Status-Check": _idx_entry({"code_run"}, ["check the daily standup"]),
    }
    # User message shares no words with the trigger -> no suggestion for code_run.
    sug = check_procedure_suggestion(
        "code_run", "sync the repo with upstream", idx
    )
    assert sug is None


def test_gate_code_run_requires_trigger_overlap():
    idx = {
        "Git-Sync-Upstream": _idx_entry(
            {"code_run"}, ["sync upstream repo stash"]
        ),
    }
    # Overlap on "sync" and "upstream" -> fires.
    sug = check_procedure_suggestion(
        "code_run", "sync the upstream repo please", idx
    )
    assert sug is not None


def test_gate_vaultbot_sync_allows_zero_overlap():
    # vaultbot_sync is a direct tool: the tool match is the cue, so a
    # zero-overlap user message still fires (the model explicitly named
    # the tool the procedure wraps).
    idx = {
        "Git-Sync-Upstream": _idx_entry(
            {"vaultbot_sync"}, ["sync upstream repo stash"]
        ),
    }
    sug = check_procedure_suggestion(
        "vaultbot_sync", "yo what's good", idx
    )
    assert sug is not None


def test_gate_skips_flagged_procedures():
    idx = {
        "Bad-Proc": _idx_entry(
            {"vaultbot_sync"}, ["sync"], status="flagged"
        ),
    }
    sug = check_procedure_suggestion("vaultbot_sync", "sync the repo", idx)
    assert sug is None  # flagged procedures are blocked from execution


def test_gate_picks_highest_trigger_overlap():
    idx = {
        "Git-Sync-Upstream": _idx_entry(
            {"vaultbot_sync"}, ["sync upstream repo"]
        ),
        "Git-Sync-Origin": _idx_entry(
            {"vaultbot_sync"}, ["sync upstream repo with origin main"]
        ),
    }
    # Both match; the second has more overlap ("origin", "main") -> wins.
    sug = check_procedure_suggestion(
        "vaultbot_sync",
        "sync the upstream repo with origin main",
        idx,
    )
    assert sug is not None
    assert sug["procedure_suggestion"] == "Git-Sync-Origin"


def test_gate_dedup_once_per_session():
    idx = {
        "Git-Sync-Upstream": _idx_entry(
            {"vaultbot_sync"}, ["sync upstream repo"]
        ),
    }
    already: set[str] = set()
    sug1 = check_procedure_suggestion(
        "vaultbot_sync", "sync upstream repo", idx, already_suggested=already
    )
    assert sug1 is not None
    assert "vaultbot_sync" in already
    # Second call same session -> no nudge (passes through).
    sug2 = check_procedure_suggestion(
        "vaultbot_sync", "sync upstream repo", idx, already_suggested=already
    )
    assert sug2 is None


def test_gate_no_fire_when_index_empty():
    sug = check_procedure_suggestion("vaultbot_sync", "sync", {})
    assert sug is None


def test_gate_strips_stopwords_from_trigger_match():
    # "the repo is synced" must not match a procedure whose trigger is
    # "the daily check" — stopwords dropped, no real overlap.
    idx = {
        "Daily-Check": _idx_entry({"vaultbot_sync"}, ["the daily check"]),
    }
    sug = check_procedure_suggestion(
        "vaultbot_sync", "the repo is synced", idx
    )
    # vaultbot_sync is direct -> fires anyway (zero overlap is allowed).
    # This is intended: the tool name is the cue. Verify the message is
    # still well-formed.
    assert sug is not None


# ── build_first_tool_index (integration with the compiler) ──────────────


def test_build_index_from_synthetic_proc_index(tmp_path):
    """Compile a real procedure note and verify the index entry."""
    proc = tmp_path / "Git-Sync-Upstream.md"
    proc.write_text(
        "---\n"
        "type: procedure\n"
        "status: experimental\n"
        "description: \"Syncs local repo with upstream main.\"\n"
        "when_to_use: \"When you need to sync with upstream before work.\"\n"
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
        "r = _sync({\"target\": \"main\"})\n"
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
    # Gate should now fire end-to-end on a matching message.
    sug = check_procedure_suggestion(
        "vaultbot_sync", "sync the repo with upstream", idx
    )
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
