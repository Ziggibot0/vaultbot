import os
import sys
import re
import json
import asyncio
import atexit
from pathlib import Path
from typing import Any, Dict, Optional, List

# ─── OpenMP conflict guard ──────────────────────────────────────────────────
# faster-whisper (CTranslate2) ships libomp140.dll while faiss/torch/onnxruntime
# ship libiomp5md.dll. When both load in one process (e.g. /stt runs whisper
# after faiss/onnxruntime are already loaded), OpenMP init crashes the whole
# backend. This must be set BEFORE any numpy/torch/faiss import. The official
# workaround per the OMP error message.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from dotenv import load_dotenv

# Import our modules
from ollama_client import OllamaClient
from llm_client import get_llm_client, get_vision_client, LLMClient
from vault_indexer import VaultIndexer
from vault_graph import VaultGraph, build_graph_context
from note_creator import NoteCreator
from session_logger import SessionLogger
from research_engine import ResearchEngine
from autonomous_researcher import AutonomousResearcher
from agent_tools import TOOL_DEFINITIONS, META_TOOL_DEFINITIONS, build_system_prompt
from self_improver import SelfImprover
from knowledge_curriculum import KnowledgeCurriculum
from plan_executor import PlanExecutor, Plan, Subtask
from identity import Identity
from graph_ops import GraphOpRegistry, SCHEMAS as GRAPH_OP_SCHEMAS
from amem_evolution import AMemeEvolution
from fused_retrieval import FusedRetriever
from compactor import Compactor
from lazy_condenser import LazyCondenser
from concept_card import build_cards_batch, card_path_for, needs_refine, refine_card, is_card
from moc_builder import build_mocs, build_mocs_incremental, MOC_PREFIX
from abstract_context import build_abstract_context
from embedding_drift import EmbeddingDrift
from supervision import HealthMonitor, generate_nssm_install, generate_nssm_uninstall
from checkpointer import Checkpointer, ResearchCheckpoint
from duckduckgo_client import DuckDuckGoClient
from free_search import FreeSearch
try:
    from forum_backends import ForumEnhancedFreeSearch
    # Use the forum-enhanced version: adds GitHub Issues + StackOverflow
    # backends, skips arXiv for technical queries, prioritizes forum results.
    FreeSearch = ForumEnhancedFreeSearch
except Exception as _forum_err:
    print(f"[startup] Forum backends unavailable, using base FreeSearch: {_forum_err}")
from procedure_tracker import ProcedureTracker, parse_procedures_from_results, interpret_validation_result
from speech import transcribe as stt_transcribe, synthesize as tts_synthesize, list_voices as tts_voices, set_logger as speech_set_logger
from context_budgeter import ContextBudgeter
from calibration import CalibrationTracker
from rag_eval import RAGEvaluator
from claim_verifier import ClaimVerifier
from pattern_extractor import PatternExtractor

# Load environment variables from the parent directory (Vault2 root).
# override=True ensures .env values win over any stale env passed by the
# Obsidian plugin spawn (which used to carry an empty TAVILY_API_KEY).
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path, override=True)

# ─── Startup import sanity check ─────────────────────────────────────────────
# Scans this file for names used in direct constructor/function calls that are
# not imported, not built-in, not locally defined, and not function parameters.
# Catches "forgot the import" bugs BEFORE the server boots, so they fail loudly
# at startup instead of silently breaking on the next restart.
def _verify_imports():
    import ast as _ast
    import builtins as _builtins
    with open(__file__, encoding="utf-8") as _f:
        _tree = _ast.parse(_f.read())

    _imported = set()
    for _node in _ast.walk(_tree):
        if isinstance(_node, _ast.ImportFrom):
            for _a in _node.names:
                _imported.add(_a.asname or _a.name)
        elif isinstance(_node, _ast.Import):
            for _a in _node.names:
                _imported.add(_a.asname or _a.name.split('.')[0])

    _called = set()
    for _node in _ast.walk(_tree):
        if isinstance(_node, _ast.Call) and isinstance(_node.func, _ast.Name):
            _called.add(_node.func.id)

    _defined = set()
    _params = set()
    for _node in _ast.walk(_tree):
        if isinstance(_node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            _defined.add(_node.name)
            for _arg in _node.args.args + list(_node.args.posonlyargs) + list(_node.args.kwonlyargs):
                _params.add(_arg.arg)
            if _node.args.vararg:
                _params.add(_node.args.vararg.arg)
            if _node.args.kwarg:
                _params.add(_node.args.kwarg.arg)
        elif isinstance(_node, _ast.ClassDef):
            _defined.add(_node.name)

    _undefined = _called - _imported - set(dir(_builtins)) - _defined - _params
    if _undefined:
        _msg = (
            f"[VaultBot] FATAL: {len(_undefined)} name(s) used in calls but not "
            f"imported or defined: {', '.join(sorted(_undefined))}. "
            f"Add the missing import(s) to main.py before restarting."
        )
        print(_msg, file=sys.stderr)
        sys.exit(1)

_verify_imports()
# ─── End import sanity check ─────────────────────────────────────────────────

# Prevent duplicate backend instances on the same vault.
PID_FILE = Path(__file__).with_name('vaultbot.pid')

def _check_pid_alive(pid: int) -> bool:
    if os.name == 'nt':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(1, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

def acquire_lock() -> None:
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
        except Exception:
            old_pid = None
        if old_pid and _check_pid_alive(old_pid):
            print(f"VaultBot backend already running (PID {old_pid}). Exiting.")
            sys.exit(0)
    PID_FILE.write_text(str(os.getpid()))

def release_lock() -> None:
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except Exception:
        pass

acquire_lock()
atexit.register(release_lock)

app = FastAPI(title="VaultBot API")

# Allow the Obsidian Electron app (origin app://obsidian.md) and local browsers
# to call the API without browser CORS preflight blocks.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Default global session logger for startup/shutdown and background tasks.
default_session_logger = SessionLogger()
speech_set_logger(default_session_logger)

# Initialize the synthesis LLM client. This is the ONLY step that spends
# tokens, so it's the one swappable surface: local Ollama (free, private,
# heavy) OR any OpenAI-compatible API (OpenAI, OpenRouter->Anthropic, Gemini,
# vLLM, LM Studio, ...) via a key the user brings. A weak-laptop user sets
# LLM_BACKEND=openai + LLM_API_KEY + LLM_MODEL in .env and runs zero local
# compute; the research loop stays token-free either way. See llm_client.py.
# Embeddings are a SEPARATE concern and stay on OllamaClient (nomic-embed-text,
# ~270MB, light enough for a weak laptop) inside vault_indexer.
ollama_client = get_llm_client(session_logger=default_session_logger)
# OPTIONAL dedicated vision model for reading textbook pages. This is a
# SEPARATE concern from the synthesis client: a user can keep a fast/cheap
# text-only chat model and delegate page-reading (textbook_read_page) to a
# vision-capable model on its own backend. None when no VISION_MODEL is set,
# in which case the page reader falls back to the synthesis client's own
# vision_capable() probe (so a vision-capable chat model still works). See
# llm_client.get_vision_client for the resolution rules.
vision_client: Optional[LLMClient] = get_vision_client(
    session_logger=default_session_logger)
# VaultBot's own search engine: a keyless, rate-limit-resistant
# multi-engine aggregator (DuckDuckGo Lite + Marginalia + arXiv) PLUS an
# opt-in SearXNG (self-hosted Docker) backend for mainstream-web coverage.
# No API keys, no signup. Each engine throttles + bans independently, so one
# rate-limit never starves the research loop. SearXNG joins the pool only
# when its Docker container is available; if Docker is missing, FreeSearch
# runs with just the 3 keyless engines. See [[free_search]].
#
# SearXNG is created defensively: a missing/broken Docker must never crash
# the backend. If the container can't be managed, searxng_manager stays
# None and FreeSearch silently omits the SearXNG backend.
searxng_manager = None
try:
    from searxng_manager import SearxngManager
    searxng_manager = SearxngManager(session_logger=default_session_logger)
except Exception as _searxng_err:
    print(f"[startup] SearXNG backend disabled (Docker/SearxngManager unavailable: {_searxng_err})")

search_client = FreeSearch(
    session_logger=default_session_logger,
    searxng_manager=searxng_manager,
)
vault_indexer = VaultIndexer(vault_path=os.getenv("VAULT_PATH", "."), session_logger=default_session_logger)
vault_graph = VaultGraph(vault_path=os.getenv("VAULT_PATH", "."), session_logger=default_session_logger)
note_creator = NoteCreator(vault_path=os.getenv("VAULT_PATH", "."), indexer=vault_indexer, session_logger=default_session_logger)

# LLM-light deep research engine. Used by both the /research_tool endpoint
# (for MCP clients) and the autonomous researcher. No LLM calls inside.
research_engine = ResearchEngine(
    session_logger=default_session_logger,
    max_rounds=int(os.getenv("VAULTBOT_RESEARCH_ROUNDS", "4")),
    max_sources_per_round=int(os.getenv("VAULTBOT_RESEARCH_SOURCES", "5")),
    max_follow_ups=int(os.getenv("VAULTBOT_RESEARCH_FOLLOWUPS", "3")),
    search_client=search_client,
)

# --- The VaultBot spine: the vault is the mind, the model is plumbing ---
# Knowledge curriculum (Voyager-style self-directed growth): decides what the
# vault should learn next based on diversity + state + completed/failed.
# Instantiated BEFORE the autonomous researcher so the researcher can use it.
knowledge_curriculum = KnowledgeCurriculum(
    vault_graph=vault_graph, session_logger=default_session_logger)

# Checkpointer: persists the autonomous researcher's in-flight work so a
# crashed/restarted backend can resume mid-research instead of losing it.
# OpenHands event-sourcing pattern (arXiv:2511.03690). Instantiated BEFORE
# the autonomous researcher so the researcher can use it.
checkpointer = Checkpointer(
    checkpoint_dir=str(Path(__file__).with_name("checkpoints")),
    session_logger=default_session_logger)

# Procedure tracker: the deterministic feedback loop for procedural notes.
# Logs validation pass/fail per procedure, triggers re-research when
# failures exceed threshold, and tracks quality promotion. Instantiated
# BEFORE the autonomous researcher so the researcher can use it.
procedure_tracker = ProcedureTracker(
    log_path=str(Path(__file__).with_name("procedure_failure_log.json")),
    vault_path=os.getenv("VAULT_PATH", "."),
)

# Autonomous researcher: scans the vault for knowledge gaps and fills them
# in the background. Started on server startup; can be toggled via the API.
autonomous_researcher = AutonomousResearcher(
    vault_path=os.getenv("VAULT_PATH", "."),
    vault_graph=vault_graph,
    vault_indexer=vault_indexer,
    note_creator=note_creator,
    session_logger=default_session_logger,
    interval_seconds=int(os.getenv("VAULTBOT_AUTONOMOUS_INTERVAL", "600")),
    max_researches_per_cycle=int(os.getenv("VAULTBOT_AUTONOMOUS_MAX", "2")),
    min_dangling_references=int(os.getenv("VAULTBOT_AUTONOMOUS_MIN_REFS", "1")),
    thin_note_threshold=int(os.getenv("VAULTBOT_AUTONOMOUS_THIN", "200")),
    search_client=search_client,
    curriculum=knowledge_curriculum,
    checkpointer=checkpointer,
    procedure_tracker=procedure_tracker,
)

# Self-improvement engine: lets VaultBot read/write its own code, run code in
# a sandbox, and create new tools in custom_tools/ that are instantly callable
# by the chat LLM and external MCP clients.
self_improver = SelfImprover(session_logger=default_session_logger)

# Three-file identity layer (IDENTITY/SELF_MODEL/GOALS): makes the agent feel
# like the same agent across days regardless of which model is in the slot.
identity = Identity(
    identity_dir=str(Path(__file__).with_name("identity")),
    ollama_client=ollama_client, session_logger=default_session_logger)

# Curated graph-op vocabulary (small, fixed, idempotent, verifiable): the 7
# ops the plan executor calls. Also exposed to the LLM for direct tool-calling.
graph_op_registry = GraphOpRegistry(
    vault_graph=vault_graph, vault_indexer=vault_indexer,
    note_creator=note_creator, research_engine=research_engine,
    ollama_client=ollama_client, session_logger=default_session_logger)

# Model-robust plan executor: takes a JSON plan of atomic idempotent subtasks,
# each with a deterministic verifier, executes them against the graph ops,
# and closes the loop with a judge — not the worker model's self-report.
plan_executor = PlanExecutor(
    op_registry=graph_op_registry.ops,
    session_logger=default_session_logger)

# A-MEM note evolution (arXiv:2502.12110): when a new note is created, evolve
# its neighbors' tags/links so the vault "learns by refining."
amem = AMemeEvolution(
    vault_path=os.getenv("VAULT_PATH", "."),
    vault_graph=vault_graph, vault_indexer=vault_indexer,
    ollama_client=ollama_client, session_logger=default_session_logger)

# Fused retrieval (vector + wikilink graph + backlinks): replaces flat FAISS
# search in the chat loop so the vault reasons graph-awarely.  The embedding-
# drift layer (relevance feedback) is wired in so retrieval ranks by "what
# is this note good FOR" (accumulated feedback), not just "what is it similar
# to" — notes that proved helpful for similar queries drift toward them.
embedding_drift = EmbeddingDrift(
    state_path=Path(__file__).with_name("embedding_drift.json"),
    session_logger=default_session_logger)
fused_retriever = FusedRetriever(
    vault_graph=vault_graph, vault_indexer=vault_indexer,
    embedding_drift=embedding_drift,
    session_logger=default_session_logger)

# Context compactor (OpenHands Condenser pattern): summarizes conversation
# middle when history grows too long, preventing context overflow on long
# chats without losing the thread.
compactor = Compactor(
    ollama_client=ollama_client, session_logger=default_session_logger)

# Lazy note condenser: de-fluffs notes over time as they're queried. After
# each chat, retrieved notes get a touch; once a note has been queried 3+
# times and is still long, a background task rewrites it in place to a terse
# version (preserving every concept, formula, and wikilink, dropping
# scaffolding + repetition).  Notes that are never queried are never touched
# — zero wasted LLM calls.  This is the "de-fluff over time as pages are
# queried" behavior: the vault gradually becomes denser in exactly the
# places the user/agent actually look.
lazy_condenser = LazyCondenser(
    vault_path=os.getenv("VAULT_PATH", "."),
    ollama_client=ollama_client, session_logger=default_session_logger)

# Health monitor: heartbeat + liveness for the autonomous researcher + a
# /health endpoint so a watchdog can detect hangs.
health_monitor = HealthMonitor(session_logger=default_session_logger)

# Context budgeter: ensures retrieved vault context fits within the model's
# token budget. Pure deterministic -- truncates from the end (lowest-priority
# detail) if context would overflow. See [[Context-Budgeting-for-Vault-Growth]].
context_budgeter = ContextBudgeter()

# Calibration tracker: uses Sean's corrections as ground truth to calibrate
# automated quality gates (vault_lint, procedure_tracker, etc.).
# Pure deterministic -- heuristic correction detection + structured logging.
# See [[Calibration-via-Operator-Feedback]].
calibration_tracker = CalibrationTracker()

# RAG evaluator: logs retrieval results and computes recall@k, precision@k,
# NDCG@k, MRR when ground truth is available. Pure deterministic.
# See [[RAG-Evaluation-for-FUSED-Retrieval]].
rag_evaluator = RAGEvaluator()

# Claim verifier: post-generation verification layer. Extracts atomic claims
# from research notes, loads cited sources, checks entailment. Uses LLM when
# available, falls back to deterministic string matching.
# See [[Claim-Verification-for-Vault-Notes]].
claim_verifier = ClaimVerifier(llm_client=ollama_client)

# Pattern extractor: deterministic extraction of cross-session patterns from
# chat logs. Scans episodic memory, finds recurring topics, sentiment
# patterns, tool usage, and self-model drift. Feeds consolidation gaps to
# the autonomous researcher. Pure deterministic -- no LLM calls.
# See [[Semantic-Consolidation-Architecture]].
pattern_extractor = PatternExtractor(
    vault_path=os.getenv("VAULT_PATH", "."),
)

# Connection manager for WebSocket
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket, session_logger: SessionLogger = None):
        try:
            await websocket.send_text(message)
        except Exception as e:
            # Client likely disconnected; don't crash the server
            if session_logger is not None:
                session_logger.log("websocket_send_failed", {"error": str(e)})
            return
        if session_logger is not None:
            try:
                session_logger.log_message("out", json.loads(message))
            except json.JSONDecodeError:
                session_logger.log_message("out", {"raw": message})

    async def broadcast(self, message: str, session_logger: SessionLogger = None):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                if session_logger is not None:
                    session_logger.log("websocket_broadcast_failed", {"error": str(e)})
                continue
            if session_logger is not None:
                try:
                    session_logger.log_message("out", json.loads(message))
                except json.JSONDecodeError:
                    session_logger.log_message("out", {"raw": message})

manager = ConnectionManager()


# ─── Services registry ───────────────────────────────────────────────────
# Bundle every singleton into a Services instance so extracted modules
# (chat_handler.py, weaving.py, task_api.py, ...) can receive it as a
# parameter instead of reading these globals as free variables. See
# services.py. The globals above stay in place; only the extracted
# functions change to `svc.<name>` access.
from services import Services
svc = Services(
    ollama_client=ollama_client,
    vision_client=vision_client,
    vault_indexer=vault_indexer,
    vault_graph=vault_graph,
    note_creator=note_creator,
    research_engine=research_engine,
    search_client=search_client,
    autonomous_researcher=autonomous_researcher,
    knowledge_curriculum=knowledge_curriculum,
    checkpointer=checkpointer,
    procedure_tracker=procedure_tracker,
    self_improver=self_improver,
    identity=identity,
    graph_op_registry=graph_op_registry,
    plan_executor=plan_executor,
    amem=amem,
    fused_retriever=fused_retriever,
    embedding_drift=embedding_drift,
    compactor=compactor,
    lazy_condenser=lazy_condenser,
    context_budgeter=context_budgeter,
    health_monitor=health_monitor,
    calibration_tracker=calibration_tracker,
    rag_evaluator=rag_evaluator,
    claim_verifier=claim_verifier,
    pattern_extractor=pattern_extractor,
    session_logger=default_session_logger,
    manager=manager,
)

@app.on_event("startup")
async def startup_event():
    # Truncate oversized stdout/stderr logs so they can't grow unbounded
    # (was 256MB, mostly GET / heartbeat noise). Keep the last 1MB.
    for _log_name in ("backend_stdout.log", "backend_stderr.log"):
        _log_path = Path(__file__).parent / _log_name
        try:
            if _log_path.exists() and _log_path.stat().st_size > 10 * 1024 * 1024:
                _log_path.with_name(_log_name).write_bytes(b"")
        except Exception:
            pass

    startup_logger = SessionLogger()
    startup_logger.log("server_startup", {"stage": "begin"})
    try:
        loop = asyncio.get_event_loop()
        # Purge stale crash-recovery partials. The old in-vault location
        # (vaultbot_backend/partials/) caused Obsidian's file-recovery core
        # plugin to race the backend's delete and spam ENOENT errors, so
        # partials now live in the OS temp dir. Clean both locations on
        # startup: any leftover partial is from a previous crashed session
        # that already restarted, so it's stale and safe to remove.
        def _purge_partials():
            import tempfile
            for d in (
                Path(__file__).with_name("partials"),  # legacy in-vault dir
                Path(tempfile.gettempdir()) / "vaultbot_partials",  # current
            ):
                if not d.is_dir():
                    continue
                for p in d.glob("partial_*.md"):
                    try:
                        p.unlink()
                    except Exception:
                        pass
        await loop.run_in_executor(None, _purge_partials)
        # Load the persisted index and start watching for live edits immediately.
        # Heavy re-indexing is deferred so the server/API is available right away.
        await loop.run_in_executor(None, vault_indexer.load)
        await loop.run_in_executor(None, vault_indexer.start_watching)

        # Kick off background re-indexing of only new/changed notes after the server is up.
        async def background_index():
            try:
                await loop.run_in_executor(None, vault_indexer.index_missing_or_changed)
            except Exception as e:
                startup_logger.log_exception(e, context="background_index")
        asyncio.create_task(background_index())

        startup_logger.log("server_startup", {"stage": "end", "status": "ok",
                                   "vectors": vault_indexer.index.ntotal if vault_indexer.index else 0})
        # Start the autonomous researcher so it begins filling vault gaps
        # in the background. It waits a short grace period before its first
        # cycle so the index/graph are settled.
        try:
            autonomous_researcher.start()
            # Wire the health monitor's heartbeat into the researcher so the
            # /health endpoint reflects live autonomous activity.
            autonomous_researcher._heartbeat = health_monitor.heartbeat
            health_monitor.start_watchdog(check_interval=300)
            # Recover any interrupted research from a previous crash.
            # The checkpointer stores in-flight work; on startup we check for
            # status="running" checkpoints and re-queue them so the
            # researcher actually retries them instead of just logging.
            try:
                recovery = checkpointer.recover(autonomous_researcher)
                if recovery.get("recovered"):
                    startup_logger.log("checkpointer_recovered", {
                        "interrupted_count": len(recovery["recovered"]),
                        "topics": [c.topic for c in recovery["recovered"]],
                    })
                    # Re-queue the interrupted gaps so the next cycle
                    # researches them FIRST, before the curriculum's
                    # normal proposals. This is the actual retry.
                    recovered_gaps = []
                    for ckpt in recovery["recovered"]:
                        recovered_gaps.append(ckpt.gap if ckpt.gap else {
                            "kind": ckpt.kind,
                            "topic": ckpt.topic,
                            "priority": 9999,  # top priority
                            "normalized_name": ckpt.topic.lower(),
                            "referenced_by": [],
                        })
                    if recovered_gaps:
                        autonomous_researcher._recovered_gaps = recovered_gaps
                        startup_logger.log("checkpointer_requeued", {
                            "count": len(recovered_gaps),
                            "topics": [g["topic"] for g in recovered_gaps],
                        })
            except Exception as e:
                startup_logger.log("checkpointer_recovery_failed", {"error": str(e)})
            startup_logger.log("autonomous_researcher_started", {})
        except Exception as e:
            startup_logger.log_exception(e, context="autonomous_researcher_start")
    except Exception as e:
        startup_logger.log_exception(e, context="server_startup")
    finally:
        startup_logger.close()

@app.on_event("shutdown")
async def shutdown_event():
    shutdown_logger = SessionLogger()
    shutdown_logger.log("server_shutdown", {"stage": "begin"})
    try:
        # Stop the autonomous researcher first so it doesn't fire mid-shutdown.
        try:
            autonomous_researcher.stop()
            shutdown_logger.log("autonomous_researcher_stopped", {})
        except Exception as e:
            shutdown_logger.log_exception(e, context="autonomous_researcher_stop")
        # Stop watching the vault for changes and persist the index
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, vault_indexer.stop_watching)
        await loop.run_in_executor(None, vault_indexer.persist)
        shutdown_logger.log("server_shutdown", {"stage": "end", "status": "ok"})
    except Exception as e:
        shutdown_logger.log_exception(e, context="server_shutdown")
    finally:
        shutdown_logger.close()

