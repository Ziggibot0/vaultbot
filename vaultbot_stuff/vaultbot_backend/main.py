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

# NOTE: the startup-reindex-failure flag lives in app_state.py (not here)
# so routers/ws.py can read it via `from app_state import
# get_startup_reindex_failed` instead of `import main` — a bare
# `import main` re-executes this module's top-level code (including
# acquire_lock() → sys.exit) and crashes every WebSocket handler.
# main.py writes to it via app_state.set_startup_reindex_failed().

# Main event loop reference, set during lifespan startup. Used by
# background-thread callbacks (e.g. researcher crash) to schedule
# coroutines on the main loop via run_coroutine_threadsafe.
main_event_loop: asyncio.AbstractEventLoop | None = None

import app_state
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

# Load environment variables from the parent directory (Vault2 root).
# override=True ensures .env values win over any stale env passed by the
# Obsidian plugin spawn (which used to carry an empty TAVILY_API_KEY).
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
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
    # Atomic claim: os.open with O_CREAT|O_EXCL is the only cross-process
    # primitive that creates-and-locks in one syscall. The old read-check-
    # write sequence raced under concurrent spawns (two processes both read
    # no/old pid, both wrote, both proceeded -> duplicate zombies idling).
    # Try once exclusively; on EEXIST, verify the recorded pid is alive and
    # exit if so, or unlink a stale file and retry once.
    import errno
    for _ in range(2):
        try:
            fd = os.open(str(PID_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode())
            finally:
                os.close(fd)
            return
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
        try:
            old_pid = int(PID_FILE.read_text().strip())
        except Exception as e:
            logger.debug("swallowed: %s", e)
            old_pid = None
        if old_pid and _check_pid_alive(old_pid):
            print(f"VaultBot backend already running (PID {old_pid}). Exiting.")
            sys.exit(0)
        # Stale lock file (process gone) -> unlink and loop to retry the
        # exclusive create. If the unlink itself races another starter, the
        # second iteration's O_EXCL will settle it.
        try:
            PID_FILE.unlink()
        except OSError:
            pass
    # Could not claim after retry (extreme race) -> exit rather than
    # risk a duplicate.
    print("VaultBot backend lock contention; another starter won. Exiting.")
    sys.exit(0)

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
        global main_event_loop
        loop = asyncio.get_event_loop()
        main_event_loop = loop
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
                app_state.set_startup_reindex_failed(str(e))
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

def _researcher_crash_callback(error: str) -> None:
    """Broadcast a type:"problem" WS event when the researcher thread crashes.

    Called from the researcher's daemon thread (not the main event loop), so
    it uses asyncio.run_coroutine_threadsafe to bridge into the main loop.
    If no loop is available or no connections are active, the problem is
    still logged to the default session logger.
    """
    import json as _json  # noqa: PLC0415
    from diagnostics import classify_error  # noqa: PLC0415
    try:
        diag = classify_error(RuntimeError(error),
                               {"stage": "autonomous researcher"})
        diag.user_message = (
            "VaultBot's autonomous researcher stopped unexpectedly. "
            "It won't fill knowledge gaps on its own until you restart. "
            "Your chat still works normally.")
        diag.remedy_hint = "Click Restart to start the researcher again."
        payload = _json.dumps({"type": "problem", "diagnosis": diag.to_dict()})
        # manager is module-level (defined below); at call time (crash) it
        # will already be assigned. The researcher thread outlives startup.
        if manager is not None and manager.active_connections:
            if main_event_loop is not None and main_event_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    manager.broadcast(payload), main_event_loop)
        default_session_logger.log("problem_notified", {
            "category": diag.category.value,
            "user_message": diag.user_message,
            "source": "autonomous_researcher_crash",
        })
    except Exception:
        pass  # the callback must never raise

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
    ollama_client=ollama_client,
    on_crash=_researcher_crash_callback,
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

