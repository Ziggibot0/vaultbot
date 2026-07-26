import os
import sys
import re
import json
import asyncio
import atexit
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional, List

logger = logging.getLogger(__name__)

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
    # Allow tests / subprocess smoke checks to import main without
    # triggering the PID lock (which would sys.exit if a backend is
    # running). Set VAULTBOT_SKIP_LOCK=1 in the environment.
    if os.environ.get("VAULTBOT_SKIP_LOCK", "") == "1":
        return
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
        except Exception as e:
            logger.debug("swallowed: %s", e)
            old_pid = None
        if old_pid and _check_pid_alive(old_pid):
            print(f"VaultBot backend already running (PID {old_pid}). Exiting.")
            sys.exit(0)
    PID_FILE.write_text(str(os.getpid()))

def release_lock() -> None:
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except Exception as e:
        logger.debug("swallowed: %s", e)

acquire_lock()
atexit.register(release_lock)


# ─── Lifespan: modern replacement for @app.on_event ──────────────────────
# The deprecated @app.on_event("startup"/"shutdown") is scheduled for
# removal in FastAPI. The lifespan context manager is the documented
# replacement (https://fastapi.tiangolo.com/advanced/testing-events/).
# It also lets TestClient(app) control startup/shutdown for endpoint tests.
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ── (moved from the old @app.on_event("startup") handler)
    # Truncate oversized stdout/stderr logs so they can't grow unbounded
    # (was 256MB, mostly GET / heartbeat noise). Keep the last 1MB.
    for _log_name in ("backend_stdout.log", "backend_stderr.log"):
        _log_path = Path(__file__).parent / _log_name
        try:
            if _log_path.exists() and _log_path.stat().st_size > 10 * 1024 * 1024:
                _log_path.with_name(_log_name).write_bytes(b"")
        except Exception as e:
            logger.debug("swallowed: %s", e)

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
                    except Exception as e:
                        logger.debug("swallowed: %s", e)
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

    yield

    # ── Shutdown ── (moved from the old @app.on_event("shutdown") handler)
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


app = FastAPI(title="VaultBot API", lifespan=lifespan)

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
            except Exception as e:
                logger.debug("swallowed: %s", e)
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
                        except Exception as e:
                            logger.debug("swallowed: %s", e)
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
    from chat_helpers import send_progress
    await send_progress(svc, websocket, stage, detail)


async def _heartbeat(websocket: WebSocket, label: str,
                      start_time: float, interval: float = 2.0) -> None:
    """Push a one-shot heartbeat so the UI can render elapsed time + a
    'still alive' pulse. Called periodically by long-running executors."""
    from chat_helpers import heartbeat
    await heartbeat(svc, websocket, label, start_time, interval)


async def _run_with_heartbeat(websocket: WebSocket, label: str,
                               coro_or_fn, *args, **kwargs) -> Any:
    """Run a blocking call in an executor while emitting heartbeats so the
    user is never staring at a frozen 'Calling X...' line.

    `coro_or_fn` is a plain callable (run in the default executor). Heartbeats
    fire every `interval` seconds with the elapsed time, and a final
    progress event fires when the call returns."""
    from chat_helpers import run_with_heartbeat
    return await run_with_heartbeat(svc, websocket, label, coro_or_fn, *args, **kwargs)


async def handle_chat(websocket: WebSocket, user_message: str, session_logger: SessionLogger):
    """Agentic chat: the LLM reasons over the vault, calls tools (research,
    search, gaps, status) when it hits a gap, and produces a grounded answer.

    This is the Jarvis loop — the LLM self-directs instead of shrugging.
    """
    from chat_handler import handle_chat as _handle_chat_impl
    await _handle_chat_impl(svc, websocket, user_message, session_logger)


async def _execute_agent_tool(tool_name: str, args: Dict[str, Any],
                              session_logger: SessionLogger,
                              websocket: Optional[WebSocket] = None) -> Dict[str, Any]:
    """Execute one tool call from the chat LLM. Runs in the async context.

    `websocket` is passed so long-running tools (vault_research) can push
    live progress events to the UI instead of going silent for 30-60s.
    """
    from chat_handler import execute_agent_tool
    return await execute_agent_tool(svc, tool_name, args, session_logger, websocket)


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
    from weaving import existing_note_titles
    return existing_note_titles(svc)


def _is_ignored_index_path(p: Path) -> bool:
    """True for vault subpaths the indexer/graph ignore (venv, index, etc.)."""
    from weaving import is_ignored_index_path
    return is_ignored_index_path(p)


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
    from weaving import link_outbound
    return link_outbound(note_path, title_map)


def _index_note_now(note_path: str) -> None:
    """Index a single note immediately so it's searchable right away (instead
    of waiting for the background watcher). Failure-isolated.
    """
    from weaving import index_note_now
    return index_note_now(svc, note_path)


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
    from weaving import cross_link_textbooks
    return cross_link_textbooks(svc, new_abs_paths, emb_by_path, source_keys)


def _insert_related_block(note_path: str,
                           links: list) -> int:
    """Insert (or refresh) a "## Related sections" block in a note with
    wikilinks to the given target paths, and insert a backlink block in
    each target.  Returns the number of links inserted; never raises.

    Idempotent: if the block already exists, it's rewritten cleanly (no
    duplicates).  The block is placed before the `---\n**Navigation:**`
    footer so it sits with the body, not in the nav.
    """
    from weaving import insert_related_block
    return insert_related_block(note_path, links)