@app.get("/")
async def get():
    return HTMLResponse("<h1>VaultBot Backend is running</h>")
@app.get("/models")
async def list_models():
    """Return available models from the active LLM backend.

    Backend-agnostic: works for local Ollama (list_local_models) AND any
    OpenAI-compatible API (the client's list_models() hits /v1/models). The
    GUI dropdown is populated from this, so whatever API key the user brings,
    they get a live list of models to choose from.
    """
    loop = asyncio.get_event_loop()
    list_fn = getattr(ollama_client, "list_models", None) or ollama_client.list_local_models
    models = await loop.run_in_executor(None, list_fn)
    return {"models": models, "current": ollama_client.llm_model}

@app.post("/set_model")
async def set_model_endpoint(payload: dict):
    """Set the active LLM model immediately.

    For API backends the user may type a model id that isn't in the cached
    list (e.g. a newly-released model), so we accept any non-empty model and
    let the backend reject it at chat time if it's invalid — rather than
    hard-failing here on a stale list.
    """
    requested_model = payload.get("model")
    if not requested_model:
        return {"status": "error", "detail": "missing model"}, 400
    ollama_client.set_model(requested_model)
    return {"status": "ok", "model": requested_model, "current": ollama_client.llm_model}

# --- Config endpoint: let the Obsidian plugin read the research backend ---
# status at runtime. FreeSearch is keyless, so the config surface is
# informational only (which engines are up / cooling down).

@app.get("/config")
async def get_config():
    """Return the current research-backend configuration + engine health."""
    engines = []
    for b in getattr(search_client, "_backends", []):
        engines.append({
            "name": b.name,
            "in_cooldown": b._in_cooldown(),
            "cooldown_remaining_s": int(b._cooldown_remaining()),
        })
    return {
        "research_backend": "freesearch",
        "search_configured": search_client.is_configured,
        "engines": engines,
    }

@app.post("/config")
async def set_config(payload: dict):
    """Update research-backend settings at runtime.

    FreeSearch is keyless, so tavily_api_key / research_backend are accepted
    for plugin backwards-compat but are no-ops. We always report freesearch.
    """
    return {
        "status": "ok",
        "research_backend": "freesearch",
        "search_configured": search_client.is_configured,
    }

def _persist_env_value(key: str, value: str) -> None:
    """Write/update a KEY=VALUE line in the vault root .env file."""
    env_path = Path(__file__).with_name("..") / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    except Exception:
        lines = []
    found = False
    out = []
    for line in lines:
        if line.startswith(key + "="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    try:
        env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"VaultBot: could not persist {key} to .env: {e}")

# --- LLM backend config ----------------------------------------------------
# Lets the GUI show which synthesis backend is active (Ollama local vs an
# OpenAI-compatible API key) and switch between them WITHOUT editing .env by
# hand. A weak-laptop user picks "openai", pastes a key + base URL + model,
# and from then on the synthesis step hits the API; the research loop stays
# token-free either way. Switching the backend reconstructs the client at
# runtime so it takes effect immediately (no restart).

def _detect_llm_backend() -> str:
    """Read the effective backend name for /llm/config reporting."""
    backend = (os.getenv("LLM_BACKEND") or "").strip().lower()
    if backend == "openai":
        return "openai"
    return "ollama"

def _rebuild_llm_client() -> None:
    """Rebuild the global synthesis client from the current .env values.

    Used after /llm/config changes a backend/key/model so the new settings
    take effect without a restart. Preserves the session_logger binding.
    Also rebuilds the optional vision client so a runtime vision-model
    change takes effect immediately too.
    """
    global ollama_client, vision_client
    new_client = get_llm_client(session_logger=default_session_logger)
    # Carry over the currently-selected model if the new backend supports it
    # (an Ollama-only model id is meaningless to an OpenAI backend, so only
    # carry when the user explicitly set a model in the payload).
    new_client.set_model(new_client.llm_model or "")
    ollama_client = new_client
    vision_client = get_vision_client(session_logger=default_session_logger)

@app.get("/llm/config")
async def get_llm_config():
    """Return the current synthesis-LLM backend config (no secrets)."""
    backend = _detect_llm_backend()
    return {
        "backend": backend,
        "base_url": os.getenv("LLM_BASE_URL", "") if backend == "openai"
                    else os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        "model": ollama_client.llm_model,
        "has_api_key": bool(os.getenv("LLM_API_KEY", "")) if backend == "openai" else True,
        "running": ollama_client.is_running(),
    }

@app.post("/llm/config")
async def set_llm_config(payload: dict):
    """Switch the synthesis LLM backend at runtime and persist to .env.

    Accepted fields (all optional):
      backend: "ollama" | "openai"
      base_url: OpenAI-compatible endpoint (for openai)
      api_key:  bearer token (for openai)  -- written to .env, not echoed back
      model:    model id to use
    Rebuilds the client immediately. Returns the new (secret-free) config.
    """
    backend = (payload.get("backend") or "").strip().lower()
    base_url = (payload.get("base_url") or "").strip()
    api_key = (payload.get("api_key") or "").strip()
    model = (payload.get("model") or "").strip()

    if backend and backend not in ("ollama", "openai"):
        return {"status": "error", "detail": "backend must be 'ollama' or 'openai'"}, 400
    if backend == "openai":
        if not api_key and not os.getenv("LLM_API_KEY", ""):
            return {"status": "error", "detail": "api_key required for openai backend"}, 400
        if not model and not os.getenv("LLM_MODEL", ""):
            return {"status": "error", "detail": "model required for openai backend"}, 400

    # Persist the changes to .env so they survive a restart.
    if backend:
        _persist_env_value("LLM_BACKEND", backend)
    if base_url:
        _persist_env_value("LLM_BASE_URL", base_url)
    if api_key:
        _persist_env_value("LLM_API_KEY", api_key)
    if model:
        # Write to the right key for the chosen backend.
        if (backend or _detect_llm_backend()) == "openai":
            _persist_env_value("LLM_MODEL", model)
        else:
            _persist_env_value("OLLAMA_LLM_MODEL", model)

    # Reload .env into the process env so the factory sees the new values.
    load_dotenv(dotenv_path, override=True)
    _rebuild_llm_client()
    return {
        "status": "ok",
        "backend": _detect_llm_backend(),
        "base_url": os.getenv("LLM_BASE_URL", "") if _detect_llm_backend() == "openai"
                    else os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        "model": ollama_client.llm_model,
        "running": ollama_client.is_running(),
    }


@app.get("/llm/vision_check")
async def vision_check():
    """Probe whether the page-reading model can see images.

    Human-centered design: the GUI calls this when the user hits Ingest (or
    on first chat) so it can alert them RIGHT THEN — in the chat, in plain
    language — if their page-reading model can't read textbook pages and
    they need to pick a vision model. Returns {vision_capable, model,
    backend, source}. The probe renders a tiny red test image and asks the
    model what color it is, so a True means the model ACTUALLY saw the
    image, not just accepted it.

    The probed model is the DEDICATED vision client if one is configured
    (VISION_MODEL set); otherwise it falls back to the synthesis client's
    own vision_capable() — so a vision-capable chat model still works
    without a separate vision config. `source` tells the UI which one was
    used ("vision" vs "synthesis") so the alert can name the right model.
    """
    loop = asyncio.get_event_loop()
    probe_client = vision_client if vision_client is not None else ollama_client
    source = "vision" if vision_client is not None else "synthesis"
    capable = await loop.run_in_executor(None, probe_client.vision_capable)
    return {
        "vision_capable": bool(capable),
        "model": probe_client.llm_model,
        "backend": _detect_llm_backend() if vision_client is None
                   else (os.getenv("VISION_BACKEND") or _detect_llm_backend()),
        "source": source,
    }