# Chat-loop checkpoint/resume (multi-day sturdiness): snapshots an in-flight
# agentic turn (round idx, tool history, working memory, partial answer) so a
# crash/restart RESUMES mid-turn instead of restarting it. Distinct from the
# research `checkpointer` (which snapshots the autonomous researcher's gap
# list). One file, atomic writes, cleared on normal completion.
from chat_checkpoint import ChatLoopCheckpointer
chat_checkpointer = ChatLoopCheckpointer(
    state_path=Path(__file__).with_name("chat_loop_checkpoint.json"),
    session_logger=default_session_logger)

# Context compactor (OpenHands Condenser pattern): summarizes conversation
# middle when history grows too long, preventing context overflow on long
# chats without losing the thread. The token threshold is now 500K (see
# compactor.py) — scaled to glm-5.2:cloud's 1M context window. The old 40-msg
# / 12K-token thresholds fired after a single tool round, summarizing away
# the tool result the model just received and producing empty answers.
# 200 messages allows a long multi-step agentic session (25 rounds × 2-3
# messages each = ~50-75 msgs) before compaction touches anything.
compactor = Compactor(
    ollama_client=ollama_client, session_logger=default_session_logger,
    max_messages=int(os.getenv("VAULTBOT_COMPACT_MAX_MESSAGES", "200")))

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
# Resolve the model's ACTUAL context window (queries Ollama /api/show) instead
# of hardcoding 32768 — with a large-window model (glm-5.2:cloud = 128K+), a
# 32K assumption made the budgeter shrink the retrieved context to a useless
# stub while the REAL flood came from the un-budgeted 49K legacy fallback.
# Fallback to 128K (not 32K) on failure so a probe error can't silently
# re-shrink context.
_ctx_limit = 0
try:
    _ctx_limit = ollama_client.context_window() or 0
except Exception:
    _ctx_limit = 0
context_budgeter = ContextBudgeter(
    model_context_limit=_ctx_limit or int(os.getenv("VAULTBOT_CONTEXT_LIMIT", "131072")))

# Calibration tracker: uses the operator's corrections as ground truth to calibrate
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
    chat_checkpointer=chat_checkpointer,
    manager=manager,
)

# Phase 3: wire verified-procedure status into FUSED retrieval so verified
# procedures get a small score bump (VERIFIED_BOOST).  The status map is
# stem -> "verified"|"flagged"|"experimental" pulled from the tracker's
# procedure index.  Refreshed lazily on each execute_procedure call by
# chat_handler (which rebuilds the stem index); here we seed it once at
# startup so the boost is active before the first procedure call.
try:
    _proc_idx = procedure_tracker.get_procedure_index(
        os.getenv("VAULT_PATH", "."))
    fused_retriever.procedure_status_index = {
        stem: entry.get("frontmatter", {}).get("status", "")
        for stem, entry in _proc_idx.items()
    }
except Exception as e:
    default_session_logger.log("procedure_status_index_failed", {"error": str(e)})

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
from routers import autonomous as _autonomous_router
from routers import custom_tools as _custom_tools_router
from routers import task as _task_router
from routers import identity as _identity_router
from routers import ws as _ws_router
app.include_router(_system_router.router)
app.include_router(_llm_router.router)
app.include_router(_config_router.router)
app.include_router(_research_router.router)
app.include_router(_autonomous_router.router)
app.include_router(_custom_tools_router.router)
app.include_router(_task_router.router)
app.include_router(_identity_router.router)
app.include_router(_ws_router.router)

@app.post("/reload-plugin")
async def reload_plugin_endpoint():
    """Broadcast a reload_plugin WebSocket message so the Obsidian plugin
    reloads its main.js without the user manually toggling it in Settings.
    Used after editing the plugin code (main.js/styles.css) so changes take
    effect immediately. The backend stays running — only the plugin reloads.
    """
    import asyncio
    asyncio.ensure_future(
        manager.broadcast(json.dumps({"type": "reload_plugin"})),
        main_event_loop)
    return {"status": "reload_broadcast"}

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
