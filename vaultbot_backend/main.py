import asyncio
import atexit
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── OpenMP conflict guard ──────────────────────────────────────────────────
# faster-whisper (CTranslate2) ships libomp140.dll while faiss/torch/onnxruntime
# ship libiomp5md.dll. When both load in one process (e.g. /stt runs whisper
# after faiss/onnxruntime are already loaded), OpenMP init crashes the whole
# backend. This must be set BEFORE any numpy/torch/faiss import. The official
# workaround per the OMP error message.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import uvicorn
from amem_evolution import AMemeEvolution
from autonomous_researcher import AutonomousResearcher
from checkpointer import Checkpointer
from compactor import Compactor
from dotenv import load_dotenv
from embedding_drift import EmbeddingDrift
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from free_search import FreeSearch
from fused_retrieval import FusedRetriever
from graph_ops import GraphOpRegistry
from identity import Identity
from knowledge_curriculum import KnowledgeCurriculum
from lazy_condenser import LazyCondenser
from llm_client import LLMClient, get_llm_client, get_vision_client
from note_creator import NoteCreator

# Import our modules
from plan_executor import PlanExecutor
from research_engine import ResearchEngine
from self_improver import SelfImprover
from session_logger import SessionLogger
from supervision import HealthMonitor, generate_nssm_install, generate_nssm_uninstall
from vault_graph import VaultGraph
from vault_indexer import VaultIndexer

try:
    from forum_backends import ForumEnhancedFreeSearch
    # Use the forum-enhanced version: adds GitHub Issues + StackOverflow
    # backends, skips arXiv for technical queries, prioritizes forum results.
    FreeSearch = ForumEnhancedFreeSearch
except Exception as _forum_err:
    print(f"[startup] Forum backends unavailable, using base FreeSearch: {_forum_err}")
from calibration import CalibrationTracker
from claim_verifier import ClaimVerifier
from context_budgeter import ContextBudgeter
from pattern_extractor import PatternExtractor
from procedure_tracker import ProcedureTracker
from rag_eval import RAGEvaluator
from speech import list_voices as tts_voices
from speech import set_logger as speech_set_logger
from speech import synthesize as tts_synthesize
from speech import transcribe as stt_transcribe

# Load environment variables from the parent directory (Vault2 root).
# override=True ensures .env values win over any stale env passed by the
# Obsidian plugin spawn (which used to carry an empty TAVILY_API_KEY).
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path, override=True)

# NOTE: The hand-rolled _verify_imports() AST checker that used to live here
# has been replaced by `ruff check` (F821 — undefined-name) configured in
# pyproject.toml.  ruff catches the same "forgot the import" bugs but on
# EVERY file, not just main.py, and doesn't add startup overhead.  Run
# `ruff check vaultbot_backend/` before deploy (see CONTRIBUTING.md).

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
vision_client: LLMClient | None = get_vision_client(
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
from app_state import set_services, get_services  # Phase 3: DI surface for routers

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

# Phase 3: register the singleton so routers using Depends(get_services)
# receive the live Services instance.  This must run BEFORE app.include_router
# is called for any router (the router handlers dereference get_services at
# request time, so the order within startup doesn't matter, but set it now
# so it's impossible to forget).
set_services(svc)

# -- Phase 3: include routers (extracted route groups) --
# Each router module reads the Services singleton via Depends(get_services)
# instead of main.py's module-level globals.  Migrated routes are deleted
# from main.py as they move into routers/.  See routers/__init__.py for the
# migration order.
from routers import system as _system_router
from routers import llm as _llm_router
from routers import config as _config_router
from routers import research as _research_router
from routers import voice as _voice_router
from routers import autonomous as _autonomous_router
from routers import custom_tools as _custom_tools_router
from routers import task as _task_router
from routers import identity as _identity_router
from routers import ws as _ws_router
app.include_router(_system_router.router)
app.include_router(_llm_router.router)
app.include_router(_config_router.router)
app.include_router(_research_router.router)
app.include_router(_voice_router.router)
app.include_router(_autonomous_router.router)
app.include_router(_custom_tools_router.router)
app.include_router(_task_router.router)
app.include_router(_identity_router.router)
app.include_router(_ws_router.router)

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


if __name__ == "__main__":
    # access_log=False: the 5s /health poll from the plugin was producing
    # ~17k log lines/day. Keep error logging on; drop the per-request
    # access lines. Structured events go to session_logger, not stdout.
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)