@app.get("/llm/vision_config")
async def get_vision_config():
    """Return the dedicated vision-model config (no secrets).

    Lets the settings panel show whether a separate vision model is
    configured (for reading textbook pages) and which backend/model it
    uses, so a user with a text-only chat model can confirm their vision
    model is wired up. `configured` is False when no VISION_MODEL is set
    (meaning the page reader falls back to the synthesis client).
    """
    backend = (os.getenv("VISION_BACKEND") or "").strip().lower()
    if not backend:
        backend = _detect_llm_backend()
    model = (os.getenv("VISION_MODEL") or "").strip()
    configured = bool(model)
    base_url = ""
    if backend == "openai":
        base_url = (os.getenv("VISION_BASE_URL") or os.getenv("LLM_BASE_URL")
                    or "").strip()
    else:
        base_url = (os.getenv("VISION_OLLAMA_HOST")
                     or os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    running = False
    if vision_client is not None:
        try:
            running = bool(vision_client.is_running())
        except Exception:
            running = False
    return {
        "configured": configured,
        "backend": backend,
        "base_url": base_url,
        "model": model,
        "has_api_key": bool(os.getenv("VISION_API_KEY")
                             or os.getenv("LLM_API_KEY", "")) if backend == "openai"
                       else True,
        "running": running,
    }


@app.post("/llm/vision_config")
async def set_vision_config(payload: dict):
    """Configure (or clear) the dedicated vision model at runtime.

    Accepted fields (all optional; sending an empty `model` clears the
    vision config so the page reader falls back to the synthesis client):
      backend:      "ollama" | "openai"  (defaults to the synthesis backend)
      base_url:     OpenAI-compatible endpoint (openai path)
      api_key:      bearer token (openai path) — written to .env, not echoed
      model:        model id (openai) OR Ollama model name
      ollama_host:  Ollama host if the vision model lives on a different
                    daemon than the chat model (ollama path)

    Persists to .env and rebuilds the vision client immediately (no
    restart). Returns the new (secret-free) config.
    """
    backend = (payload.get("backend") or "").strip().lower()
    base_url = (payload.get("base_url") or "").strip()
    api_key = (payload.get("api_key") or "").strip()
    model = (payload.get("model") or "").strip()
    ollama_host = (payload.get("ollama_host") or "").strip()

    if backend and backend not in ("ollama", "openai"):
        return {"status": "error",
                "detail": "backend must be 'ollama' or 'openai'"}, 400
    if backend == "openai" and model:
        if not api_key and not os.getenv("VISION_API_KEY", "") \
                and not os.getenv("LLM_API_KEY", ""):
            return {"status": "error",
                    "detail": "api_key required for openai vision backend"}, 400

    # Persist the changes to .env so they survive a restart.
    if backend:
        _persist_env_value("VISION_BACKEND", backend)
    if base_url:
        _persist_env_value("VISION_BASE_URL", base_url)
    if api_key:
        _persist_env_value("VISION_API_KEY", api_key)
    if ollama_host:
        _persist_env_value("VISION_OLLAMA_HOST", ollama_host)
    # An empty model string clears the vision config (fall back to synthesis).
    _persist_env_value("VISION_MODEL", model)

    # Reload .env into the process env so the factory sees the new values.
    load_dotenv(dotenv_path, override=True)
    _rebuild_llm_client()
    return {
        "status": "ok",
        "configured": bool(model),
        "backend": (os.getenv("VISION_BACKEND") or _detect_llm_backend()),
        "base_url": (os.getenv("VISION_BASE_URL")
                     or os.getenv("LLM_BASE_URL", "")) if backend == "openai"
                    else (os.getenv("VISION_OLLAMA_HOST")
                          or os.getenv("OLLAMA_HOST", "http://localhost:11434")),
        "model": model,
        "running": bool(vision_client.is_running()) if vision_client is not None
                   else False,
    }


# --- Research + autonomous endpoints -------------------------------------

@app.post("/research_tool")
async def research_tool_endpoint(payload: dict):
    """Deep-research a topic via the LLM-light engine. Used by the MCP server
    and by any client that wants a sourced dig without invoking the LLM.

    Returns the structured research report plus the path of the note written.
    """
    topic = (payload.get("topic") or "").strip()
    depth = payload.get("depth", "deep")
    if not topic:
        return {"error": "missing topic"}, 400
    loop = asyncio.get_event_loop()
    if depth == "quick":
        # Quick mode: one round, no gap fill.
        research_engine.max_rounds = 1
        research_engine.max_follow_ups = 0
    try:
        report = await loop.run_in_executor(None, research_engine.research, topic)
    finally:
        # Restore defaults so the autonomous researcher isn't affected.
        research_engine.max_rounds = int(os.getenv("VAULTBOT_RESEARCH_ROUNDS", "4"))
        research_engine.max_follow_ups = int(os.getenv("VAULTBOT_RESEARCH_FOLLOWUPS", "3"))

    # Persist a linked research note so the dig becomes vault knowledge.
    note_path = None
    if report.get("source_count") and report.get("synthesis"):
        try:
            summary = (f"Deep research into '{topic}' ({report['source_count']} "
                       f"sources, {report['synthesis_facts']} facts).")
            note_path = await loop.run_in_executor(
                None, note_creator.create_note_from_research,
                topic, report["synthesis"], summary)
            # Overwrite with the richer markdown so sources are preserved.
            md = research_engine.synthesize_note_markdown(report, summary)
            try:
                from pathlib import Path as _P
                _P(note_path).write_text(md, encoding="utf-8")
            except Exception:
                pass
        except Exception as e:
            default_session_logger.log_exception(e, context="research_tool_note")
    report["note_path"] = note_path
    
    # Run claim verification on the newly written note.
    # Extracts atomic claims, checks entailment against cited sources,
    # updates frontmatter with verification stats. Graceful degradation
    # if LLM unavailable (falls back to deterministic string matching).
    if note_path:
        try:
            verification = await loop.run_in_executor(
                None, claim_verifier.verify_note, note_path)
            report["verification"] = verification
            if verification.get("unsupported", 0) + verification.get("contradicted", 0) > 0:
                default_session_logger.log(
                    "claim_verification",
                    f"Note {note_path}: {verification['verified']}/{verification['total_claims']} verified, "
                    f"{verification['unsupported']} unsupported, {verification['contradicted']} contradicted"
                )
        except Exception as e:
            default_session_logger.log_exception(e, context="claim_verification")
    
    return report


# --- Learning-material ingestion (index-only paradigm) --------------------
# A non-tech user presses the Ingest button; this endpoint scans
# learningMaterial/ for PDFs that haven't been indexed yet and builds an
# index TOC for each (heading -> page pointer). No content is copied, no
# OCR runs, no monolith notes clutter the graph. The PDF stays the source
# of truth. The LLM later reads pages on demand via textbook_read_page.
# This is fast (seconds per book) and precise (the model sees the rendered
# page, equations and all) — the paradigm shift from pre-extracting text.
@app.post("/ingest_learning_material")
async def ingest_learning_material_endpoint(payload: dict = None):
    """Index any new PDFs from learningMaterial/ as pointer-only TOCs.

    Returns a summary of what was indexed. Idempotent: a PDF already indexed
    (its source-key is in an existing index TOC) is skipped.
    """
    from textbook_index import index_learning_material
    payload = payload or {}
    loop = asyncio.get_event_loop()
    vault_root = Path(os.getenv("VAULT_PATH", "."))
    learning_dir = vault_root / "learningMaterial"
    result = await loop.run_in_executor(
        None, lambda: index_learning_material(str(learning_dir)))
    return result


# --- Voice: local STT + TTS endpoints ------------------------------------
# Lets the Obsidian plugin add a "Call" button: record audio in the browser,
# POST the bytes to /stt → get text back → send it through the chat →
# POST the assistant reply to /tts → play the returned WAV. No cloud keys,
# no per-call cost. Vosk (offline) for STT, pyttsx3/SAPI for TTS.

@app.post("/stt")
async def stt_endpoint(request: Request):
    """Transcribe a raw audio upload (webm/wav/ogg from MediaRecorder).

    Body = raw audio bytes. Content-Type is honored to pick the decoder.
    Returns {text: "..."}. On any failure returns {text: "", error: "..."}
    so the caller can degrade gracefully.
    """
    mime = request.headers.get("content-type", "audio/webm")
    body = await request.body()
    if not body:
        return {"text": "", "error": "empty body"}
    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(None, stt_transcribe, body, mime)
    return {"text": text}


@app.post("/tts")
async def tts_endpoint(request: Request):
    """Synthesize text to a WAV. Body = JSON {text, voice?, rate?}.

    Returns audio/wav bytes (or 204 if text is empty). Used as a server-side
    fallback when the browser's speechSynthesis isn't available.
    """
    try:
        payload = await request.json()
    except Exception:
        return {"error": "invalid json"}, 400
    text = (payload.get("text") or "").strip()
    if not text:
        return b"", 204
    voice = payload.get("voice")
    rate = int(payload.get("rate", 190))
    loop = asyncio.get_event_loop()
    wav = await loop.run_in_executor(None, tts_synthesize, text, voice, rate)
    if not wav:
        return {"error": "synthesis failed"}, 500
    # FastAPI Response with explicit media type so the browser plays it.
    from fastapi import Response
    return Response(content=wav, media_type="audio/wav")


@app.get("/voices")
async def voices_endpoint():
    """List available local TTS voices (SAPI on Windows)."""
    loop = asyncio.get_event_loop()
    voices = await loop.run_in_executor(None, tts_voices)
    return {"voices": voices}


@app.get("/autonomous/status")
async def autonomous_status():
    """Report autonomous researcher state and recent history."""
    return autonomous_researcher.status()


@app.get("/autonomous/gaps")
async def autonomous_gaps():
    """List the vault's current knowledge gaps via the knowledge curriculum.

    Uses the Voyager-style diversity-aware curriculum (not the simple
    reference-count ranking) so the gaps reflect what the vault should
    learn next for maximum coverage at achievable cost.
    """
    try:
        loop = asyncio.get_event_loop()
        gaps = await loop.run_in_executor(None, knowledge_curriculum.propose_next_gaps, 20)
        return {"gaps": gaps, "count": len(gaps),
                "curriculum_state": knowledge_curriculum.state_summary()}
    except Exception as e:
        default_session_logger.log_exception(e, context="autonomous_gaps")
        return {"error": str(e)}, 500


@app.post("/autonomous/trigger")
async def autonomous_trigger():
    """Run one autonomous research cycle immediately."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, autonomous_researcher.trigger_now)
    return result


@app.get("/consolidation/gaps")
async def consolidation_gaps():
    """Return patterns ripe for semantic consolidation.

    The pattern extractor scans chat logs for recurring topics, correction
    patterns, tool usage, and self-model drift. These gaps can be
    consolidated into semantic knowledge notes so future sessions start
    smarter. See [[Semantic-Consolidation-Architecture]].
    """
    try:
        loop = asyncio.get_event_loop()
        gaps = await loop.run_in_executor(
            None, pattern_extractor.get_consolidation_gaps)
        return {"gaps": gaps, "count": len(gaps),
                "report": pattern_extractor.consolidation_report()}
    except Exception as e:
        default_session_logger.log_exception(e, context="consolidation_gaps")
        return {"error": str(e)}, 500


@app.post("/consolidation/extract")
async def consolidation_extract():
    """Run pattern extraction and log the results without writing a note.

    This is the scan-only step of the consolidation pipeline. It extracts
    patterns deterministically (no LLM) and logs them. The LLM can then
    synthesize semantic notes from the pre-extracted findings.
    """
    try:
        loop = asyncio.get_event_loop()
        patterns = await loop.run_in_executor(
            None, pattern_extractor.extract_all)
        pattern_extractor.log_consolidation(patterns)
        return {
            "sessions_scanned": patterns["total_sessions"],
            "exchanges_scanned": patterns["total_exchanges"],
            "recurring_topics": len(patterns["recurring_topics"]),
            "sentiment": patterns["sentiment"]["distribution"],
            "negative_rate": patterns["sentiment"]["negative_rate"],
            "tool_frequency": dict(
                list(patterns["tool_patterns"]["tool_frequency"].items())[:10]),
            "over_reporting": patterns["over_reporting"]["count"],
            "self_model_drift": patterns["self_model_drift"],
        }
    except Exception as e:
        default_session_logger.log_exception(e, context="consolidation_extract")
        return {"error": str(e)}, 500


@app.post("/autonomous/toggle")
async def autonomous_toggle(payload: dict = None):
    """Enable or disable the autonomous researcher."""
    if payload is None:
        payload = {}
    enable = payload.get("enabled", not autonomous_researcher.enabled)
    autonomous_researcher.enabled = bool(enable)
    if enable and not (autonomous_researcher._thread and autonomous_researcher._thread.is_alive()):
        autonomous_researcher.start()
    elif not enable:
        autonomous_researcher.stop()
    return autonomous_researcher.status()


# --- Custom tool endpoints (for the MCP server + external clients) ------

@app.get("/custom_tools")
async def list_custom_tools():
    """Return schemas for all agent-authored custom tools."""
    return {"tools": self_improver.custom_tool_schemas()}


@app.post("/custom_tools/call")
async def call_custom_tool(payload: dict):
    """Execute an agent-authored custom tool by name."""
    name = payload.get("name", "")
    args = payload.get("args", {})
    if not self_improver.has_tool(name):
        return {"error": f"custom tool not found: {name}"}, 404
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: self_improver.execute_custom_tool(name, args))
    return result


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    session_logger = SessionLogger()
    client_host = websocket.client.host if websocket.client else "unknown"
    session_logger.log("websocket_connect", {"client_host": client_host})
    await manager.connect(websocket)
    # Per-connection conversation history. This is THE fix for the "amnesia"
    # bug: without it, every message started a fresh 2-message conversation
    # (system + this message) with zero memory of prior turns. The model
    # literally couldn't see your previous message or its previous answer.
    # History persists across messages in the same session (one Obsidian
    # chat tab = one session) and resets cleanly when you reload the tab.
    # The compactor trims it when it grows too long so context never overflows.
    websocket.conversation_history = []
    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except json.JSONDecodeError as e:
                session_logger.log_exception(e, context="websocket_receive_json")
                await manager.send_personal_message(json.dumps({"type": "error", "content": "Invalid JSON"}), websocket)
                continue

            session_logger.log_message("in", payload)

            msg_type = payload.get("type", "chat")
            user_message = payload.get("message", "")

            # "stop" lets the UI interrupt a running chat/research without
            # sending a new message. Cancels the current task if any.
            if msg_type == "stop":
                task = getattr(websocket, "_current_task", None)
                if task and not task.done():
                    # Mark the task as stopped-by-user so its CancelledError
                    # handler doesn't send a duplicate 'stopped' event.
                    setattr(task, "_stopped_by_user", True)
                    task.cancel()
                await manager.send_personal_message(
                    json.dumps({"type": "stopped", "content": "Interrupted"}), websocket)
                continue

            # "/new" (typed as a chat message) starts a FRESH session: clears
            # the per-connection conversation history AND rolls a new session
            # log, so the next message begins with no memory of prior turns
            # and diagnostics go to a new JSONL file. No plugin change needed —
            # the user just types /new and hits enter like any message.
            if msg_type == "chat" and user_message.strip().lower() == "/new":
                # Cancel any in-flight task first so it doesn't write into the
                # freshly-reset history.
                task = getattr(websocket, "_current_task", None)
                if task and not task.done():
                    setattr(task, "_stopped_by_user", True)
                    task.cancel()
                websocket.conversation_history = []
                # Roll a new session log so diagnostics for the new conversation
                # land in their own file (the old log is preserved on disk).
                old_session_id = session_logger.session_id
                session_logger = SessionLogger()
                session_logger.log("session_reset", {
                    "trigger": "/new", "previous_session_id": old_session_id})
                session_logger.log("websocket_connect", {"client_host": client_host})
                await manager.send_personal_message(json.dumps({
                    "type": "session_reset", "content": "New session started. I've cleared our conversation history — what would you like to work on?"
                }), websocket, session_logger=session_logger)
                continue

            if not user_message:
                session_logger.log("empty_message", {"payload": payload})
                continue

            # Optional per-message model override
            requested_model = payload.get("model")
            if requested_model and requested_model != ollama_client.llm_model:
                ollama_client.set_model(requested_model)
                session_logger.log("model_override", {"model": requested_model})

            # Interrupt-on-send: if a previous chat/research is still running,
            # cancel it so the new message takes over immediately (no queueing).
            task = getattr(websocket, "_current_task", None)
            if task and not task.done():
                task.cancel()
                session_logger.log("chat_interrupted", {"reason": "new_message"})

            # Spawn the handler as a fire-and-forget task so the receive loop
            # stays responsive to stop/new messages. The task's own exceptions
            # are logged via its done callback (not awaited here, since
            # awaiting would block the read loop).
            def _spawn_handler():
                async def _run():
                    try:
                        if msg_type == "research":
                            await handle_research(websocket, user_message, session_logger)
                        else:
                            await handle_chat(websocket, user_message, session_logger)
                    except asyncio.CancelledError:
                        session_logger.log("chat_cancelled", {"reason": "interrupted"})
                        # The stop/new-message handler already sent the
                        # 'stopped' event (it sets _stopped_by_user). Only
                        # emit here if the cancel came from elsewhere.
                        if not getattr(asyncio.current_task(), "_stopped_by_user", False):
                            await manager.send_personal_message(
                                json.dumps({"type": "stopped", "content": "Interrupted"}), websocket)
                    except Exception as e:
                        session_logger.log_exception(e, context=f"handle_{msg_type}")
                        await manager.send_personal_message(
                            json.dumps({"type": "error", "content": f"Server error: {e}"}), websocket)
                    finally:
                        # Chat-priority: always release the researcher pause so
                        # background research resumes after the turn ends —
                        # whether it completed, errored, or was interrupted.
                        try:
                            autonomous_researcher.resume_after_chat()
                        except Exception:
                            pass
                return asyncio.create_task(_run())

            websocket._current_task = _spawn_handler()
    except WebSocketDisconnect:
        session_logger.log("websocket_disconnect", {"reason": "client_disconnected"})
    except Exception as e:
        session_logger.log_exception(e, context="websocket_endpoint")
    finally:
        manager.disconnect(websocket)
        session_logger.close()


# --- Live progress plumbing: banish the black box -----------------------
# During long synchronous steps (research, scrape, synthesis, A-MEM) the
# backend used to go silent for 30-60+ seconds. These helpers push periodic
# progress/heartbeat events over the websocket so the UI always shows what
# stage the model is in and that it's still alive.

async def _send_progress(websocket: WebSocket, stage: str,
                          detail: Optional[Dict[str, Any]] = None) -> None:
    """Send a structured progress event to the live UI."""
    try:
        await manager.send_personal_message(
            json.dumps({"type": "progress", "stage": stage,
                         "detail": detail or {}}),
            websocket, session_logger=default_session_logger)
    except Exception:
        pass


async def _heartbeat(websocket: WebSocket, label: str,
                      start_time: float, interval: float = 2.0) -> None:
    """Push a one-shot heartbeat so the UI can render elapsed time + a
    'still alive' pulse. Called periodically by long-running executors."""
    try:
        elapsed = asyncio.get_event_loop().time() - start_time
        await manager.send_personal_message(
            json.dumps({"type": "heartbeat", "label": label,
                         "elapsed_ms": int(elapsed * 1000)}),
            websocket, session_logger=default_session_logger)
    except Exception:
        pass


async def _run_with_heartbeat(websocket: WebSocket, label: str,
                               coro_or_fn, *args, **kwargs) -> Any:
    """Run a blocking call in an executor while emitting heartbeats so the
    user is never staring at a frozen 'Calling X...' line.

    `coro_or_fn` is a plain callable (run in the default executor). Heartbeats
    fire every `interval` seconds with the elapsed time, and a final
    progress event fires when the call returns."""
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    interval = kwargs.pop("interval", 2.0)
    task = loop.run_in_executor(None, lambda: coro_or_fn(*args, **kwargs))
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=interval)
        except asyncio.TimeoutError:
            await _heartbeat(websocket, label, t0, interval)
        except Exception:
            # Re-raise the real exception from the task.
            return task.result()
    result = task.result()
    await _send_progress(websocket, label + "_done", {
        "duration_ms": int((loop.time() - t0) * 1000)})
    return result


async def handle_chat(websocket: WebSocket, user_message: str, session_logger: SessionLogger):
    """Agentic chat: the LLM reasons over the vault, calls tools (research,
    search, gaps, status) when it hits a gap, and produces a grounded answer.

    This is the Jarvis loop — the LLM self-directs instead of shrugging.
    """
    session_logger.log("chat_begin", {"user_message": user_message})

    # Chat-priority: pause the autonomous researcher so it doesn't compete
    # with this interactive turn for the Ollama GPU. On a single-GPU laptop
    # the user's embedding + LLM calls would otherwise queue behind the
    # researcher's background synthesis, making the chat appear to hang.
    # Resumed in the finally block below so it always clears (even on
    # cancel/error). The researcher skips its cycle while this is set.
    autonomous_researcher.pause_for_chat()

    # Calibration: detect if this message is a correction of the previous
    # answer. Sean's corrections are ground truth for calibrating automated
    # quality gates. See [[Calibration-via-Operator-Feedback]].
    try:
        _prev_history = getattr(websocket, "conversation_history", None)
        _prev_answer = None
        if _prev_history:
            for _msg in reversed(_prev_history):
                if _msg.get("role") == "assistant" and _msg.get("content"):
                    _prev_answer = _msg["content"]
                    break
        if _prev_answer and calibration_tracker.detect_correction(user_message, _prev_answer):
            _ftype = calibration_tracker.classify_failure(user_message, _prev_answer)
            calibration_tracker.log_correction(
                user_message, _prev_answer, failure_type=_ftype)
            session_logger.log("correction_detected", {"failure_type": _ftype})
    except Exception as e:
        session_logger.log("correction_detection_failed", {"error": str(e)})
    await manager.send_personal_message(json.dumps({"type": "status", "content": "Searching vault..."}), websocket, session_logger=session_logger)
    loop = asyncio.get_event_loop()

    # Keep the in-memory vault graph current with disk before retrieval.
    # The intended design was an incremental diff (cost proportional to
    # changed files); VaultGraph.refresh() is now mtime-gated and only re-reads
    # files that changed since the last refresh, so the common no-edit case is
    # a cheap stat-only scan and notes created/edited earlier in the chat still
    # surface. This keeps the vault graph current with disk before retrieval.
    try:
        _t_graph = loop.time()
        await loop.run_in_executor(None, vault_graph.refresh)
        session_logger.log("graph_refreshed", {
            "node_count": len(vault_graph.nodes),
            "duration_ms": (loop.time() - _t_graph) * 1000,
        })
    except Exception as e:
        session_logger.log_exception(e, context="graph_refresh")

    t0 = loop.time()
    try:
        # Heartbeat-wrapped: the fused retriever embeds the query via Ollama,
        # which can stall when the autonomous researcher is also using Ollama.
        # Without a heartbeat the GUI freezes on "Searching vault..." with no
        # feedback. This pushes a "still alive / elapsed" pulse every 2s so
        # Sean always sees the backend is working, not hung.
        fused_result = await _run_with_heartbeat(
            websocket, "retrieving vault",
            fused_retriever.retrieve, user_message, 5, 1)
        results = fused_result.get("results", []) if isinstance(fused_result, dict) else (fused_result or [])
    except Exception as e:
        session_logger.log_exception(e, context="fused_retriever.retrieve")
        # Degrade gracefully to flat vector search.
        try:
            results = await _run_with_heartbeat(
                websocket, "retrieving vault (fallback)",
                vault_indexer.search, user_message, 5)
        except Exception:
            results = []
    session_logger.log("vault_search", {
        "query": user_message,
        "k": 5,
        "result_count": len(results),
        "duration_ms": (loop.time() - t0) * 1000,
        "retriever": "fused",
    })

    # RAG evaluation: log retrieval results for every query (cheap, always on).
    # Metrics are computed on-demand when ground truth is available.
    # See [[RAG-Evaluation-for-FUSED-Retrieval]].
    try:
        rag_evaluator.log_retrieval(user_message, results, k=5)
    except Exception as e:
        session_logger.log("rag_eval_log_failed", {"error": str(e)})

    # Lazy-condenser touch tracking: record that each retrieved note was
    # queried.  Notes that cross the touch threshold (3+) AND are still long
    # get de-fluffed in the background after the answer is delivered — this
    # is the "de-fluff over time as pages are queried" behavior.  Never
    # raises; a failure here must not break the chat.
    retrieved_paths = []
    try:
        for r in results:
            fp = r.get("file_path") if isinstance(r, dict) else None
            if fp:
                retrieved_paths.append(fp)
                lazy_condenser.note_touched(fp)
        # Persist the batched touch counts once per chat turn, not once per
        # retrieved note (each note_touched() only marks the dict dirty).
        lazy_condenser.flush_touch_counts()
    except Exception as e:
        session_logger.log("lazy_condenser_touch_failed", {"error": str(e)})

    # Procedure context tracking: which procedural notes were in the vault
    # context for this turn? Used to log validation results against them.
    procedures_in_context = parse_procedures_from_results(results)
    if procedures_in_context:
        session_logger.log("procedures_in_context", {
            "procedures": procedures_in_context,
        })

    # Multi-resolution context: L2 MOC (bird's-eye) + L1 concept cards
    # (the thought highway — terse, hop-able) + L0 drill-down (full raw of
    # the single top seed only).  Replaces the old `build_graph_context`
    # content dump, which truncated every note to its first 2000 chars and
    # flooded the context with low-density detail.  Falls back to the legacy
    # builder if no L1 cards exist yet (pre-hierarchy vault regions).
    try:
        abs_ctx = await _run_with_heartbeat(
            websocket, "building context",
            build_abstract_context, vault_graph, results,
            user_message, 5, 2, None)
        context = abs_ctx.get("context", "")
        session_logger.log("context_resolution", {
            "resolution": abs_ctx.get("resolution"),
            "l1_cards": abs_ctx.get("l1_cards", 0),
            "drill_down_used": abs_ctx.get("drill_down_used", False),
            "l0_drill": abs_ctx.get("l0_drill"),
            "context_length": len(context)})
    except Exception as e:
        session_logger.log_exception(e, context="build_abstract_context")
        context = build_graph_context(vault_graph, results, user_message, k=5, depth=2)

    # Context budgeting: ensure the retrieved context fits within the
    # model's token budget. Truncates from the end (lowest-priority L0
    # drill-down detail) if the context would overflow the context window.
    # Pure deterministic -- no LLM calls. Graceful degradation: if the
    # budgeter fails, the original context is used unchanged.
    try:
        _budgeted = context_budgeter.budget(
            context, getattr(websocket, "conversation_history", []))
        context = _budgeted["context"]
        if _budgeted["truncated"]:
            session_logger.log("context_budget", {
                "original_tokens": _budgeted["original_tokens"],
                "budgeted_tokens": _budgeted["budgeted_tokens"],
                "budget": _budgeted["budget"],
                "chars_dropped": _budgeted["chars_dropped"],
            })
    except Exception as e:
        session_logger.log("context_budget_failed", {"error": str(e)})

    # Inject the identity boot context so the agent wakes up coherent across
    # days regardless of which model is in the slot (IDENTITY + SELF_MODEL +
    # GOALS, delivered verbatim before the first turn — MIRROR/Letta pattern).
    identity_context = identity.boot_context()

    # Gather live state so the system prompt is a real briefing, not static.
    autonomous_state = autonomous_researcher.status()
    try:
        _t_gaps = loop.time()
        gaps = await _run_with_heartbeat(
            websocket, "finding gaps",
            knowledge_curriculum.propose_next_gaps, 10)
        session_logger.log("gaps_proposed", {
            "gap_count": len(gaps),
            "duration_ms": (loop.time() - _t_gaps) * 1000,
        })
    except Exception:
        gaps = []
    gaps_summary = "\n".join(
        f"- [{g.get('kind')}] {g.get('topic')} (priority {g.get('priority', 0)})"
        for g in gaps[:10]) or "(none detected)"

    # Build the combined tool list: built-in vault tools + meta-tools (self-
    # improvement) + any agent-authored custom tools currently loaded.
    custom_schemas = self_improver.custom_tool_schemas()
    custom_tool_names = [s["function"]["name"] for s in custom_schemas]
    all_tools = TOOL_DEFINITIONS + META_TOOL_DEFINITIONS + custom_schemas
    custom_tools_desc = "\n".join(
        f"- {s['function']['name']}: {s['function']['description'][:100]}"
        for s in custom_schemas) if custom_schemas else "(none yet)"

    system_prompt = (identity_context + "\n\n" +
                      build_system_prompt(context, autonomous_state, gaps_summary,
                                         custom_tools=custom_tools_desc,
                                         custom_tool_names=custom_tool_names))
    session_logger.log("prompt_built", {
        "system_prompt_length": len(system_prompt),
        "context_length": len(context),
        "gaps_reported": len(gaps),
        "custom_tools": len(custom_schemas),
        "total_tools": len(all_tools),
    })

    # Build the conversation for /api/chat using PERSISTENT per-session history.
    # This is the amnesia fix: prior turns (user + assistant + tool exchanges)
    # carry over within the same websocket session, so corrections and
    # context survive. History lives on websocket.conversation_history.
    # On the first turn it's empty; we rebuild the system prompt fresh each
    # turn (it carries live vault state) and prepend it.
    conversation = [{"role": "system", "content": system_prompt}]
    # Append the prior turns (no system prompt — that's rebuilt above each
    # turn so the agent always sees current vault state + gaps).
    conversation.extend(getattr(websocket, "conversation_history", []))
    # Add this turn's user message.
    conversation.append({"role": "user", "content": user_message})

    # Compact if the conversation is getting long (OpenHands Condenser pattern).
    # Prevents context overflow on long chats; keeps head + tail verbatim,
    # summarizes the middle. Now this actually has something to compact.
    if compactor.should_compact(conversation):
        conversation = compactor.compact(conversation)
        session_logger.log("context_compacted", {"messages": len(conversation)})

    await manager.send_personal_message(json.dumps({"type": "status", "content": "Thinking..."}), websocket, session_logger=session_logger)

    # --- Agentic loop: reason → tool call → execute → feed back → repeat ---
    # No cap on rounds/tool calls: the agent loops until it produces a final
    # answer (a turn with no tool calls) or the loop crashes.
    final_answer = ""
    thinking_text = ""
    total_chunks = 0
    t0 = loop.time()

    # Partial-answer crash protection: write the streamed-so-far answer to a
    # temp file so a crash mid-stream doesn't lose it. On normal completion
    # the file is deleted; on crash, it survives and the next session can
    # surface it ("You were answering 'X' when I crashed — here's what I had:").
    #
    # The partial dir lives OUTSIDE the vault (in the OS temp dir) so that
    # Obsidian's file-recovery core plugin — which snapshots every .md file
    # inside the vault — doesn't race the backend's delete and spam the
    # console with ENOENT errors. The old in-vault location
    # (vaultbot_backend/partials/) is cleaned up at startup.
    import time as _time, hashlib, tempfile
    partial_dir = Path(tempfile.gettempdir()) / "vaultbot_partials"
    partial_dir.mkdir(parents=True, exist_ok=True)
    partial_id = hashlib.md5((user_message + str(_time.time())).encode()).hexdigest()[:12]
    partial_path = partial_dir / f"partial_{partial_id}.md"
    _write_partial(partial_path, user_message, "", "")  # create the file immediately

    try:
     round_idx = 0
     while True:
        # Stream the LLM response for this round.
        round_text = ""
        round_thinking = ""
        round_tool_calls = []
        chunk_count = 0
        try:
            def sync_stream():
                for chunk in ollama_client.chat(conversation, tools=all_tools, stream=True):
                    yield chunk
            gen = sync_stream()
            round_t0 = loop.time()
            last_chunk_at = loop.time()
            while True:
                # Fetch the next chunk with a timeout so we can emit a
                # heartbeat while the model is silent (e.g. still loading the
                # model into memory, or in a long thinking pause before the
                # first token). This is what kills the black-box feeling:
                # even with zero output, the user sees "still thinking, 8s".
                next_chunk_task = loop.run_in_executor(None, lambda: next(gen, {"done": True}))
                chunk = None
                while chunk is None:
                    try:
                        chunk = await asyncio.wait_for(
                            asyncio.shield(next_chunk_task), timeout=3.0)
                    except asyncio.TimeoutError:
                        elapsed = int((loop.time() - round_t0) * 1000)
                        since = int((loop.time() - last_chunk_at) * 1000)
                        await manager.send_personal_message(json.dumps({
                            "type": "heartbeat", "label": f"thinking (round {round_idx+1})",
                            "elapsed_ms": elapsed, "silent_ms": since,
                            "chunks": chunk_count,
                        }), websocket, session_logger=session_logger)
                        # Loop back: shield kept the task alive, retry the wait.
                    except asyncio.CancelledError:
                        # Interrupt (stop button / new message): close the
                        # Ollama generator so the backend thread stops pulling
                        # tokens, then re-raise so the outer handler exits.
                        gen.close()
                        raise
                if chunk.get("done") and not chunk.get("response") and not chunk.get("tool_calls"):
                    break
                chunk_count += 1
                total_chunks += 1
                last_chunk_at = loop.time()
                thinking = chunk.get("thinking", "")
                text = chunk.get("response", "")
                tcs = chunk.get("tool_calls", [])
                if thinking:
                    round_thinking += thinking
                    thinking_text += thinking
                    await manager.send_personal_message(json.dumps({"type": "thinking", "content": thinking}), websocket, session_logger=session_logger)
                if text:
                    round_text += text
                    await manager.send_personal_message(json.dumps({"type": "answer_chunk", "content": text}), websocket, session_logger=session_logger)
                    # Update the partial-answer file so a crash mid-stream
                    # preserves whatever was streamed so far.
                    _write_partial(partial_path, user_message, final_answer + round_text, thinking_text)
                if tcs:
                    round_tool_calls.extend(tcs)
        except Exception as e:
            session_logger.log_exception(e, context="ollama_client.chat")
            await manager.send_personal_message(json.dumps({"type": "error", "content": f"LLM error: {e}"}), websocket, session_logger=session_logger)
            return

        session_logger.log("agent_round", {
            "round": round_idx,
            "chunk_count": chunk_count,
            "text_length": len(round_text),
            "tool_calls": len(round_tool_calls),
        })

        # Append the assistant's turn to the conversation so the next round
        # sees the full history (including thinking for Qwen-style models).
        assistant_msg = {"role": "assistant", "content": round_text}
        if round_thinking:
            assistant_msg["thinking"] = round_thinking
        if round_tool_calls:
            assistant_msg["tool_calls"] = round_tool_calls
        conversation.append(assistant_msg)

        # No tool calls → the LLM produced a final answer. We're done.
        if not round_tool_calls:
            final_answer = round_text
            break

        # Accumulate non-final round text into final_answer so the partial
        # file captures all streamed text across rounds, not just the last.
        final_answer += round_text

        # Execute each tool call and feed results back as tool-role messages.
        for tc in round_tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            tool_args_raw = fn.get("arguments", "{}")
            try:
                tool_args = json.loads(tool_args_raw) if isinstance(tool_args_raw, str) else tool_args_raw
            except json.JSONDecodeError:
                tool_args = {}
            tool_call_id = tc.get("id", tool_name)

            await manager.send_personal_message(json.dumps({
                "type": "tool_call", "tool": tool_name, "args": tool_args
            }), websocket, session_logger=session_logger)
            session_logger.log("tool_call_requested", {
                "tool": tool_name, "args": tool_args, "round": round_idx,
            })

            t_tool0 = loop.time()
            try:
                tool_result = await _execute_agent_tool(
                    tool_name, tool_args, session_logger, websocket)
            except Exception as e:
                session_logger.log_exception(e, context=f"tool_{tool_name}")
                tool_result = {"error": str(e)}
            # If the agent just created a tool, refresh the tool list so the
            # new tool is callable in the very next round.
            if tool_name == "tool_create":
                custom_schemas = self_improver.custom_tool_schemas()
                all_tools = TOOL_DEFINITIONS + META_TOOL_DEFINITIONS + custom_schemas
            tool_duration = (loop.time() - t_tool0) * 1000
            session_logger.log("tool_call_result", {
                "tool": tool_name, "duration_ms": tool_duration,
                "result_keys": list(tool_result.keys()) if isinstance(tool_result, dict) else None,
            })

            # Procedure tracking: log validation results against procedures
            # that were in context for this turn. This is the deterministic
            # feedback loop -- no LLM judgment, just structured logging.
            if tool_name in ("vault_lint", "safe_write", "code_run"):
                try:
                    v_result, v_category, v_details = interpret_validation_result(
                        tool_name, tool_result)
                    proc_name = procedures_in_context[0] if procedures_in_context else "no_procedure"
                    procedure_tracker.log_result(
                        procedure=proc_name,
                        task=tool_name,
                        validation_result=v_result,
                        validation_tool=tool_name,
                        error_details=v_details,
                        category=v_category,
                    )
                except Exception as e:
                    session_logger.log("procedure_tracking_failed", {"error": str(e)})
            await manager.send_personal_message(json.dumps({
                "type": "tool_result", "tool": tool_name,
                "summary": _tool_result_summary(tool_name, tool_result),
            }), websocket, session_logger=session_logger)

            conversation.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(tool_result, default=str),
            })

        # Loop back: the LLM now sees the tool results and will produce
        # either another tool call or the final answer.
        round_idx += 1

    except Exception as e:
        # The whole agentic loop crashed — save whatever was streamed so far.
        session_logger.log_exception(e, context="handle_chat_agentic_loop")
        _write_partial(partial_path, user_message, final_answer, thinking_text)
        session_logger.log("partial_answer_saved_on_crash", {
            "partial_path": str(partial_path),
            "answer_chars": len(final_answer),
        })
        raise
    finally:
        # If the answer completed normally, clean up the partial file.
        # If it didn't (crash, disconnect), the partial survives on disk.
        if final_answer and len(final_answer) > 50:
            try:
                if partial_path.exists():
                    partial_path.unlink()
            except Exception:
                pass

    session_logger.log("llm_generate", {
        "model": ollama_client.llm_model,
        "stream": True,
        "total_chunks": total_chunks,
        "answer_length": len(final_answer),
        "thinking_length": len(thinking_text),
        "tool_rounds": round_idx + 1,
        "duration_ms": (loop.time() - t0) * 1000,
    })

    await manager.send_personal_message(json.dumps({"type": "answer_done", "content": final_answer}), websocket, session_logger=session_logger)
    session_logger.log("chat_end", {
        "answer_length": len(final_answer),
        "thinking_length": len(thinking_text),
        "tool_rounds": round_idx + 1,
    })

    # Embedding-drift feedback (relevance feedback, LLM-free): nudge the
    # stored embeddings of retrieved notes toward (or away from) this query
    # based on whether the context was useful.  Signal heuristic:
    #   - If the agent produced a substantive answer (len > 50) on the
    #     FIRST round WITHOUT calling vault_research, the vault context was
    #     helpful → nudge the top retrieved note's embedding TOWARD the
    #     query (it ranks higher for similar queries next time).
    #   - If the agent's first move was to call vault_research (the vault
    #     was insufficient), the retrieved context was UNhelpful for this
    #     query → nudge the top retrieved note AWAY from the query.
    #   - A short answer (< 50 chars) is ambiguous → no signal.
    # This is the "scooch embeddings toward/away based on if the LLM says
    # it's helpful" behavior.  Zero extra LLM calls — the signal is derived
    # from the agent's own behavior.  Drift is capped + reset on rewrite
    # (see embedding_drift.py).
    if retrieved_paths:
        try:
            # did the agent research on round 0? (vault context unhelpful)
            researched_first = False
            # round_idx 0 + a research tool call in the first round.
            # We approximate: if final_answer is short AND round_idx > 0,
            # the agent looped through tools (likely research).  A cleaner
            # signal would track whether vault_research was called, but
            # this is LLM-free and good enough for drift seeding.
            first_round_researched = (round_idx > 0 and len(final_answer) < 200)
            q_emb = await loop.run_in_executor(
                None, vault_indexer._get_embedding, user_message)
            top_fp = retrieved_paths[0]
            if first_round_researched:
                embedding_drift.record_feedback(top_fp, q_emb, helpful=False)
            elif len(final_answer) > 50:
                embedding_drift.record_feedback(top_fp, q_emb, helpful=True)
            session_logger.log("drift_feedback", {
                "top_note": Path(top_fp).stem,
                "helpful": (len(final_answer) > 50 and not first_round_researched),
                "answer_len": len(final_answer),
                "rounds": round_idx + 1})
        except Exception as e:
            session_logger.log("drift_feedback_failed", {"error": str(e)})

    # Lazy de-fluff: after the answer is delivered, condense any retrieved
    # notes that have crossed the touch threshold (3+ queries) and are still
    # long.  Fire-and-forget so the user is never blocked — the condense LLM
    # call happens in the background.  Notes that are never queried are never
    # touched (zero wasted LLM calls).  A note that condenses gets its touch
    # counter reset so it isn't immediately re-condensed.
    if retrieved_paths:
        async def _run_lazy_condense_bg():
            try:
                summary = await loop.run_in_executor(
                    None, lazy_condenser.condense_batch, retrieved_paths)
                if not summary.get("condensed"):
                    return
                session_logger.log("lazy_condense_done", summary)
                # Re-index condensed notes AND get the new embeddings back so
                # we can re-weave.  batch_add_files skips unchanged notes by
                # hash, so only the condensed ones cost an embedding call.
                # Detect which notes actually got condensed by checking for
                # the marker (the summary's details list uses stem names,
                # not full paths, so it's unreliable for path lookups).
                from lazy_condenser import CONDENSE_MARKER
                condensed_paths = []
                for fp in retrieved_paths:
                    try:
                        if CONDENSE_MARKER in Path(fp).read_text(
                                encoding="utf-8", errors="replace"):
                            condensed_paths.append(fp)
                    except Exception:
                        continue
                if not condensed_paths:
                    return
                _n, new_embs = await loop.run_in_executor(
                    None, vault_indexer.batch_add_files,
                    condensed_paths, True)
                # --- Post-condense re-weave --- #
                # The condense LLM is told to keep all [[wikilinks]], but if
                # it drops a scaffolding sentence that carried a link, the
                # link goes with it.  Re-run the outbound linker on each
                # condensed note to restore any links whose concept is still
                # mentioned as plain text in the new body.  Idempotent (won't
                # double-wrap).  Also re-run the cross-book linker with the
                # NEW embeddings so the "## Related sections" block reflects
                # the condensed content, not the original.
                title_map = _existing_note_titles()
                for fp in condensed_paths:
                    try:
                        await loop.run_in_executor(
                            None, _link_outbound, fp, title_map)
                    except Exception:
                        pass
                # Re-run cross-book linking on the condensed notes only.
                # source_keys = the condensed set (so a condensed note doesn't
                # cross-link to another condensed note from the same batch
                # incorrectly — though same-book exclusion is less precise
                # here since we don't have the full book path set; the
                # distance threshold still prevents weak matches).
                source_keys = {str(Path(fp).resolve()) for fp in condensed_paths}
                try:
                    cross = await loop.run_in_executor(
                        None, _cross_link_textbooks,
                        condensed_paths, new_embs, source_keys)
                    session_logger.log("post_condense_relink", {
                        "condensed": len(condensed_paths),
                        "cross_links": cross.get("cross_links_added", 0),
                    })
                except Exception as e:
                    session_logger.log("post_condense_crosslink_failed",
                                       {"error": str(e)})
                # --- L1 concept-card lazy refine (rehearsal-gated) --- #
                # Cards retrieved 3+ times get a one-shot LLM rewrite into a
                # tight semantic summary (same rehearsal contract as the L0
                # condenser).  When an L0 section is condensed, also refresh
                # its card so the card reflects the new terse content.  Zero
                # LLM calls for cards that haven't earned it.
                try:
                    from concept_card import (card_path_for, needs_refine,
                                              refine_card, build_card_for)
                    # First: refresh cards for any condensed L0 sections so
                    # the card sketch reflects the new body (unless the card
                    # was already LLM-refined, which is sticky).  Also RESET
                    # the embedding drift for any note whose content changed
                    # (condense or refine) — the old drift was earned against
                    # content that no longer exists, so keeping it would
                    # mislead retrieval.
                    for fp in condensed_paths:
                        card = card_path_for(fp)
                        if card.exists():
                            # Re-build the extractive sketch only if not refined.
                            try:
                                old = card.read_text(
                                    encoding="utf-8", errors="replace")
                                from concept_card import REFINED_MARKER
                                if REFINED_MARKER not in old:
                                    build_card_for(fp, vault_graph=vault_graph)
                            except Exception:
                                pass
                        # Drift reset: content changed, old drift is invalid.
                        try:
                            embedding_drift.reset(fp)
                            if card.exists():
                                embedding_drift.reset(str(card))
                        except Exception:
                            pass
                    # Then: LLM-refine any retrieved cards that have crossed
                    # the rehearsal threshold.  Uses the touch counter the
                    # lazy_condenser already maintains.
                    refined = 0
                    for fp in retrieved_paths:
                        card = card_path_for(fp)
                        if not card.exists():
                            continue
                        try:
                            tc = lazy_condenser.touch_counts.get(
                                str(Path(card).resolve()), 0)
                        except Exception:
                            tc = 0
                        if needs_refine(card, tc):
                            r = await loop.run_in_executor(
                                None, refine_card, card, ollama_client, None)
                            if r.get("refined"):
                                refined += 1
                                # re-index the refined card
                                await loop.run_in_executor(
                                    None, vault_indexer.batch_add_files,
                                    [str(card)], False)
                                # Drift reset: the card's content changed
                                # (extractive → LLM summary), so the old
                                # drift is invalid.
                                try:
                                    embedding_drift.reset(str(card))
                                except Exception:
                                    pass
                    if refined:
                        session_logger.log("card_refine_done",
                                           {"refined": refined})
                except Exception as e:
                    session_logger.log("card_refine_failed",
                                       {"error": str(e)})
            except Exception as e:
                session_logger.log("lazy_condense_bg_failed", {"error": str(e)})
        asyncio.create_task(_run_lazy_condense_bg())

    # Persist this turn into the per-session history so the NEXT message has
    # context. We save the system-prompt-stripped conversation (the system
    # prompt is rebuilt fresh each turn) — i.e. everything after the initial
    # system message: the user turn, all assistant + tool rounds, and the
    # final assistant answer. This is what gets prepended next turn.
    try:
        history = getattr(websocket, "conversation_history", None)
        if history is not None and final_answer:
            # The conversation list is [system, ...history, user, assistant,
            # tool, assistant, ...]. Strip the leading system message and
            # everything we just added (user msg onward) is the new history.
            new_turns = [m for m in conversation if m.get("role") != "system"]
            websocket.conversation_history = new_turns
            session_logger.log("history_persisted", {
                "turns": len(new_turns),
                "history_chars": sum(len(str(m.get("content", ""))) for m in new_turns),
            })
    except Exception as e:
        session_logger.log("history_persist_failed", {"error": str(e)})

    # Save a chat note if the answer is substantive
    if len(final_answer) > 100:
        try:
            note_path = await loop.run_in_executor(None, note_creator.create_note_from_chat, user_message, final_answer, thinking_text)
            session_logger.log("chat_note_created", {"note_path": note_path})
        except Exception as e:
            session_logger.log_exception(e, context="note_creator.create_note_from_chat")
            print(f"Error creating chat note: {e}")

    # Keep GOALS.md current: if the user's message looks like a task/request
    # (not a casual greeting), update the active goal so the agent remembers
    # what it's working on across restarts. This is the Generative Agents
    # plan-persistence pattern — the goal lives in a file, not in context.
    try:
        if len(user_message) > 15 and not user_message.lower().startswith(("hi", "hey", "sup", "hello", "yo")):
            # Simple heuristic: the user's message IS the current goal.
            # The self-model already captures what happened; GOALS captures
            # what's active. If the answer completed the request, the goal
            # clears next turn; if it's ongoing (e.g. multi-step), it persists.
            identity.update_goals(
                goal=user_message[:500],
                next_step="(in progress)" if len(final_answer) < 200 else "(completed this turn)"
            )
            session_logger.log("goals_updated", {"goal": user_message[:100]})
    except Exception as e:
        session_logger.log("goals_update_failed", {"error": str(e)})

    # Close the MIRROR loop: regenerate the bounded self-model from this
    # turn's activity so the agent consolidates its reasoning into a durable
    # first-person narrative that survives context compaction and model swaps.
    # This is the +9.3% vs +2.4% finding (MIRROR arXiv:2506.00430): the value
    # of thinking lies in maintaining its outputs across time, not the act of
    # thinking itself.
    try:
        activity = f"User asked: {user_message[:300]}\nAnswer: {final_answer[:500]}"
        await loop.run_in_executor(None, lambda: identity.regenerate_self_model(activity))
    except Exception as e:
        session_logger.log("self_model_regenerate_failed", {"error": str(e)})

    # Pattern extraction: check for new consolidation gaps after each chat.
    # This is the episodic -> semantic consolidation trigger. The pattern
    # extractor scans chat logs for recurring topics, correction patterns,
    # and self-model drift. Gaps are logged so the autonomous researcher
    # can consolidate them into semantic knowledge notes.
    # Pure deterministic -- no LLM calls. See [[Semantic-Consolidation-Architecture]].
    try:
        _gaps = await loop.run_in_executor(
            None, pattern_extractor.get_consolidation_gaps)
        if _gaps:
            session_logger.log("consolidation_gaps", {
                "gap_count": len(_gaps),
                "top_gaps": [
                    {"kind": g["kind"], "topic": g["topic"],
                     "priority": g.get("priority", 0)}
                    for g in _gaps[:5]
                ],
            })
    except Exception as e:
        session_logger.log("pattern_extraction_failed", {"error": str(e)})


async def _execute_agent_tool(tool_name: str, args: Dict[str, Any],
                              session_logger: SessionLogger,
                              websocket: Optional[WebSocket] = None) -> Dict[str, Any]:
    """Execute one tool call from the chat LLM. Runs in the async context.

    `websocket` is passed so long-running tools (vault_research) can push
    live progress events to the UI instead of going silent for 30-60s.
    """
    loop = asyncio.get_event_loop()

    if tool_name == "vault_research":
        topic = (args.get("topic") or "").strip()
        depth = args.get("depth", "deep")
        if not topic:
            return {"error": "missing topic"}
        if depth == "quick":
            research_engine.max_rounds = 1
            research_engine.max_follow_ups = 0

        # Live progress: the research engine calls back from a worker thread
        # at each stage. We marshal those into websocket sends on the loop so
        # the UI shows "round 2/4, 12 sources…" instead of a black box.
        prev_cb = research_engine.progress_callback
        if websocket is not None:
            def _progress_cb(stage: str, detail: dict):
                try:
                    asyncio.run_coroutine_threadsafe(
                        _send_progress(websocket, stage, detail), loop)
                except Exception:
                    pass
            research_engine.progress_callback = _progress_cb

        t_research = loop.time()
        try:
            report = await _run_with_heartbeat(
                websocket, f"research:{topic[:40]}", research_engine.research, topic)
        finally:
            research_engine.max_rounds = int(os.getenv("VAULTBOT_RESEARCH_ROUNDS", "4"))
            research_engine.max_follow_ups = int(os.getenv("VAULTBOT_RESEARCH_FOLLOWUPS", "3"))
            research_engine.progress_callback = prev_cb
            session_logger.log("agent_research_done", {
                "duration_ms": (loop.time() - t_research) * 1000,
                "source_count": report.get("source_count", 0) if isinstance(report, dict) else 0,
            })
        # Persist a linked note so the research becomes vault knowledge.
        if report.get("source_count") and report.get("synthesis"):
            try:
                summary = (f"Research into '{topic}' ({report['source_count']} "
                           f"sources, {report['synthesis_facts']} facts).")
                await _send_progress(websocket, "writing_note", {"topic": topic})
                note_path = await _run_with_heartbeat(
                    websocket, "writing_note",
                    note_creator.create_note_from_research,
                    topic, report["synthesis"], summary)
                md = research_engine.synthesize_note_markdown(report, summary)
                try:
                    Path(note_path).write_text(md, encoding="utf-8")
                except Exception:
                    pass
                report["note_path"] = note_path
            except Exception as e:
                session_logger.log_exception(e, context="agent_research_note")
        # A-MEM: evolve neighboring notes' tags/links so the vault learns from
        # the new note (arXiv:2502.12110).
        if report.get("note_path"):
            try:
                await _send_progress(websocket, "amem_evolve", {
                    "note": Path(report["note_path"]).stem})
                await _run_with_heartbeat(
                    websocket, "amem_evolve",
                    lambda: amem.evolve_on_create(
                        report.get("note_path", ""), report.get("synthesis", ""),
                        skip_refresh=True))
            except Exception as e:
                session_logger.log("amem_evolve_failed", {"error": str(e)})
        return report

    if tool_name == "vault_search":
        query = args.get("query", "")
        k = int(args.get("k", 5))
        results = await loop.run_in_executor(None, vault_indexer.search, query, k)
        return {"query": query, "results": [
            {"file_path": r.get("file_path"), "content": r.get("content", "")[:1200],
             "score": r.get("score")} for r in results
        ]}

    if tool_name == "vault_gaps":
        gaps = await loop.run_in_executor(None, autonomous_researcher._identify_gaps)
        return {"gaps": gaps[:20], "count": len(gaps)}

    if tool_name == "vaultbot_status":
        return autonomous_researcher.status()

    # --- Meta-tools (self-improvement) ---
    if tool_name == "code_read":
        return await loop.run_in_executor(None, lambda: self_improver.code_read(
            args.get("file_path", ""), int(args.get("start_line", 1)),
            int(args.get("end_line", 0))))

    if tool_name == "code_run":
        return await loop.run_in_executor(None, lambda: self_improver.code_run(
            args.get("code", ""), int(args.get("timeout", 15))))

    if tool_name == "tool_create":
        result = await loop.run_in_executor(None, lambda: self_improver.tool_create(
            args.get("tool_name", ""), args.get("description", ""),
            args.get("parameters", {}), args.get("code", "")))
        # Hot-reload so the new tool is callable immediately.
        self_improver.load_custom_tools()
        return result

    if tool_name == "self_reflect":
        ctx = args.get("vault_context", "")
        return await loop.run_in_executor(None, lambda: self_improver.self_reflect(
            args.get("topic", ""), ctx))

    if tool_name == "git_rollback":
        return await loop.run_in_executor(None, lambda: self_improver.git_rollback(
            args.get("file_path", "")))

    if tool_name == "safe_write":
        return await loop.run_in_executor(None, lambda: self_improver.safe_write(
            args.get("file_path", ""), args.get("content", ""),
            bool(args.get("dry_run", False))))

    if tool_name == "capability_audit":
        return await loop.run_in_executor(None, lambda: self_improver.capability_audit(
            args.get("task", "")))

    # --- Textbook page reader (index-only paradigm) ---
    # The LLM calls this to read one page of an ingested textbook PDF. The
    # page is rendered to an image and sent to a vision-capable model so
    # equations/figures come through exactly as printed. Falls back to the
    # text layer (with a caveat) if the model can't see images. The result
    # carries provenance so the LLM can cite it in notes.
    #
    # Client selection: prefer the DEDICATED vision client (a separate
    # model the user configured just for page-reading, e.g. a vision model
    # on a different backend while their chat model stays text-only/fast).
    # Fall back to the synthesis client so a vision-capable chat model still
    # works without a separate vision config.
    if tool_name == "textbook_read_page":
        from custom_tools.textbook_read_page import run as _read_page
        page_client = vision_client if vision_client is not None else ollama_client
        # Inject the active page-reading client so the tool can probe vision
        # support and call it for the page read.
        result = await loop.run_in_executor(
            None, lambda: _read_page(args, llm_client=page_client))
        return result

    # --- Web source re-reader (index-only paradigm for web research) ---
    # The LLM calls this to re-read a source the research engine archived in
    # learningMaterial/web/. Returns the page's article text + provenance to
    # the saved file, so the LLM can verify/quote without re-scraping.
    if tool_name == "web_read_source":
        from custom_tools.web_read_source import run as _read_web
        result = await loop.run_in_executor(None, lambda: _read_web(args))
        return result

    # --- Custom (agent-authored) tools ---
    if self_improver.has_tool(tool_name):
        result = await loop.run_in_executor(None, lambda: self_improver.execute_custom_tool(
            tool_name, args))
        # Post-ingest weaving: tie newly-ingested textbook notes into the
        # existing vault so the content is actually usable (not inert islands).
        # Runs IN THE BACKGROUND so the tool returns immediately — the agent
        # (and the user) aren't blocked for minutes while 100+ notes get
        # indexed + linked + A-MEM evolved. Progress is pushed to the UI via
        # websocket so the user sees "linking 47/129…" instead of a freeze.
        # Only fires for textbook_ingest; cheap no-op otherwise.
        if tool_name == "textbook_ingest" and isinstance(result, dict):
            note_count = len(result.get("notes_created", []) +
                             result.get("notes_updated", []))
            if note_count > 0:
                result["weaving"] = {
                    "status": "background",
                    "notes_to_weave": note_count,
                    "message": (f"Weaving {note_count} notes into the vault "
                                f"in the background (indexing + linking + "
                                f"evolving neighbors)..."),
                }
                # Fire-and-forget: run the weaving in a background thread so
                # the agent gets the result now and can keep working/talking.
                # Progress events are sent to the websocket from the thread.
                async def _run_weave_bg():
                    try:
                        await _weave_textbook_notes(
                            result, websocket=websocket,
                            session_logger=session_logger)
                    except Exception as e:
                        session_logger.log("textbook_weave_bg_failed",
                                           {"error": str(e)})
                asyncio.create_task(_run_weave_bg())
        return result

    return {"error": f"unknown tool: {tool_name}"}


# ---------------------------------------------------------------------------
# Post-ingest weaving: tie ingested textbook notes into the existing vault.
# ---------------------------------------------------------------------------
# Two passes, run after the ingester writes the notes but before returning to
# the LLM, so the content is linked (not just inert text):
#   1. Index + outbound-link each new/updated section note: scan its body for
#      plain-text mentions of EXISTING note titles and convert them to
#      [[wikilinks]]. This is the "new -> old" direction A-MEM doesn't do — it
#      lets a textbook section on "Newton's Second Law" link INTO a research
#      note that already exists with that title.
#   2. A-MEM evolution on each section: evolve the NEIGHBORS (existing notes
#      semantically similar to the new section) so they get backlinks +
#      enriched tags. This is the "old -> new" direction — existing notes now
#      point back at the new textbook section.
# Both directions are idempotent (re-running over an already-linked note is a
# no-op) and failure-isolated (a weave error never breaks the ingest).
def _existing_note_titles() -> dict:
    """Return {normalized_title: file_path} for every note in the vault.

    Used to detect plain-text mentions worth wikilinking. Normalized = the
    Obsidian wikilink form (lowercased). Excludes the ingester's own textbook
    notes so we don't link textbook-to-textbook (that's the ingester's job).

    Sourced from the in-memory vault graph (``vault_graph.nodes``) instead of
    a full ``rglob`` over the vault — the graph already holds every note's
    stem + file_path in memory, so this is a dict walk instead of a disk
    scan. The graph's ignore-dir filter (venv/.obsidian/.git/etc.) already
    applies; the textbooks-folder exclusion is applied here.
    """
    titles: Dict[str, str] = {}
    try:
        for _name, node in (vault_graph.nodes or {}).items():
            fp = node.get("file_path") or ""
            if not fp:
                continue
            # Skip the textbooks/ folder — those are what we're weaving.
            if ("vaultbot" + os.sep + "textbooks" + os.sep
                    not in fp + os.sep):
                pass  # not a textbook note — keep it
            else:
                continue
            stem = Path(fp).stem
            if len(stem) < 3:
                continue  # too short to link safely (e.g. "a", "is")
            titles[stem.lower()] = fp
    except Exception:
        pass
    return titles


def _is_ignored_index_path(p: Path) -> bool:
    """True for vault subpaths the indexer/graph ignore (venv, index, etc.)."""
    parts = str(p).replace("\\", "/").lower()
    ignored = ("vaultbot_venv/", "vaultbot_backend/vaultbot_index/",
               "vaultbot_backend/partials/", ".git/")
    return any(seg in parts for seg in ignored)


def _link_outbound(note_path: str, title_map: dict) -> int:
    """Convert plain-text mentions of existing note titles in a note into
    [[wikilinks]]. Returns the number of links inserted.

    Safe rules (won't corrupt the note):
      - Only links titles >= 4 chars (avoids linking "the", "a", "is").
      - Only links titles that appear as whole words, case-insensitively.
      - Never wraps a mention that's already inside [[...]] or is a URL.
      - Skips the title line (H1) so the note's own heading isn't self-linked.
      - Atomic write; never raises (returns 0 on any failure).
    """
    try:
        p = Path(note_path)
        text = p.read_text(encoding="utf-8", errors="replace")
        if not text:
            return 0
        lines = text.split("\n")
        # Skip the first H1 line (the note's own heading).
        start = 1 if lines and lines[0].lstrip().startswith("# ") else 0
        links_added = 0
        for stem_lower, _fp in title_map.items():
            if len(stem_lower) < 4:
                continue
            # Match the title as a whole word, case-insensitive, but NOT when
            # it's already inside a wikilink. The lookbehind/lookahead block
            # matches right after `[[` or right before `]]`, so an already-
            # linked mention is skipped. A bare mention mid-sentence matches.
            pattern = re.compile(
                r"(?<!\[\[)\b" + re.escape(stem_lower) + r"\b(?!\]\])",
                re.IGNORECASE)
            for i in range(start, len(lines)):
                # Don't link a mention that sits inside a URL.
                if "http" in lines[i] and stem_lower in lines[i].lower():
                    # Could still link a non-URL word on the same line; the
                    # subn count=1 picks the first match, so only skip if the
                    # first match is inside the URL. Simplest safe rule: skip
                    # the line entirely if the title appears inside an http link.
                    url_match = re.search(r"https?://\S*", lines[i])
                    if url_match and stem_lower in url_match.group(0).lower():
                        continue
                new_line, n = pattern.subn(
                    lambda m: f"[[{m.group(0)}]]", lines[i], count=1)
                if n:
                    lines[i] = new_line
                    links_added += n
        if links_added:
            p.write_text("\n".join(lines), encoding="utf-8")
        return links_added
    except Exception:
        return 0


def _index_note_now(note_path: str) -> None:
    """Index a single note immediately so it's searchable right away (instead
    of waiting for the background watcher). Failure-isolated.
    """
    try:
        vault_indexer._add_file(note_path)
        vault_indexer.persist()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Cross-book concept linking (LLM-free, semantic)
# ---------------------------------------------------------------------------
# Prevents info islands between textbooks.  Two books covering the same
# concept (calculus + physics both covering "derivatives") won't share
# filenames (slugged differently per book), so the title-based outbound
# linker misses them.  This pass uses the FAISS index to find semantically
# similar sections ACROSS textbooks and inserts bidirectional
# "Related sections" wikilinks so the books are woven into one graph.
#
# LLM-free: reuses the embeddings computed during the index pass.  Idempotent:
# the "Related sections" block is rewritten cleanly each run (no duplicates).
# Tight threshold: only strong semantic matches get linked, so we don't
# spam a note with 20 weakly-related links.

_CROSS_LINK_HEADER = "## Related sections"
# Relative distance threshold for cross-linking.  We use a RELATIVE
# threshold (not absolute) because raw L2 distances in 768-dim
# nomic-embed-text space run 45-195, not 0-1.  A cross-book candidate
# is linked if its distance is within _CROSS_LINK_DISTANCE_RATIO × the
# nearest cross-book candidate's distance.  With nearest=45 and ratio=2.0,
# that's ≤90 — catches genuine concept overlap (thermo sections at 45-60)
# while excluding unrelated notes (kinematics at 195).  Adapts to any
# embedding model's distance scale.
_CROSS_LINK_DISTANCE_RATIO = 2.0
_CROSS_LINK_MAX_PER_NOTE = 5  # cap links per note to avoid link spam
# Absolute floor: never link if the nearest cross-book candidate is farther
# than this.  Prevents linking in a vault where everything is roughly
# equidistant (no real semantic structure).  300 is well above the
# thermo-kinematics gap (195) so genuine matches always pass; a vault with
# only loosely-related notes won't get spam.
_CROSS_LINK_MAX_ABS_DISTANCE = 300.0


def _cross_link_textbooks(new_abs_paths: list[str],
                           emb_by_path: dict,
                           source_keys: Optional[set] = None) -> dict:
    """Cross-link the newly-ingested textbook sections to OTHER textbook
    sections in the vault that are semantically similar.

    For each new note, finds the top-k closest OTHER textbook notes via the
    FAISS index (excluding itself + notes from the same book), and inserts a
    "## Related sections" block with [[wikilinks]] to the strong matches.
    The link is bidirectional: the other note also gets a backlink to the
    new note.

    Args:
      new_abs_paths: absolute paths of the notes just ingested.
      emb_by_path: {abs_path: embedding_list} from the index pass (reused
        so we don't re-embed).
      source_keys: optional set of abs paths belonging to the SAME book as
        the new notes — these are excluded as cross-link targets (a book
        shouldn't cross-link to its own sections; the ingester handles
        intra-book nav).  If None, same-book exclusion is skipped.

    Returns {"cross_links_added": int, "notes_linked": int}; never raises.
    """
    out: Dict[str, Any] = {"cross_links_added": 0, "notes_linked": 0}
    try:
        textbooks_dir = (Path(os.getenv("VAULT_PATH", "."))
                         / "vaultbot" / "textbooks")
        if not textbooks_dir.exists():
            return out
        # Build the set of all textbook note paths (candidates for cross-linking).
        all_textbook_paths = [str(p) for p in textbooks_dir.rglob("*.md")]
        if len(all_textbook_paths) < 2:
            return out
        import numpy as _np
        for new_path in new_abs_paths:
            try:
                emb = emb_by_path.get(new_path)
                if emb is None:
                    continue
                # Find nearest neighbors among ALL indexed notes.
                hits = vault_indexer.search_by_vector(
                    _np.asarray(emb, dtype=_np.float32),
                    k=15)  # over-fetch then filter
                # Filter to: textbook notes, not self, not same book.
                # We collect ALL cross-book textbook candidates first, then
                # apply the relative distance threshold (link to candidates
                # within _CROSS_LINK_DISTANCE_RATIO × the nearest candidate's
                # distance).  This adapts to any embedding model's distance
                # scale — raw L2 in 768-dim space runs 45-195, not 0-1.
                candidates: list = []
                for h in hits:
                    fp = h.get("file_path", "")
                    if not fp or fp == new_path:
                        continue
                    fp_norm = str(Path(fp).resolve())
                    new_norm = str(Path(new_path).resolve())
                    if fp_norm == new_norm:
                        continue
                    # Must be a textbook note.
                    if "vaultbot" + os.sep + "textbooks" + os.sep not in fp_norm + os.sep:
                        continue
                    # Exclude same-book notes if we have source_keys.
                    if source_keys and fp_norm in source_keys:
                        continue
                    dist = h.get("score", 999.0)
                    candidates.append((fp, dist))
                if not candidates:
                    continue
                # Sort by distance (closest first).
                candidates.sort(key=lambda x: x[1])
                nearest = candidates[0][1]
                # Absolute floor: if even the nearest cross-book candidate is
                # very far away, there's no real semantic match — skip.
                if nearest > _CROSS_LINK_MAX_ABS_DISTANCE:
                    continue
                # Relative threshold: keep candidates within
                # _CROSS_LINK_DISTANCE_RATIO × nearest.
                cutoff = nearest * _CROSS_LINK_DISTANCE_RATIO
                links = [(fp, d) for fp, d in candidates if d <= cutoff]
                links = links[:_CROSS_LINK_MAX_PER_NOTE]
                if not links:
                    continue
                # Insert/refresh the "Related sections" block in the new note
                # + a backlink in each target note.
                added = _insert_related_block(new_path, links)
                if added:
                    out["cross_links_added"] += added
                    out["notes_linked"] += 1
            except Exception:
                continue
    except Exception:
        pass
    return out


def _insert_related_block(note_path: str,
                           links: list) -> int:
    """Insert (or refresh) a "## Related sections" block in a note with
    wikilinks to the given target paths, and insert a backlink block in
    each target.  Returns the number of links inserted; never raises.

    Idempotent: if the block already exists, it's rewritten cleanly (no
    duplicates).  The block is placed before the `---\n**Navigation:**`
    footer so it sits with the body, not in the nav.
    """
    try:
        p = Path(note_path)
        text = p.read_text(encoding="utf-8", errors="replace")
        # Build the new Related sections block.
        lines = []
        for fp, _dist in links:
            stem = Path(fp).stem
            lines.append(f"- [[{stem}]]")
        block = _CROSS_LINK_HEADER + "\n" + "\n".join(lines) + "\n"
        # Remove any existing Related sections block (idempotent refresh).
        text = _strip_related_block(text)
        # Insert before the navigation footer.
        nav_idx = text.find("\n---\n**Navigation:**")
        if nav_idx == -1:
            nav_idx = text.find("\n---\n")
        if nav_idx == -1:
            text = text.rstrip() + "\n\n" + block
        else:
            text = text[:nav_idx].rstrip() + "\n\n" + block + text[nav_idx:]
        p.write_text(text, encoding="utf-8")
        # Backlinks: add the new note to each target's Related sections block.
        new_stem = p.stem
        added = 0
        for fp, _dist in links:
            try:
                tp = Path(fp)
                ttext = tp.read_text(encoding="utf-8", errors="replace")
                ttext = _strip_related_block(ttext)
                back_block = (_CROSS_LINK_HEADER + "\n"
                              + f"- [[{new_stem}]]\n")
                tnav_idx = ttext.find("\n---\n**Navigation:**")
                if tnav_idx == -1:
                    tnav_idx = ttext.find("\n---\n")
                if tnav_idx == -1:
                    ttext = ttext.rstrip() + "\n\n" + back_block
                else:
                    ttext = (ttext[:tnav_idx].rstrip() + "\n\n"
                             + back_block + ttext[tnav_idx:])
                tp.write_text(ttext, encoding="utf-8")
                added += 1
            except Exception:
                continue
        return added + len(links)
    except Exception:
        return 0


def _strip_related_block(text: str) -> str:
    """Remove an existing '## Related sections' block from a note (idempotent)."""
    import re as _re
    # Match the header + its bullet lines up to the next blank line / heading / ---.
    pat = _re.compile(
        r"\n?## Related sections\n(?:- \[\[[^\]]+\]\]\n)+\n?",
        _re.MULTILINE)
    return pat.sub("\n", text)


async def _weave_textbook_notes(ingest_result: dict,
                                websocket: Optional[WebSocket] = None,
                                session_logger: Optional[Any] = None) -> dict:
    """Run the two post-ingest passes over every section note the ingester
    created or updated. Returns a summary; never raises.

    If `websocket` is provided, sends live progress events so the user sees
    "linking 47/129…" instead of a frozen screen during a long weave.
    """
    out: Dict[str, Any] = {
        "indexed": 0, "outbound_links_added": 0,
        "amem_evolved": 0, "amem_links_added": 0,
        "cross_links_added": 0, "notes_cross_linked": 0,
        "notes": [],
        "status": "complete",
    }
    sl = session_logger or default_session_logger
    try:
        created = ingest_result.get("notes_created", [])
        updated = ingest_result.get("notes_updated", [])
        note_rels = created + updated
        total = len(note_rels)
        if not note_rels:
            return out
        # Resolve absolute paths. The ingester returns paths relative to its
        # VAULT_DIR (which is <vault_root>/vaultbot), so we prepend vaultbot/
        # when joining to the vault root. We try both forms to be safe.
        vault_root = Path(os.getenv("VAULT_PATH", "."))
        title_map = _existing_note_titles()
        loop = asyncio.get_event_loop()

        if websocket is not None:
            await _send_progress(websocket, "weaving_begin", {
                "total_notes": total,
                "message": f"Linking {total} textbook notes into the vault..."})

        # Resolve all absolute paths first
        abs_paths: list[str] = []
        for rel in note_rels:
            candidate = (vault_root / "vaultbot" / rel).resolve()
            if not candidate.exists():
                candidate = (vault_root / rel).resolve()
            abs_paths.append(str(candidate))

        # --- Pass 1: batch-index all notes in parallel --- #
        # This is the slow part (embedding calls).  We fire them all at once
        # via ThreadPoolExecutor so Ollama processes them concurrently, and
        # we ASK FOR THE EMBEDDINGS BACK so the A-MEM pass below can reuse
        # them as neighbor-search queries instead of re-embedding each note
        # (saves one embedding call per note — ~129 calls on a big ingest).
        if websocket is not None:
            await _send_progress(websocket, "weaving_progress", {
                "note": 0, "total": total,
                "message": f"Indexing {total} notes in parallel..."})
        indexed, emb_by_path = await loop.run_in_executor(
            None, vault_indexer.batch_add_files, abs_paths, True)
        out["indexed"] = indexed

        # One graph refresh for the whole weave — the graph doesn't change
        # between consecutive notes in the same ingest, so refreshing once
        # here (instead of inside every evolve_on_create) saves N full vault
        # rescans.  A-MEM is told to skip its own refresh via skip_refresh.
        try:
            vault_graph.refresh()
        except Exception:
            pass

        # --- Pass 2: outbound links + A-MEM (sequential, fast) --- #
        # A-MEM runs in heuristic_only mode here: the per-neighbor LLM
        # tag-suggestion call is skipped entirely, so a 129-note ingest
        # makes ZERO generative LLM calls during the weave (the single-note
        # vault_research path still uses the LLM).  The heuristic adds the
        # new note's title as a tag + inserts a backlink — most of A-MEM's
        # value for textbook sections, which have unambiguous titles.
        for idx, (rel, abs_path) in enumerate(zip(note_rels, abs_paths)):
            if websocket is not None and (idx % 10 == 0 or idx == total - 1):
                await _send_progress(websocket, "weaving_progress", {
                    "note": idx + 1, "total": total,
                    "message": f"Linking note {idx+1}/{total}..."})

            # outbound-link into existing notes
            added = await loop.run_in_executor(
                None, _link_outbound, abs_path, title_map)
            out["outbound_links_added"] += added
            # A-MEM: evolve existing neighbors (old -> new backlinks).
            # heuristic_only=True skips the LLM; query_embedding reuses the
            # embedding we just computed during indexing; skip_refresh=True
            # because we refreshed the graph once above.
            try:
                content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = ""
            ev = await loop.run_in_executor(
                None, lambda c=content, a=abs_path: amem.evolve_on_create(
                    a, c,
                    heuristic_only=True,
                    query_embedding=emb_by_path.get(a),
                    skip_refresh=True))
            if ev.get("evolved_count"):
                out["amem_evolved"] += ev["evolved_count"]
            out["amem_links_added"] += ev.get("links_added", 0)
            out["notes"].append({
                "note": rel, "outbound": added,
                "neighbors_evolved": ev.get("evolved_count", 0),
            })

        # --- Pass 3: cross-book concept linking (LLM-free, semantic) --- #
        # The outbound linker (pass 2) explicitly excludes textbooks, so two
        # books covering the same concept (calculus + physics both covering
        # "derivatives") stay invisible to each other — info islands.  This
        # pass uses the FAISS index + the embeddings we already computed to
        # find semantically similar sections ACROSS textbooks and insert
        # bidirectional "## Related sections" wikilinks.  Tight distance
        # threshold (0.75) so only genuine concept overlap gets linked, not
        # "both are about math."  Idempotent.  Same-book notes excluded.
        if websocket is not None:
            await _send_progress(websocket, "weaving_progress", {
                "note": total, "total": total,
                "message": f"Cross-linking {total} notes to other textbooks..."})
        # Build the set of paths belonging to THIS ingest's book so we don't
        # cross-link a book to its own sections (intra-book nav is the
        # ingester's job).
        source_keys = set(abs_paths)
        cross = await loop.run_in_executor(
            None, _cross_link_textbooks, abs_paths, emb_by_path, source_keys)
        out["cross_links_added"] = cross.get("cross_links_added", 0)
        out["notes_cross_linked"] = cross.get("notes_linked", 0)

        # --- Pass 4: L1 concept cards (LLM-free abstraction layer) --- #
        # Build a terse concept card (~300-500 chars) for each L0 section so
        # the chat loop can walk the ABSTRACT graph (cards) instead of the
        # raw graph (full chapters).  Cards point back to their L0 source
        # via `> source: [[...]]`.  Zero LLM calls — extractive sketch only.
        # Cards are first-class vault nodes: indexed by FAISS, walked by the
        # link graph, hop-able by the LLM at ~1/100th the context cost of L0.
        if websocket is not None:
            await _send_progress(websocket, "weaving_progress", {
                "note": total, "total": total,
                "message": f"Building concept cards for {total} notes..."})
        try:
            card_result = await loop.run_in_executor(
                None, build_cards_batch, abs_paths, vault_graph, None)
            out["cards_built"] = card_result.get("cards_built", 0)
            card_paths = card_result.get("card_paths", [])
        except Exception as e:
            out["cards_built"] = 0
            card_paths = []
            try:
                sl.log("concept_card_build_failed", {"error": str(e)})
            except Exception:
                pass

        # --- Pass 5: L2 maps of content (incremental, graph-integrity-
        # preserving).  Cluster the L1 cards by embedding similarity and
        # write/update one MOC note per cluster.  INCREMENTAL: existing
        # clusters keep their IDs + members (so L2 abstractions stay
        # supported by their L1 cards — no "floating abstractions"); only
        # new/changed cards are assigned (to the nearest existing cluster
        # within threshold, or seed a new one), and only AFFECTED MOC notes
        # are rewritten.  Reuses the ingest embeddings — zero new embedding
        # calls for clustering; only the new cards needed indexing. --- #
        if card_paths:
            if websocket is not None:
                await _send_progress(websocket, "weaving_progress", {
                    "note": total, "total": total,
                    "message": f"Clustering {len(card_paths)} new cards into maps of content..."})
            # Index the new cards so they're in the FAISS index + get their
            # embeddings back for clustering.  This is the only new embedding
            # cost of the whole hierarchy build, and it's parallel + local.
            try:
                _cn, card_embs = await loop.run_in_executor(
                    None, vault_indexer.batch_add_files, card_paths, True)
                vault_graph.refresh()
                textbooks_dir = (Path(os.getenv("VAULT_PATH", ".")) / "vaultbot" / "textbooks")
                # Gather ALL L1 cards in the vault (incremental mode needs
                # the full set to preserve existing cluster assignments;
                # only the new subset gets assigned).  Merge the new
                # embeddings with any existing ones we can recover.
                all_card_paths = [str(p) for p in textbooks_dir.rglob("*-L1.md")]
                # The new cards' embeddings are in card_embs; for existing
                # cards not in this batch, recover their embeddings from the
                # FAISS index via search_by_vector on themselves (cheap —
                # we have the content).  Fall back to re-embedding only if
                # needed.
                full_embs = dict(card_embs)
                missing = [p for p in all_card_paths if p not in full_embs]
                if missing:
                    try:
                        _mn, recovered = await loop.run_in_executor(
                            None, vault_indexer.batch_add_files, missing, True)
                        full_embs.update(recovered)
                    except Exception:
                        pass
                moc_result = await loop.run_in_executor(
                    None, build_mocs_incremental, all_card_paths, full_embs,
                    str(textbooks_dir), card_paths, None)
                out["mocs_built"] = moc_result.get("mocs_built", 0)
                out["mocs_updated"] = moc_result.get("mocs_updated", 0)
                out["mocs_unchanged"] = moc_result.get("mocs_unchanged", 0)
                out["new_clusters"] = moc_result.get("new_clusters", 0)
                out["clusters"] = moc_result.get("clusters", [])
                # Re-index the MOC notes that were written/updated.
                moc_paths = moc_result.get("moc_paths", [])
                if moc_paths:
                    await loop.run_in_executor(
                        None, vault_indexer.batch_add_files, moc_paths, False)
                try:
                    sl.log("hierarchy_built", {
                        "cards": out.get("cards_built", 0),
                        "mocs": out.get("mocs_built", 0),
                        "mocs_updated": out.get("mocs_updated", 0),
                        "mocs_unchanged": out.get("mocs_unchanged", 0),
                        "new_clusters": out.get("new_clusters", 0),
                        "clusters": len(out.get("clusters", []))})
                except Exception:
                    pass
            except Exception as e:
                out["mocs_built"] = 0
                try:
                    sl.log("moc_build_failed", {"error": str(e)})
                except Exception:
                    pass
        else:
            out["mocs_built"] = 0

        if websocket is not None:
            await _send_progress(websocket, "weaving_done", {
                "total_notes": total,
                "indexed": out["indexed"],
                "outbound_links": out["outbound_links_added"],
                "amem_evolved": out["amem_evolved"],
                "amem_links": out["amem_links_added"],
                "cross_links": out.get("cross_links_added", 0),
                "notes_cross_linked": out.get("notes_cross_linked", 0),
                "cards_built": out.get("cards_built", 0),
                "mocs_built": out.get("mocs_built", 0),
                "message": (f"Done: {out['outbound_links_added']} outbound links, "
                            f"{out['amem_evolved']} neighbors evolved, "
                            f"{out.get('cross_links_added', 0)} cross-book links, "
                            f"{out.get('cards_built', 0)} concept cards, "
                            f"{out.get('mocs_built', 0)} maps of content "
                            f"across {total} notes.")})

        sl.log("textbook_weave_complete", {
            "total": total, "indexed": out["indexed"],
            "outbound_links": out["outbound_links_added"],
            "amem_evolved": out["amem_evolved"],
            "amem_links": out["amem_links_added"],
            "cross_links": out.get("cross_links_added", 0),
            "notes_cross_linked": out.get("notes_cross_linked", 0),
            "cards_built": out.get("cards_built", 0),
            "mocs_built": out.get("mocs_built", 0)})
    except Exception as e:
        out["error"] = str(e)
        out["status"] = "error"
        sl.log("textbook_weave_failed", {"error": str(e)})
        if websocket is not None:
            await _send_progress(websocket, "weaving_done", {
                "message": f"Weaving completed with errors: {str(e)[:100]}"})
    return out


def _tool_result_summary(tool_name: str, result: Any) -> str:
    """Human-readable one-line summary of a tool result for the UI."""
    if not isinstance(result, dict):
        return str(result)[:200]
    if result.get("error"):
        return f"error: {str(result['error'])[:150]}"
    if tool_name == "vault_research":
        return (f"{result.get('source_count', 0)} sources, "
                f"{result.get('synthesis_facts', 0)} facts"
                + (f", note: {Path(result['note_path']).stem}"
                   if result.get("note_path") else ""))
    if tool_name == "vault_search":
        return f"{len(result.get('results', []))} notes found"
    if tool_name == "vault_gaps":
        return f"{result.get('count', 0)} gaps found"
    if tool_name == "vaultbot_status":
        st = result
        return ("running" if st.get("running") else "stopped") + \
               f", {st.get('history_count', 0)} cycles"
    if tool_name == "code_read":
        return f"{result.get('total_lines', 0)} lines from {result.get('file_path', '?')}"
    if tool_name == "code_run":
        return f"exit {result.get('exit_code', '?')}: {str(result.get('stdout', ''))[:80]!r}"
    if tool_name == "tool_create":
        return f"{result.get('status', '?')}: {result.get('tool_name', '?')}"
    if tool_name == "self_reflect":
        return f"reflection: {str(result.get('reflection', ''))[:80]!r}"
    if tool_name == "git_rollback":
        return f"restored {result.get('restored', '?')}"
    if tool_name == "safe_write":
        st = result.get("status", "?")
        if st == "written":
            return f"safe_write: wrote {result.get('bytes', 0)} bytes to {result.get('file_path', '?')} (verified)"
        if st == "dry_run_ok":
            return f"safe_write dry_run: OK — would write safely"
        return f"safe_write {st}: {str(result.get('error', ''))[:80]}"
    if tool_name == "capability_audit":
        return f"{result.get('total', 0)} tools ({result.get('kinds', {})})"
    # Custom tools: try to extract a meaningful key.
    if isinstance(result, dict) and result.get("result"):
        return str(result["result"])[:120]
    return str(result)[:200]


async def handle_research(websocket: WebSocket, user_message: str, session_logger: SessionLogger):
    """Deep-research the web via the LLM-light engine, create a linked note,
    then answer from the note + vault.

    The dig itself uses NO LLM — only extractive synthesis over corroborated
    sources. The LLM only sees the finished, sourced summary at the end.
    """
    session_logger.log("research_begin", {"user_message": user_message})
    await manager.send_personal_message(json.dumps({"type": "status", "content": "Researching the web (deep dig)..."}), websocket, session_logger=session_logger)
    loop = asyncio.get_event_loop()

    t0 = loop.time()
    # Wire a thread-safe progress callback so the UI shows each search round.
    prev_cb = research_engine.progress_callback
    def _progress_cb(stage: str, detail: dict):
        try:
            asyncio.run_coroutine_threadsafe(
                _send_progress(websocket, stage, detail), loop)
        except Exception:
            pass
    research_engine.progress_callback = _progress_cb
    try:
        report = await _run_with_heartbeat(
            websocket, "research", research_engine.research, user_message)
    except Exception as e:
        research_engine.progress_callback = prev_cb
        session_logger.log_exception(e, context="research_engine.research")
        await manager.send_personal_message(json.dumps({"type": "error", "content": f"Research failed: {e}"}), websocket, session_logger=session_logger)
        return
    finally:
        research_engine.progress_callback = prev_cb
    session_logger.log("deep_research", {
        "query": user_message,
        "source_count": report.get("source_count", 0),
        "facts": report.get("synthesis_facts", 0),
        "rounds": len(report.get("rounds", [])),
        "duration_ms": (loop.time() - t0) * 1000,
    })

    if not report.get("source_count"):
        await manager.send_personal_message(json.dumps({"type": "error", "content": "No web sources found."}), websocket, session_logger=session_logger)
        session_logger.log("research_error", {"stage": "search", "error": "no_sources"})
        return

    research_text = report.get("synthesis", "")
    if not research_text:
        research_text = " ".join(s.get("snippet", "") for s in report.get("sources", [])[:3])

    await manager.send_personal_message(json.dumps({"type": "status", "content": "Creating linked note..."}), websocket, session_logger=session_logger)
    await _send_progress(websocket, "writing_note", {"topic": _derive_topic(user_message)})

    try:
        topic = report.get("topic") or _derive_topic(user_message)
        summary = (f"Deep research into '{topic}' "
                   f"({report.get('source_count', 0)} sources, "
                   f"{report.get('synthesis_facts', 0)} facts).")
        if len(summary) > 800:
            summary = summary[:797] + "..."
        note_path = await _run_with_heartbeat(
            websocket, "writing_note",
            note_creator.create_note_from_research, topic, research_text, summary)
        # Overwrite with the richer markdown so sources + follow-ups persist.
        try:
            md = research_engine.synthesize_note_markdown(report, summary)
            Path(note_path).write_text(md, encoding="utf-8")
        except Exception:
            pass
        session_logger.log("research_note_created", {"note_path": note_path, "topic": topic})
    except Exception as e:
        session_logger.log_exception(e, context="note_creator.create_note_from_research")
        await manager.send_personal_message(json.dumps({"type": "error", "content": f"Note creation failed: {e}"}), websocket, session_logger=session_logger)
        return

    await manager.send_personal_message(json.dumps({"type": "status", "content": f"Created note: {Path(note_path).name}"}), websocket, session_logger=session_logger)
    session_logger.log("research_end", {"note_path": note_path})

    # Refresh graph after writing so subsequent chats see the updated vault state
    vault_graph.refresh()
    await handle_chat(websocket, user_message, session_logger)


def _derive_topic(user_message: str) -> str:
    """Derive a concise note title from the user's research request."""
    cleaned = user_message.strip().rstrip("?").lower()
    for word in ["what is", "what are", "research", "tell me about", "explain", "define"]:
        cleaned = cleaned.replace(word, "")
    cleaned = cleaned.strip().title()
    return cleaned if cleaned else "Research Note"


# Kept for backward compatibility; graph context is now built by build_graph_context.
def build_context(results: list) -> str:
    if not results:
        return "VAULT CONTEXT: (no relevant notes found)"
    lines = ["VAULT CONTEXT:"]
    for i, res in enumerate(results, 1):
        file_path = res.get("file_path", "")
        note_name = Path(file_path).stem if file_path else "Unknown"
        lines.append(f"\n--- Note {i}: [[{note_name}]] ---")
        lines.append(res.get("content", "")[:1500])
    return "\n".join(lines)

# --- /task: plain-English → verified plan execution (model-robust) --------

@app.post("/task")
async def create_task(payload: dict):
    """Take a plain-English task, decompose it into a JSON plan of atomic
    idempotent graph-op subtasks (each with a deterministic verifier), and
    execute them against the curated graph-op vocabulary.

    The plan is persisted to disk so it survives crashes and model swaps —
    a fresh session can resume from the plan file. Completion is decided by
    a judge (deterministic verifier + optional LLM), not the worker's
    self-report.

    POST body: {"goal": "<plain English>", "execute": true}
    If execute is false, returns the plan without running it.
    """
    goal = (payload.get("goal") or "").strip()
    if not goal:
        return {"error": "missing goal"}, 400
    execute_now = payload.get("execute", True)

    loop = asyncio.get_event_loop()

    # Step 1: ask the LLM to decompose the goal into atomic graph-op subtasks.
    # Each subtask has: op (one of the 7), args, verifier (Python expression
    # over result), intent. This is the one reasoning step where the model
    # matters most — but the output is JSON with a fixed schema, so even a
    # weak model produces a usable plan (NL2GQL evidence: schema grounding >
    # parameter scale).
    op_names = list(graph_op_registry.ops.keys())
    op_descriptions = "\n".join(
        f"  - {name}: {s['function']['description'][:120]}"
        for name, s in zip(op_names, GRAPH_OP_SCHEMAS))
    decompose_prompt = (
        "You are a task planner for VaultBot, a self-improving research agent "
        "in an Obsidian vault. Decompose the user's goal into a sequence of "
        "atomic, verifiable subtasks using ONLY these graph operations:\n"
        f"{op_descriptions}\n\n"
        "Return a JSON object: {\"subtasks\": [{\"op\": \"...\", "
        "\"intent\": \"...\", \"args\": {...}, \"verifier\": \"result.get('count',0) > 0\"}]}.\n"
        "Each verifier is a Python expression over `result`. Make every subtask "
        "idempotent and independently verifiable. Be specific.\n\n"
        f"User goal: {goal}\n\nReturn ONLY valid JSON, no prose.")
    try:
        plan_response = await loop.run_in_executor(
            None, lambda: ollama_client.chat(
                [{"role": "user", "content": decompose_prompt}],
                temperature=0.3, stream=False))
    except Exception as e:
        default_session_logger.log_exception(e, context="task_decompose")
        return {"error": f"decomposition failed: {e}"}, 500

    raw = plan_response.get("message", {}).get("content", "") if isinstance(plan_response, dict) else ""
    # Parse the JSON plan (tolerate code fences / preamble).
    try:
        plan_data = json.loads(_extract_json(raw))
    except Exception:
        return {"error": "could not parse plan", "raw": raw[:500]}, 500

    subtask_dicts = plan_data.get("subtasks", [])
    if not subtask_dicts:
        return {"error": "plan has no subtasks", "raw": raw[:500]}, 500

    # Build the Plan object.
    import time as _time
    plan_id = f"task_{int(_time.time())}"
    subtasks = []
    for i, sd in enumerate(subtask_dicts):
        subtasks.append(Subtask(
            id=f"S{i+1}",
            op=sd.get("op", ""),
            intent=sd.get("intent", ""),
            args=sd.get("args", {}),
            verifier=sd.get("verifier", "True"),
            max_attempts=int(sd.get("max_attempts", 5))))
    plan = Plan(id=plan_id, goal=goal, subtasks=subtasks)

    # Persist the plan so it survives crashes and model swaps.
    plans_dir = Path(__file__).with_name("plans")
    plans_dir.mkdir(exist_ok=True)
    plan_path = plans_dir / f"{plan_id}.json"
    plan_executor.save_plan(plan, str(plan_path))

    if not execute_now:
        return {"plan_id": plan_id, "plan": plan_executor.plan_to_json(plan),
                "executed": False}

    # Execute the plan (graph ops are idempotent; verifier gates each step).
    try:
        plan = await loop.run_in_executor(None, plan_executor.execute, plan)
    except Exception as e:
        default_session_logger.log_exception(e, context="task_execute")
        return {"error": f"execution failed: {e}", "plan_id": plan_id}, 500
    plan_executor.save_plan(plan, str(plan_path))

    # Judge completion (deterministic fallback if no LLM).
    judgment = plan_executor.judge(plan)
    return {"plan_id": plan_id, "plan": plan_executor.plan_to_json(plan),
            "judgment": judgment, "executed": True}


def _write_partial(path: Path, user_message: str, answer: str, thinking: str) -> None:
    """Write the streamed-so-far answer to a partial file for crash recovery.
    Called after each answer_chunk so a crash mid-stream preserves progress.
    Never raises — a partial-write failure must not kill the chat loop.
    """
    try:
        from datetime import datetime, timezone
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        content = (
            f"---\npartial: true\ncreated: {ts}\n---\n\n"
            f"# Partial Answer (crash recovery)\n\n"
            f"## User asked\n{user_message}\n\n"
            f"## Answer so far\n{answer}\n\n"
            f"## Thinking so far\n{thinking[:2000]}\n"
        )
        path.write_text(content, encoding="utf-8")
    except Exception:
        pass


def _extract_json(text: str) -> str:
    """Extract a JSON object from text that may have code fences or prose."""
    import re as _re
    # Strip code fences.
    m = _re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, _re.DOTALL)
    if m:
        return m.group(1)
    # Find the first { ... } block.
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i+1]
    return text.strip()


