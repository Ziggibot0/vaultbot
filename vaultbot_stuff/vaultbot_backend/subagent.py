import json
import os
import subprocess
import sys
import tempfile
from typing import Any

from subprocess_utils import run as _subprocess_run

"""Subagent context isolation — run verbose tools in a separate process so
the orchestrator's conversation never balloons.

A single ``vault_research`` call produces thousands of verbose events
(source-rejection logs, scrape text, a 50K-char synthesis). Running that
in-process would flood the orchestrator's context window. Instead, we build
a Python source string that runs in a subprocess. The subprocess prints ONLY
a compact JSON brief to stdout; the orchestrator gets one bounded tool result.
The full synthesis stays on disk in the created note — re-readable via
vault_search / web_read_source.

Pattern: Copilot runSubagent / Claude subagent isolation.
"""


def _backend_dir() -> str:
    """Return the absolute path to the vaultbot_backend directory."""
    return os.path.dirname(os.path.abspath(__file__))


def subagent_enabled() -> bool:
    """Check whether subagent isolation is enabled (default: on).

    Set VAULTBOT_SUBAGENT=off to disable (falls back to in-process path).
    """
    return os.environ.get("VAULTBOT_SUBAGENT", "on").lower() in ("on", "1", "yes", "true")


def build_subagent_code(topic: str, depth: str = "deep") -> str:
    """Build the Python source code for the research subagent.

    The code runs in a subprocess with VAULT_PATH set. It:
    1. Rebuilds the search backend (FreeSearch + optional SearXNG)
    2. Creates a ResearchEngine instance
    3. Runs the research dig (with vault note titles for wikilink repair)
    4. Creates a note on disk via NoteCreator
    5. Optionally restructures with LLM (skipped if already LLM-synthesized)
    6. Prints a compact JSON brief to stdout

    All diagnostic output goes to stderr. stdout is reserved for the JSON
    brief ONLY.
    """
    # Defensive allowlist: an unrecognized depth defaults to "deep" so a
    # bad payload can't inject arbitrary code via the depth parameter.
    _VALID_DEPTHS = {"deep", "shallow", "quick"}
    if depth not in _VALID_DEPTHS:
        depth = "deep"

    topic_repr = repr(topic)

    code = (
        "import json, os, sys, traceback\n"
        "from pathlib import Path\n"
        "\n"
        "def _log(msg):\n"
        "    print(msg, file=sys.stderr, flush=True)\n"
        "\n"
        "_real_stdout = sys.stdout\n"
        "sys.stdout = sys.stderr\n"
        "\n"
        "try:\n"
        f"    vault_path = os.environ.get('VAULT_PATH', '.')\n"
        f"    topic = {topic_repr}\n"
        f"    depth = {depth!r}\n"
        "\n"
        "    # --- Rebuild the search backend ---\n"
        "    searxng_manager = None\n"
        "    try:\n"
        "        from searxng_manager import SearxngManager\n"
        "        searxng_manager = SearxngManager()\n"
        "    except Exception as _e:  # noqa: BLE001 — best-effort, returns error to caller\n"
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
        "    _oc = None\n"
        "    try:\n"
        "        from llm_client import get_llm_client\n"
        "        _oc = get_llm_client()\n"
        "    except Exception as _le:\n"
        "        _log(f'[subagent] LLM client failed: {_le}')\n"
        "        raise\n"
        "    _log('[subagent] LLM client available for synthesis')\n"
        "\n"
        "    # Load vault note titles (actual filename casing) for wikilink repair.\n"
        "    # VaultGraph normalizes to lowercase, but _repair_wikilinks needs\n"
        "    # the real casing to fix case-mismatched wikilinks.\n"
        "    _vault_titles = []\n"
        "    try:\n"
        "        _vault_titles = engine._get_vault_note_titles(vault_path)\n"
        "        _log(f'[subagent] loaded {len(_vault_titles)} vault note titles')\n"
        "    except Exception as _te:  # noqa: BLE001 — best-effort, returns error to caller\n"
        "        _log(f'[subagent] title loading failed: {_te}')\n"
        "\n"
        "    report = engine.research(topic, llm_client=_oc,\n"
        "                              vault_note_titles=_vault_titles)\n"
        "    if not report.get('source_count'):\n"
        "        _log('[subagent] no sources found — aborting')\n"
        "        sys.stdout = _real_stdout\n"
        "        print(json.dumps({'status': 'empty', 'topic': topic,\n"
        "                          'source_count': 0, 'error': 'no sources found'}))\n"
        "        sys.exit(0)\n"
        "\n"
        "    _log(f'[subagent] {report[\"source_count\"]} sources, {report.get(\"fact_count\", 0)} facts')\n"
        "\n"
        "    # --- Create the linked note ---\n"
        "    from vault_indexer import VaultIndexer\n"
        "    from note_creator import NoteCreator\n"
        "    indexer = VaultIndexer(vault_path=vault_path)\n"
        "    note_creator = NoteCreator(vault_path=vault_path, indexer=indexer)\n"
        "    summary = (report.get('synthesis', '') or '')[:200].replace('\\n', ' ')\n"
        "    note_path = note_creator.create_note_from_research(\n"
        "        topic=topic, research_content=report.get('synthesis', ''),\n"
        "        summary=summary)\n"
        "\n"
        "    # If the LLM already produced a structured synthesis during research(),\n"
        "    # write it directly — skip the extractive overwrite + restructure cycle.\n"
        "    if report.get('llm_synthesized') and report.get('synthesis'):\n"
        "        _log('[subagent] writing LLM-synthesized note directly')\n"
        "        from vault_guard import assert_writable\n"
        "        assert_writable(Path(note_path))\n"
        "        Path(note_path).write_text(report['synthesis'], encoding='utf-8')\n"
        "    else:\n"
        "        # Extractive synthesis: overwrite with richer markdown\n"
        "        from vault_guard import assert_writable\n"
        "        assert_writable(Path(note_path))\n"
        "        md = engine.synthesize_note_markdown(report, summary)\n"
        "        Path(note_path).write_text(md, encoding='utf-8')\n"
        "\n"
        "        # LLM-assisted restructuring (one call)\n"
        "        _log('[subagent] attempting structured note synthesis')\n"
        "        _structured = engine.synthesize_structured_note(\n"
        "            report, summary, ollama_client=_oc,\n"
        "            vault_note_titles=_vault_titles)\n"
        "        if _structured and len(_structured) >= engine._STRUCTURED_MIN_CHARS:\n"
        "            Path(note_path).write_text(_structured, encoding='utf-8')\n"
        "            _log(f'[subagent] structured note written ({len(_structured)} chars)')\n"
        "\n"
        "    # --- A-MEM evolution (best-effort, LLM-free) ---\n"
        "    try:\n"
        "        from amem_evolution import AMemeEvolution\n"
        "        from vault_graph import VaultGraph\n"
        "        graph = VaultGraph(vault_path=vault_path)\n"
        "        amem = AMemeEvolution(graph=graph, ollama_client=None)\n"
        "        amem.evolve_on_create(note_path, report.get('synthesis', ''),\n"
        "                              heuristic_only=True, skip_refresh=True)\n"
        "    except Exception as _e:  # noqa: BLE001 — best-effort, returns error to caller\n"
        "        _log(f'[subagent] A-MEM evolution skipped: {_e}')\n"
        "\n"
        "    # --- Index the new note ---\n"
        "    try:\n"
        "        indexer._add_file_to_index(Path(note_path))\n"
        "    except Exception as _e:  # noqa: BLE001 — best-effort, returns error to caller\n"
        "        _log(f'[subagent] index failed (note still on disk): {_e}')\n"
        "\n"
        "    # --- Build the compact brief (the ONLY thing on stdout) ---\n"
        "    synth = str(report.get('synthesis', '') or '')\n"
        "    facts = report.get('synthesis_facts') or []\n"
        "    facts_txt = ''\n"
        "    if isinstance(facts, list):\n"
        "        facts_txt = chr(10).join(f'- {str(f)[:300]}' for f in facts[:8])\n"
        "    else:\n"
        "        facts_txt = str(facts)[:500]\n"
        "\n"
        "    sys.stdout = _real_stdout\n"
        "    _brief = {\n"
        "        'status': 'ok',\n"
        "        'note_path': str(note_path),\n"
        "        'source_count': report.get('source_count', 0),\n"
        "        'fact_count': report.get('fact_count', 0),\n"
        "        'synthesis_brief': synth[:500] +\n"
        "            chr(10) + '*[... full synthesis in the note at note_path ...]*'\n"
        "            if len(synth) > 500 else synth,\n"
        "        'key_facts': facts_txt[:1000],\n"
        "        'llm_synthesized': report.get('llm_synthesized', False),\n"
        "    }\n"
        "    print(json.dumps(_brief))\n"
        "\n"
        "except Exception as _e:  # noqa: BLE001 — best-effort, returns error to caller\n"
        "    try:\n"
        "        sys.stdout = sys.stderr\n"
        "    except Exception:  # noqa: BLE001 — best-effort, returns error to caller\n"
        "        pass\n"
        "    _log(f'[subagent] FATAL: {_e}')\n"
        "    _log(traceback.format_exc())\n"
        "    try:\n"
        "        sys.stdout = _real_stdout\n"
        "        print(json.dumps({'status': 'error', 'error': str(_e)}))\n"
        "    except Exception:  # noqa: BLE001 — best-effort, returns error to caller\n"
        "        pass\n"
    )
    return code