def _strip_related_block(text: str) -> str:
    """Remove an existing '## Related sections' block from a note (idempotent)."""
    from weaving import strip_related_block
    return strip_related_block(text)


async def _weave_textbook_notes(ingest_result: dict,
                                websocket: Optional[WebSocket] = None,
                                session_logger: Optional[Any] = None) -> dict:
    """Run the two post-ingest passes over every section note the ingester
    created or updated. Returns a summary; never raises.

    If `websocket` is provided, sends live progress events so the user sees
    "linking 47/129…" instead of a frozen screen during a long weave.
    """
    from weaving import weave_textbook_notes
    return await weave_textbook_notes(svc, ingest_result, websocket, session_logger)


def _tool_result_summary(tool_name: str, result: Any) -> str:
    """Human-readable one-line summary of a tool result for the UI."""
    from chat_helpers import tool_result_summary
    return tool_result_summary(tool_name, result)


async def handle_research(websocket: WebSocket, user_message: str, session_logger: SessionLogger):
    """Deep-research the web via the LLM-light engine, create a linked note,
    then answer from the note + vault.

    Thin shim — body extracted to research_handler.handle_research (which
    receives the Services registry so it reads singletons via svc.<name>
    instead of as free variables).
    """
    from research_handler import handle_research as _handle_research_impl
    return await _handle_research_impl(svc, websocket, user_message, session_logger)


def _derive_topic(user_message: str) -> str:
    """Derive a concise note title from the user's research request.

    Thin shim — body extracted to research_handler.derive_topic.
    """
    from research_handler import derive_topic
    return derive_topic(user_message)


# Kept for backward compatibility; graph context is now built by build_graph_context.
def build_context(results: list) -> str:
    """Thin shim — body extracted to research_handler.build_context."""
    from research_handler import build_context as _build_context_impl
    return _build_context_impl(results)

# --- /task: plain-English → verified plan execution (model-robust) --------

@app.post("/task")
async def create_task(payload: dict):
    """Take a plain-English task, decompose it into a JSON plan of atomic
    idempotent graph-op subtasks (each with a deterministic verifier), and
    execute them against the curated graph-op vocabulary.

    Thin shim — body extracted to task_api.create_task (which receives the
    Services registry so it reads singletons via svc.<name>).
    """
    from task_api import create_task as _create_task_impl
    return await _create_task_impl(svc, payload)


def _write_partial(path: Path, user_message: str, answer: str, thinking: str) -> None:
    """Write the streamed-so-far answer to a partial file for crash recovery.

    Thin shim — body extracted to task_api.write_partial.
    """
    from task_api import write_partial
    return write_partial(path, user_message, answer, thinking)


def _extract_json(text: str) -> str:
    """Extract a JSON object from text that may have code fences or prose.

    Thin shim — body extracted to task_api.extract_json.
    """
    from task_api import extract_json
    return extract_json(text)


@app.get("/task/{plan_id}")
async def get_task(plan_id: str):
    """Retrieve a persisted plan's status.

    Thin shim — body extracted to task_api.get_task.
    """
    from task_api import get_task as _get_task_impl
    return await _get_task_impl(svc, plan_id)


@app.post("/task/{plan_id}/resume")
async def resume_task(plan_id: str):
    """Resume a partially-completed plan from disk.

    Thin shim — body extracted to task_api.resume_task.
    """
    from task_api import resume_task as _resume_task_impl
    return await _resume_task_impl(svc, plan_id)


# --- /identity: the three-file identity layer ---------------------------

@app.get("/identity")
async def get_identity():
    """Return the agent's current identity state (IDENTITY + SELF_MODEL +
    GOALS) so the UI can show who the agent is and what it's working on.

    Thin shim — body extracted to identity_api.get_identity.
    """
    from identity_api import get_identity as _get_identity_impl
    return await _get_identity_impl(svc)


@app.post("/identity/goals")
async def set_goals(payload: dict):
    """Update the agent's active goal (full-replace GOALS.md).

    Thin shim — body extracted to identity_api.set_goals.
    """
    from identity_api import set_goals as _set_goals_impl
    return await _set_goals_impl(svc, payload)


@app.post("/identity/self_model")
async def regenerate_self_model(payload: dict):
    """Regenerate the MIRROR-style bounded self-model from recent activity.

    This is the bounded reconstructive synthesis (regenerate, don't append)
    that gave +5-20% across 7 architecturally diverse models (MIRROR,
    arXiv:2506.00430). The self-model is a ≤3000-token first-person narrative
    that makes the agent coherent across days regardless of model.

    Thin shim — body extracted to identity_api.regenerate_self_model.
    """
    from identity_api import regenerate_self_model as _regen_impl
    return await _regen_impl(svc, payload)


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
    except Exception as e:
        logger.debug("swallowed: %s", e)

    def _terminate():
        try:
            # Give the HTTP response time to flush back to the client.
            import time
            time.sleep(0.25)
            # Run the graceful shutdown path synchronously (best effort).
            try:
                autonomous_researcher.stop()
            except Exception as e:
                logger.debug("swallowed: %s", e)
            try:
                loop = asyncio.get_event_loop()
                loop.run_until_complete(vault_indexer.stop_watching())
                loop.run_until_complete(vault_indexer.persist())
            except Exception as e:
                logger.debug("swallowed: %s", e)
            try:
                release_lock()
            except Exception as e:
                logger.debug("swallowed: %s", e)
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