@app.get("/task/{plan_id}")
async def get_task(plan_id: str):
    """Retrieve a persisted plan's status."""
    plan_path = Path(__file__).with_name("plans") / f"{plan_id}.json"
    if not plan_path.exists():
        return {"error": "plan not found"}, 404
    try:
        plan = plan_executor.load_plan(str(plan_path))
        return {"plan": plan_executor.plan_to_json(plan),
                "judgment": plan_executor.judge(plan)}
    except Exception as e:
        return {"error": str(e)}, 500


@app.post("/task/{plan_id}/resume")
async def resume_task(plan_id: str):
    """Resume a partially-completed plan from disk."""
    plan_path = Path(__file__).with_name("plans") / f"{plan_id}.json"
    if not plan_path.exists():
        return {"error": "plan not found"}, 404
    loop = asyncio.get_event_loop()
    try:
        plan = await loop.run_in_executor(None, plan_executor.resume, str(plan_path))
    except Exception as e:
        return {"error": str(e)}, 500
    plan_executor.save_plan(plan, str(plan_path))
    return {"plan": plan_executor.plan_to_json(plan),
            "judgment": plan_executor.judge(plan)}


# --- /identity: the three-file identity layer ---------------------------

@app.get("/identity")
async def get_identity():
    """Return the agent's current identity state (IDENTITY + SELF_MODEL +
    GOALS) so the UI can show who the agent is and what it's working on."""
    return {
        "identity": identity.get_identity(),
        "self_model": identity.get_self_model(),
        "goals": identity.get_goals(),
        "summary": identity.summary(),
    }