# Alias for the original name used by tests + callers before the rename.
_build_research_wrapper = build_subagent_code


def _run_subprocess(wrapper_code: str,
                    session_logger: Any = None,
                    timeout: int = 180,
                    log_tag: str = "subagent") -> dict:
    """Generic subprocess runner — executes ``wrapper_code`` in a child
    process and returns a JSON brief dict.

    This is the primitive that ``run_research_subagent`` and the
    ``run_subagent`` dispatcher build on.  It handles:
    - timeout → ``{"status":"error","error":"...timed out...","subagent":True}``
    - no stdout → ``{"status":"error","error":"...no stdout...","subagent":True}``
    - child error brief → returned as-is (child's own error handling preserved)
    - non-JSON stdout → ``{"status":"error","error":"...not valid JSON...","subagent":True}``

    Session logger (if given) receives ``<log_tag>_start`` and
    ``<log_tag>_done`` events.
    """

    if session_logger:
        session_logger.log(f"{log_tag}_start", {"timeout": timeout})

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(wrapper_code)
        script_path = f.name

    try:
        env = os.environ.copy()
        if "VAULT_PATH" not in env:
            env["VAULT_PATH"] = os.path.dirname(_backend_dir())
        backend = _backend_dir()
        existing_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = backend + (os.pathsep + existing_path if existing_path else "")

        result = _subprocess_run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=backend,
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if session_logger and stderr:
            session_logger.log(f"{log_tag}_stderr", {"tail": stderr[-500:]})

        if not stdout:
            if session_logger:
                session_logger.log(f"{log_tag}_no_output", {
                    "returncode": result.returncode,
                    "stderr_tail": stderr[-300:] if stderr else "",
                })
            return {"status": "error",
                    "error": f"no stdout from subagent (rc={result.returncode})",
                    "subagent": True}

        try:
            brief = json.loads(stdout)
        except json.JSONDecodeError:
            if session_logger:
                session_logger.log(f"{log_tag}_json_parse_failed", {
                    "stdout_tail": stdout[-300:],
                })
            return {"status": "error",
                    "error": "subagent output was not valid JSON",
                    "subagent": True}

        # The brief must be a dict to be useful.  A JSON string / number /
        # list is technically valid JSON but not a usable brief — treat it
        # as a parse failure so the caller gets a clean error.
        if not isinstance(brief, dict):
            if session_logger:
                session_logger.log(f"{log_tag}_non_dict_brief", {
                    "type": type(brief).__name__,
                })
            return {"status": "error",
                    "error": "subagent output was not a JSON object",
                    "subagent": True}

        if session_logger:
            session_logger.log(f"{log_tag}_done", {
                "status": brief.get("status"),
            })

        return brief

    except subprocess.TimeoutExpired:
        if session_logger:
            session_logger.log(f"{log_tag}_timeout", {})
        return {"status": "error",
                "error": f"subagent timed out after {timeout}s",
                "subagent": True}
    finally:
        try:
            os.unlink(script_path)
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            pass


