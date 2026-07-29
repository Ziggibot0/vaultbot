"""Subagent context isolation — run verbose tools in a separate process so
their output never floods the orchestrator's conversation.

THE PROBLEM THIS SOLVES
-----------------------
A single ``vault_research`` call produces thousands of verbose events
(source rejections, scrapes, facet fills) and a multi-KB report. When that
work runs in-process, every byte of it is available to leak into the
orchestrator's conversation context — and even with ``truncate_tool_result``
capping the final result, the intermediate work ballooned the conversation
past the compaction threshold every round, causing the "read-loop wall"
where the model spins without converging.

This module is the Copilot ``runSubagent`` / Claude Code subagent pattern:
spawn a real OS subprocess per verbose tool call. The subprocess does the
noisy work in its own memory + stdout, then prints ONLY a compact JSON
brief to stdout. The orchestrator's conversation gets one bounded result,
not a flood.

WHY A SUBPROCESS (not a thread)
-------------------------------
A thread shares the GIL, the asyncio event loop, and the Services
singletons — a crash or exception in the thread leaks into the orchestrator.
A subprocess is a hard isolation boundary: if it hangs, OOMs, or dies, the
orchestrator receives a clean timeout/error, not a corrupted loop. This is
the same proven pattern ``step_gate_runtime._run_code_step`` already uses
for procedure code steps.

CONTRACT
--------
- The child receives configuration via ENVIRONMENT VARIABLES only (never a
  pickled ``Services`` object — env is safe, inspectable, and decoupled
  from internal class versions). The child rebuilds its own singletons
  from ``VAULT_PATH`` + the search/env config.
- The child prints ONLY the final JSON result to stdout. All diagnostic
  logging goes to stderr. A stray ``print()`` on stdout would corrupt the
  JSON parse — every wrapper is written to enforce this.
- On any failure (timeout, non-zero exit, unparseable stdout), the
  orchestrator gets a clean error dict. This function NEVER raises — the
  chat loop stays safe.
- The subagent is NOT VaultBot: it has no identity, no self-model, no chat
  history. It is a disposable worker that exists for one tool call.

ENVIRONMENT KNOBS
-----------------
- ``VAULTBOT_SUBAGENT`` (default ``on``) — set ``off`` to fall back to the
  in-process path (debugging / safety net).
- ``VAULTBOT_SUBAGENT_TIMEOUT`` (default ``180``) — hard kill the child after
  this many seconds. Research can legitimately take 60-120s.

See:
  - ``step_gate_runtime._run_code_step`` — the proven subprocess template
    this module is modeled on.
  - ``chat_handler.execute_agent_tool`` — the caller for the research path.
  - [[Procedure-Subprocess-Architecture]] — the design spec.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Default hard timeout for a subagent call. Research can legitimately take
# 60-120s on a remote cloud model round; 180s leaves headroom without
# letting a truly stuck child hang the chat indefinitely.
_DEFAULT_TIMEOUT = int(os.getenv("VAULTBOT_SUBAGENT_TIMEOUT", "180"))

# Bounded brief size: the synthesis returned to the orchestrator is capped
# so the conversation can't balloon even if the dig was huge. The full
# synthesis stays on disk in the created note — re-readable via
# vault_search / web_read_source.
_SYNTHESIS_BRIEF_CAP = 1500
_KEY_FACTS_CAP = 8


def _backend_dir() -> Path:
    """The vaultbot_backend directory (this file's parent)."""
    return Path(__file__).parent.resolve()


def _venv_python() -> str:
    """Resolve the venv python executable, mirroring step_gate_runtime.

    Returns the venv python if present (so the child has faiss/numpy/etc.),
    falling back to the current interpreter. Wrong interpreter = the child
    can't import faiss → immediate crash.

    Checks both venv layouts used on this repo: ``vaultbot_venv`` (the one
    ``step_gate_runtime`` targets) and ``.venv`` (the one the test suite +
    the running backend use). Prefers whichever exists; the fallback to
    ``sys.executable`` guarantees the child at least shares the parent's
    environment (and its installed packages) if neither venv dir is found.
    """
    backend_parent = _backend_dir().parent
    for venv_name in ("vaultbot_venv", ".venv"):
        candidate = backend_parent / venv_name / "Scripts" / "python.exe"
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _build_research_wrapper(topic: str, depth: str) -> str:
    """Build the Python wrapper script for a research subagent call.

    The wrapper is a self-contained Python program (run via ``python -c``)
    that:
      1. Rebuilds the search client + research engine from env + vault_path.
      2. Runs the LLM-free multi-round dig.
      3. Creates a linked note (respecting the vault write guard) + indexes it.
      4. Runs LLM-free A-MEM evolution on the new note.
      5. Prints ONLY the compact JSON brief to stdout. All logging → stderr.

    The script is built with string concatenation (NOT .format()) so the
    topic's braces/quotes don't break the template — same approach as
    ``step_gate_runtime._run_code_step``.
    """
    # Defensive: depth is a small allowlist. Anything unrecognized defaults
    # to "deep" so a model-supplied string can't inject arbitrary code via
    # the depth field (it's repr'd below, but defense-in-depth is cheap).
    if depth not in ("deep", "quick"):
        depth = "deep"
    # NOTE: the topic is injected via repr() so it's a safe Python string
    # literal, immune to injection. depth is validated above.
    return (
        "import json, os, sys, traceback\n"
        "from pathlib import Path\n"
        "\n"
        "# All diagnostic output → stderr. stdout is reserved for the final\n"
        "# JSON brief ONLY. A stray print() on stdout corrupts the parse.\n"
        "def _log(msg):\n"
        "    print(msg, file=sys.stderr, flush=True)\n"
        "\n"
        "try:\n"
        "    vault_path = os.environ.get('VAULT_PATH', '.')\n"
        "    topic = " + repr(topic) + "\n"
        "    depth = " + repr(depth) + "\n"
        "\n"
        "    # --- Rebuild the search backend (no Services object crosses) ---\n"
        "    # SearXNG is optional; a missing Docker must not crash the child.\n"
        "    searxng_manager = None\n"
        "    try:\n"
        "        from searxng_manager import SearxngManager\n"
        "        searxng_manager = SearxngManager()\n"
        "    except Exception as _e:\n"
        "        _log(f'[subagent] SearXNG disabled: {_e}')\n"
        "    from free_search import FreeSearch\n"
        "    search_client = FreeSearch(searxng_manager=searxng_manager)\n"
        "\n"
        "    from research_engine import ResearchEngine\n"
        "    engine = ResearchEngine(\n"
        "        max_rounds=int(os.environ.get('VAULTBOT_RESEARCH_ROUNDS', '4')),\n"
        "        max_sources_per_round=int(os.environ.get('VAULTBOT_RESEARCH_SOURCES', '5')),\n"
        "        max_follow_ups=int(os.environ.get('VAULTBOT_RESEARCH_FOLLOWUPS', '3')),\n"
        "        search_client=search_client,\n"
        "    )\n"
        "    if depth == 'quick':\n"
        "        engine.max_rounds = 1\n"
        "        engine.max_follow_ups = 0\n"
        "\n"
        "    _log(f'[subagent] researching: {topic}')\n"
        "    report = engine.research(topic)\n"
        "    if not report.get('source_count'):\n"
        "        print(json.dumps({'status': 'empty', 'topic': topic,\n"
        "                          'source_count': 0, 'error': 'no sources found'}))\n"
        "        sys.exit(0)\n"
        "\n"
        "    # --- Create the linked note (respecting the vault write guard) ---\n"
        "    from vault_indexer import VaultIndexer\n"
        "    from note_creator import NoteCreator\n"
        "    indexer = VaultIndexer(vault_path=vault_path)\n"
        "    note_creator = NoteCreator(vault_path=vault_path, indexer=indexer)\n"
        "    summary = (f\"Research into '{topic}' ({report['source_count']} \"\n"
        "               f\"sources, {report.get('synthesis_facts', 0)} facts).\")\n"
        "    note_path = note_creator.create_note_from_research(\n"
        "        topic=topic, research_content=report.get('synthesis', ''),\n"
        "        summary=summary)\n"
        "    # Overwrite with the richer markdown so sources + follow-ups persist.\n"
        "    from vault_guard import assert_writable\n"
        "    assert_writable(Path(note_path))\n"
        "    md = engine.synthesize_note_markdown(report, summary)\n"
        "    Path(note_path).write_text(md, encoding='utf-8')\n"
        "\n"
        "    # --- LLM-free A-MEM evolution (no cloud calls from the child) ---\n"
        "    try:\n"
        "        from amem_evolution import AMemeEvolution\n"
        "        from vault_graph import VaultGraph\n"
        "        graph = VaultGraph(vault_path=vault_path)\n"
        "        amem = AMemeEvolution(graph=graph, ollama_client=None)\n"
        "        amem.evolve_on_create(note_path, report.get('synthesis', ''),\n"
        "                              heuristic_only=True, skip_refresh=True)\n"
        "    except Exception as _e:\n"
        "        _log(f'[subagent] A-MEM evolution skipped: {_e}')\n"
        "\n"
        "    # --- Index the new note so the orchestrator can find it ---\n"
        "    try:\n"
        "        indexer._add_file_to_index(Path(note_path))\n"
        "    except Exception as _e:\n"
        "        _log(f'[subagent] index failed (note still on disk): {_e}')\n"
        "\n"
        "    # --- Build the compact brief (the ONLY thing on stdout) ---\n"
        "    synth = str(report.get('synthesis', '') or '')\n"
        "    facts = report.get('synthesis_facts') or []\n"
        "    facts_txt = ''\n"
        "    if isinstance(facts, list):\n"
        "        facts_txt = chr(10).join(f'- {str(f)[:300]}' for f in facts[:8])\n"
        "    else:\n"
        "        facts_txt = str(facts)[:1500]\n"
        "    brief = {\n"
        "        'status': 'ok',\n"
        "        'topic': report.get('topic', topic),\n"
        "        'source_count': report.get('source_count', 0),\n"
        "        'note_path': str(note_path),\n"
        "        'synthesis_brief': synth[:1500] + (\n"
        "            chr(10) + '*[... full synthesis in the note at note_path ...]*'\n"
        "            if len(synth) > 1500 else ''),\n"
        "        'key_facts': facts_txt,\n"
        "        'duration_ms': report.get('duration_ms', 0),\n"
        "        'subagent_note': (\n"
        "            'Verbose dig output kept OUT of context (subagent '\n"
        "            'isolation). Full synthesis is in the created note; '\n"
        "            're-read it via vault_search/web_read_source if you '\n"
        "            'need a specific detail.'),\n"
        "    }\n"
        "    print(json.dumps(brief, ensure_ascii=False))\n"
        "except Exception as e:\n"
        "    # Loud failure: print an error brief to stdout (so the orchestrator\n"
        "    # always gets parseable JSON) + the traceback to stderr.\n"
        "    print(json.dumps({'status': 'error', 'error': str(e),\n"
        "                      'traceback_head': traceback.format_exc()[:1500]},\n"
        "                     ensure_ascii=False))\n"
        "    sys.exit(1)\n"
    )


def _run_subprocess(
    wrapper: str,
    session_logger: Any = None,
    timeout: int = _DEFAULT_TIMEOUT,
    log_tag: str = "subagent",
) -> dict[str, Any]:
    """Run a wrapper script in a subprocess and return its parsed JSON brief.

    This is the low-level primitive. It NEVER raises — on any failure it
    returns a clean error dict so the orchestrator's chat loop stays safe.
    """
    backend = _backend_dir()
    vault_root = str(backend.parent.resolve())
    env = {
        **os.environ,
        # Ensure the child can import vaultbot_backend modules.
        "PYTHONPATH": str(backend),
        # Inherit VAULT_PATH (the orchestrator sets it); default to the vault
        # root if unset so the child writes notes to the right place.
        "VAULT_PATH": os.environ.get("VAULT_PATH", vault_root),
    }
    # KMP_DUPLICATE_LIB_OK MUST be inherited (it's in os.environ from main.py)
    # or the child crashes on importing faiss (OpenMP duplicate-init).

    t0 = time.time()
    if session_logger is not None:
        try:
            session_logger.log(f"{log_tag}_start", {"timeout": timeout})
        except Exception:
            pass

    try:
        proc = subprocess.run(
            [_venv_python(), "-c", wrapper],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=vault_root,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        if session_logger is not None:
            try:
                session_logger.log(f"{log_tag}_timeout", {"timeout": timeout})
            except Exception:
                pass
        return {
            "status": "error",
            "error": f"subagent timed out after {timeout}s",
            "subagent": True,
        }
    except Exception as e:
        if session_logger is not None:
            try:
                session_logger.log(f"{log_tag}_spawn_failed", {"error": str(e)})
            except Exception:
                pass
        return {
            "status": "error",
            "error": f"subagent spawn failed: {e}",
            "subagent": True,
        }

    duration_ms = int((time.time() - t0) * 1000)

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if not stdout:
        if session_logger is not None:
            try:
                session_logger.log(f"{log_tag}_no_stdout", {
                    "returncode": proc.returncode,
                    "stderr_head": stderr[:500],
                })
            except Exception:
                pass
        return {
            "status": "error",
            "error": "subagent produced no stdout",
            "returncode": proc.returncode,
            "stderr_head": stderr[:500],
            "subagent": True,
        }

    try:
        brief = json.loads(stdout)
    except json.JSONDecodeError:
        if session_logger is not None:
            try:
                session_logger.log(f"{log_tag}_bad_json", {
                    "stdout_head": stdout[:500],
                })
            except Exception:
                pass
        return {
            "status": "error",
            "error": "subagent stdout was not valid JSON",
            "stdout_head": stdout[:500],
            "subagent": True,
        }

    if session_logger is not None:
        try:
            session_logger.log(f"{log_tag}_done", {
                "duration_ms": duration_ms,
                "status": brief.get("status") if isinstance(brief, dict) else None,
                "source_count": brief.get("source_count", 0) if isinstance(brief, dict) else 0,
                "note_path": brief.get("note_path") if isinstance(brief, dict) else None,
            })
        except Exception:
            pass

    # Tag every brief with subagent=True so callers can distinguish the
    # isolated path from the in-process fallback. Guard: a child that
    # printed a bare JSON string/list (not a dict) can't be tagged — wrap
    # it so the orchestrator always gets a dict with the subagent flag.
    if not isinstance(brief, dict):
        brief = {"status": "error",
                 "error": "subagent stdout was not a JSON object",
                 "raw": str(brief)[:500],
                 "subagent": True}
    else:
        brief["subagent"] = True
    return brief


def run_research_subagent(
    topic: str,
    depth: str = "deep",
    session_logger: Any = None,
) -> dict[str, Any]:
    """Run a vault_research call in an isolated subprocess.

    Returns a compact brief dict (the SAME shape as the in-process
    distillation output) so the chat loop + ``truncate_tool_result`` +
    ``goal_hint`` all work unchanged. NEVER raises.

    Args:
        topic: The research topic (already validated non-empty by caller).
        depth: "deep" (default) or "quick" — maps to research rounds.
        session_logger: Optional SessionLogger for structured logging.

    Returns:
        A brief dict with keys: status, topic, source_count, note_path,
        synthesis_brief, key_facts, subagent_note, duration_ms, subagent.
        On failure: {status: "error", error: ..., subagent: True}.
    """
    # Defensive: depth is a small allowlist (the model can pass arbitrary
    # strings). Anything unrecognized defaults to "deep".
    if depth not in ("deep", "quick"):
        depth = "deep"
    wrapper = _build_research_wrapper(topic, depth)
    return _run_subprocess(
        wrapper, session_logger=session_logger, log_tag="subagent_research")


# ── Dispatcher (the "set the example" piece) ────────────────────────────
# A single entry point so future verbose tools (textbook_ingest, a future
# code-audit subagent) reuse the same isolation primitive. Each task type
# has a wrapper builder; the dispatcher routes to the right one. Adding a
# new subagent is one builder function + one entry in the dispatch table —
# not a new subprocess harness.

# Task type → wrapper-builder mapping. Builders take a payload dict and
# return the wrapper script string. Kept as a function (not a module-level
# dict) so it's evaluated lazily — importing subagent.py doesn't require
# the builders to be defined at module init.
def _dispatch(task_type: str, payload: dict[str, Any]) -> str:
    builders = {
        "research": lambda p: _build_research_wrapper(
            p.get("topic", ""), p.get("depth", "deep")),
    }
    builder = builders.get(task_type)
    if builder is None:
        raise ValueError(f"unknown subagent task_type: {task_type!r}")
    return builder(payload)


def run_subagent(
    task_type: str,
    payload: dict[str, Any],
    session_logger: Any = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Run a verbose tool call in an isolated subprocess (general dispatcher).

    This is the single entry point for the subagent pattern. Each verbose
    tool registers a wrapper builder in ``_dispatch``; the dispatcher routes
    to the right one. The orchestrator calls this instead of running the
    tool in-process, so the tool's output stays bounded in the
    conversation regardless of how noisy the tool actually is.

    Args:
        task_type: The registered task (currently: "research").
        payload: Task-specific arguments (e.g. {"topic": "...", "depth": "..."}).
        session_logger: Optional SessionLogger.
        timeout: Optional override for the default timeout.

    Returns:
        The parsed JSON brief from the subprocess. NEVER raises.
    """
    try:
        wrapper = _dispatch(task_type, payload)
    except Exception as e:
        return {"status": "error", "error": str(e), "subagent": True}
    return _run_subprocess(
        wrapper, session_logger=session_logger,
        timeout=timeout or _DEFAULT_TIMEOUT, log_tag=f"subagent_{task_type}")


def subagent_enabled() -> bool:
    """Is the subagent path active? (env VAULTBOT_SUBAGENT, default on)."""
    return os.getenv("VAULTBOT_SUBAGENT", "on").lower() != "off"