@app.post("/identity/goals")
async def set_goals(payload: dict):
    """Update the agent's active goal (full-replace GOALS.md)."""
    goal = payload.get("goal", "")
    steps = payload.get("steps", [])
    completed = payload.get("completed_step")
    next_step = payload.get("next_step")
    if not goal:
        return {"error": "missing goal"}, 400
    text = identity.update_goals(goal, steps, completed, next_step)
    return {"goals": text, "summary": identity.summary()}


@app.post("/identity/self_model")
async def regenerate_self_model(payload: dict):
    """Regenerate the MIRROR-style bounded self-model from recent activity.

    This is the bounded reconstructive synthesis (regenerate, don't append)
    that gave +5-20% across 7 architecturally diverse models (MIRROR,
    arXiv:2506.00430). The self-model is a ≤3000-token first-person narrative
    that makes the agent coherent across days regardless of model.
    """
    activity = payload.get("activity", "")
    loop = asyncio.get_event_loop()
    try:
        new_model = await loop.run_in_executor(
            None, lambda: identity.regenerate_self_model(activity))
    except Exception as e:
        return {"error": str(e)}, 500
    return {"self_model": new_model, "summary": identity.summary()}


# --- /health: liveness endpoint for watchdog / monitoring ---------------

@app.get("/health")
async def health():
    """Liveness check. Returns uptime, heartbeat age, current task, and
    dependency status so a watchdog (or the Obsidian plugin) can detect hangs
    and restart if needed. Keep this <50ms.
    """
    extra = {
        "ollama": _ping_ollama(),
        "autonomous_enabled": autonomous_researcher.enabled,
        "autonomous_running": bool(autonomous_researcher._thread and
                                   autonomous_researcher._thread.is_alive()),
        "index_vectors": vault_indexer.index.ntotal if vault_indexer.index else 0,
        "graph_nodes": len(vault_graph.nodes),
        "identity_self_model_chars": len(identity.get_self_model()),
    }
    return health_monitor.health(extra=extra)