# Default timeout for the dispatcher (seconds).
_DEFAULT_TIMEOUT = 180


def run_subagent(task_type: str, payload: dict,
                 session_logger: Any = None,
                 timeout: int | None = None) -> dict:
    """Dispatch a subagent task by type.

    Currently only ``task_type="research"`` is supported.  Unknown types
    return a clean error dict (never raise).

    ``payload`` for research: ``{"topic": str, "depth": str}``.
    """
    if task_type == "research":
        topic = payload.get("topic", "")
        depth = payload.get("depth", "deep")
        wrapper = build_subagent_code(topic, depth)
        return _run_subprocess(
            wrapper, session_logger=session_logger,
            timeout=timeout or _DEFAULT_TIMEOUT,
            log_tag="subagent_research")

    return {"status": "error",
            "error": f"unknown subagent task_type: {task_type!r}",
            "subagent": True}


def run_research_subagent(topic: str, depth: str = "deep",
                          session_logger: Any = None) -> dict:
    """Run a research subagent for the given topic.

    Returns a compact JSON brief dict with keys:
    - status: 'ok' | 'empty' | 'error'
    - note_path: path to the created note (if any)
    - source_count, fact_count: research stats
    - synthesis_brief: first 500 chars of synthesis
    - key_facts: up to 8 fact summaries
    - llm_synthesized: whether the LLM produced structured synthesis
    """
    code = build_subagent_code(topic, depth)
    return _run_subprocess(
        code, session_logger=session_logger,
        timeout=300, log_tag="subagent")