def _ping_ollama() -> bool:
    """Quick check that Ollama is responding."""
    try:
        import requests
        r = requests.get(f"{ollama_client.base_url}/api/version", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


@app.post("/shutdown")
async def shutdown_endpoint(request: Request):
    """Self-terminate the backend so the Obsidian plugin can stop it
    deterministically on close without relying on Windows taskkill from a
    process that is itself tearing down. We flush the response first, then
    run the graceful shutdown path (stop autonomous researcher, persist the
    index) in a background thread and hard-exit. os._exit is the hard
    guarantee — no signal handling, no dependence on the event loop staying
    alive — so the process always dies even if a background thread is stuck.

    Accepts and ignores any request body (including the Blob sent by
    navigator.sendBeacon during teardown, which FastAPI would otherwise 422
    on a body-less handler). We drain+discard the body so the request is
    fully consumed before we exit.
    """
    import threading

    # Drain any request body (sendBeacon sends a Blob) so Starlette doesn't
    # log a warning about an unread body; the content is irrelevant.
    try:
        await request.body()
    except Exception:
        pass

    def _terminate():
        try:
            # Give the HTTP response time to flush back to the client.
            import time
            time.sleep(0.25)
            # Run the graceful shutdown path synchronously (best effort).
            try:
                autonomous_researcher.stop()
            except Exception:
                pass
            try:
                loop = asyncio.get_event_loop()
                loop.run_until_complete(vault_indexer.stop_watching())
                loop.run_until_complete(vault_indexer.persist())
            except Exception:
                pass
            try:
                release_lock()
            except Exception:
                pass
        finally:
            os._exit(0)

    threading.Thread(target=_terminate, daemon=True).start()
    return {"status": "shutting_down"}


# --- /checkpoints: crash-recovery status --------------------------------

@app.get("/checkpoints")
async def checkpoint_status():
    """Return the autonomous researcher's checkpoint state so the UI can
    show whether there's interrupted work to resume after a crash.
    """
    return checkpointer.summary()


@app.post("/checkpoints/recover")
async def recover_checkpoints():
    """Manually trigger recovery of any interrupted research work."""
    try:
        loop = asyncio.get_event_loop()
        recovery = await loop.run_in_executor(None, checkpointer.recover, autonomous_researcher)
        return recovery
    except Exception as e:
        return {"error": str(e)}, 500


@app.get("/supervision/nssm")
async def nssm_install_script():
    """Return the nssm install commands so the user can install VaultBot as a
    Windows service that starts on boot, restarts on crash, and rotates logs.
    Run the output in an admin terminal to install.
    """
    vaultbot_dir = str(Path(__file__).parent.resolve())
    python_exe = str(Path(sys.executable).resolve())
    log_dir = str(Path(vaultbot_dir).parent / "logs")
    return {
        "install": generate_nssm_install(vaultbot_dir, python_exe, log_dir),
        "uninstall": generate_nssm_uninstall(),
        "instructions": (
            "1. Install nssm: https://nssm.cc/download\n"
            "2. Open an admin terminal\n"
            "3. Paste the install commands\n"
            "4. VaultBot will start on boot, restart on crash, and run for days.\n"
            "5. Logs rotate at 10MB in: " + log_dir
        ),
    }


if __name__ == "__main__":
    # access_log=False: the 5s /health poll from the plugin was producing
    # ~17k log lines/day. Keep error logging on; drop the per-request
    # access lines. Structured events go to session_logger, not stdout.
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)