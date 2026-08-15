"""FastAPI application entry point — lifespan, middleware, WebSocket manager.

Constructs all singleton services at startup, wires them into a ``Services``
dataclass, and registers route handlers from ``routers/``. The PID lock
prevents two backends from running on the same vault.
"""

import asyncio
import atexit
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

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
from dotenv import load_dotenv
from embedding_drift import EmbeddingDrift
from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fused_retrieval import FusedRetriever
from graph_ops import GraphOpRegistry
from identity import Identity
from knowledge_curriculum import KnowledgeCurriculum
from lazy_condenser import LazyCondenser
from llm_client import LLMClient, build_role_client
from providers import ProviderRegistry
from note_creator import NoteCreator

# Import our modules
from plan_executor import PlanExecutor
from research_engine import ResearchEngine
from self_improver import SelfImprover
from session_logger import SessionLogger
from supervision import HealthMonitor
from vault_graph import VaultGraph
from vault_indexer import VaultIndexer

from forum_backends import ForumEnhancedFreeSearch

# Use the forum-enhanced version: adds GitHub Issues + StackOverflow
# backends, skips arXiv for technical queries, prioritizes forum results.
FreeSearch = ForumEnhancedFreeSearch  # noqa: F811  — intentional override of the base class
from calibration import CalibrationTracker
from claim_verifier import ClaimVerifier
from context_budgeter import ContextBudgeter
from pattern_extractor import PatternExtractor
from procedure_tracker import ProcedureTracker
from rag_eval import RAGEvaluator

# Load environment variables from the parent directory (Vault2 root).
# override=True ensures .env values win over any stale env passed by the
# Obsidian plugin spawn (which used to carry an empty TAVILY_API_KEY).
dotenv_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
load_dotenv(dotenv_path, override=True)

# Resolve VAULT_PATH relative to the vault root (two levels up from this
# file).  When .env says VAULT_PATH=. and the process cwd is the backend
# dir, "." resolves to the backend dir — not the vault root.  This makes
# every vault_path=os.getenv("VAULT_PATH", ".") call site resolve correctly
# regardless of the process working directory.
_VAULT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_vp = os.environ.get("VAULT_PATH", ".")
if not os.path.isabs(_vp):
    os.environ["VAULT_PATH"] = os.path.normpath(os.path.join(_VAULT_ROOT, _vp))

# NOTE: The hand-rolled _verify_imports() AST checker that used to live here
# has been replaced by `ruff check` (F821 — undefined-name) configured in
# pyproject.toml.  ruff catches the same "forgot the import" bugs but on
# EVERY file, not just main.py, and doesn't add startup overhead.  Run
# `ruff check vaultbot_backend/` before deploy (see CONTRIBUTING.md).

# Prevent duplicate backend instances on the same vault.
PID_FILE = Path(__file__).with_name("vaultbot.pid")


def _check_pid_alive(pid: int) -> bool:
    if os.name == "nt":
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
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
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
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
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
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
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
                    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
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
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                startup_logger.log_exception(e, context="background_index")
                app_state.set_startup_reindex_failed(str(e))

        asyncio.create_task(background_index())

        # ── Schema self-heal ──────────────────────────────────────────
        # On every boot, scan all vault .md files and auto-inject missing
        # required frontmatter fields (type, status, created, summary, tags).
        # This heals notes written before the schema was introduced AND
        # notes edited externally (e.g. via Obsidian) that lost frontmatter.
        # Runs in the background so it never blocks the server from accepting
        # connections.  Only writes files that actually changed.
        async def background_schema_heal():
            try:
                from note_schema import heal_vault_schema

                _vault_root = os.getenv("VAULT_PATH", ".")

                def _heal():
                    return heal_vault_schema(
                        _vault_root,
                        logger=lambda msg: startup_logger.log(
                            "schema_heal", {"msg": msg}
                        ),
                    )

                result = await loop.run_in_executor(None, _heal)
                startup_logger.log(
                    "schema_heal_complete",
                    {
                        "scanned": result["scanned"],
                        "healed": result["healed"],
                        "skipped": result["skipped"],
                        "errors": result["errors"],
                    },
                )
            except Exception as e:  # noqa: BLE001 — best-effort
                startup_logger.log_exception(e, context="schema_heal")

        asyncio.create_task(background_schema_heal())

        # ── Build initial QA queue ────────────────────────────────────
        # On boot, build the priority-ordered QA queue (most-used notes
        # first, from touch_counts.json) and persist it to qa_queue.json.
        # The idle-time QA worker (triggered after each chat reply) pulls
        # from this queue.  Rebuilding on boot ensures new notes are added
        # and stale queue entries are pruned.
        async def background_build_qa_queue():
            try:
                from qa_worker import build_qa_queue, save_qa_queue

                _vault_root = os.getenv("VAULT_PATH", ".")

                def _build():
                    q = build_qa_queue(_vault_root)
                    save_qa_queue(q)
                    return len(q)

                count = await loop.run_in_executor(None, _build)
                startup_logger.log("qa_queue_built", {"notes": count})
            except Exception as e:  # noqa: BLE001 — best-effort
                startup_logger.log_exception(e, context="build_qa_queue")

        asyncio.create_task(background_build_qa_queue())

        startup_logger.log(
            "server_startup",
            {
                "stage": "end",
                "status": "ok",
                "vectors": vault_indexer.index.ntotal if vault_indexer.index else 0,
            },
        )

        # ── Model preload ──────────────────────────────────────────────
        # Ollama loads models lazily: the first request to a cold model
        # triggers a full load from disk (up to 5 min for a 27B model).  By
        # preloading at startup, the model is resident in GPU memory before
        # the user ever sends their first message.  This runs in a background
        # thread so it never blocks the server from accepting connections.
        # The preload is a no-op for cloud backends (OpenAICompatibleClient).
        # Skip if disabled via VAULTBOT_PRELOAD_ON_STARTUP=0.
        if os.environ.get("VAULTBOT_PRELOAD_ON_STARTUP", "1") != "0":

            def _preload_models():
                _preloaded = []

                def _preload_with_retry(client, label):
                    """Preload a model with retry — keeps trying until Ollama
                    is available (it may not be up yet when the backend starts)
                    or the max wait is reached."""
                    _max_wait = int(
                        os.environ.get("VAULTBOT_PRELOAD_MAX_WAIT_S", "300")
                    )
                    _interval = 10  # retry every 10s
                    _elapsed = 0
                    while _elapsed < _max_wait:
                        try:
                            if client.preload_model():
                                return True
                        except Exception:  # noqa: BLE001 — best-effort retry
                            pass
                        time.sleep(_interval)
                        _elapsed += _interval
                    startup_logger.log(
                        "model_preload_timeout", {"model": label, "waited_s": _elapsed}
                    )
                    return False

                # Big model (chat/reasoning).
                _preload_with_retry(ollama_client, "big")
                _preloaded.append(ollama_client.llm_model)
                # Small model (tiny dance partner for procedures).
                try:
                    if small_client is not None:
                        _preload_with_retry(small_client, "small")
                        _preloaded.append(small_client.llm_model)
                except Exception as e:  # noqa: BLE001
                    startup_logger.log(
                        "model_preload_startup_failed",
                        {"model": "small", "error": str(e)},
                    )
                startup_logger.log("models_preloaded", {"models": _preloaded})

            # Run in a DEDICATED thread (not the default ThreadPoolExecutor)
            # so a long preload (up to 300s for a cold large model) can't
            # starve the executor pool that endpoints like /models and
            # /llm/providers/live_models rely on for run_in_executor calls.
            import threading as _threading

            _preload_thread = _threading.Thread(
                target=_preload_models, name="startup-preload", daemon=True
            )
            _preload_thread.start()

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
                    startup_logger.log(
                        "checkpointer_recovered",
                        {
                            "interrupted_count": len(recovery["recovered"]),
                            "topics": [c.topic for c in recovery["recovered"]],
                        },
                    )
                    # Re-queue the interrupted gaps so the next cycle
                    # researches them FIRST, before the curriculum's
                    # normal proposals. This is the actual retry.
                    recovered_gaps = []
                    for ckpt in recovery["recovered"]:
                        recovered_gaps.append(
                            ckpt.gap
                            if ckpt.gap
                            else {
                                "kind": ckpt.kind,
                                "topic": ckpt.topic,
                                "priority": 9999,  # top priority
                                "normalized_name": ckpt.topic.lower(),
                                "referenced_by": [],
                            }
                        )
                    if recovered_gaps:
                        autonomous_researcher._recovered_gaps = recovered_gaps
                        startup_logger.log(
                            "checkpointer_requeued",
                            {
                                "count": len(recovered_gaps),
                                "topics": [g["topic"] for g in recovered_gaps],
                            },
                        )
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                startup_logger.log("checkpointer_recovery_failed", {"error": str(e)})
            startup_logger.log("autonomous_researcher_started", {})
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            startup_logger.log_exception(e, context="autonomous_researcher_start")
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
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
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            shutdown_logger.log_exception(e, context="autonomous_researcher_stop")
        # Stop watching the vault for changes and persist the index
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, vault_indexer.stop_watching)
        await loop.run_in_executor(None, vault_indexer.persist)
        shutdown_logger.log("server_shutdown", {"stage": "end", "status": "ok"})
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        shutdown_logger.log_exception(e, context="server_shutdown")
    finally:
        shutdown_logger.close()


app = FastAPI(title="VaultBot API", lifespan=lifespan)

# Allow the Obsidian Electron app (origin app://obsidian.md) and local browsers
# to call the API without browser CORS preflight blocks.
#
# CORS note: allow_credentials is intentionally NOT set (defaults to False).
# Starlette silently ignores credentials when allow_origins=["*"] — the
# combination was a misconfiguration that read as "credentials protected" while
# doing nothing. This backend is localhost-only and does not use cookies or
# auth headers, so credentials are genuinely unnecessary. If a future change
# tightens allow_origins to a real origin list, re-enable allow_credentials
# there — not here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate limiting middleware ────────────────────────────────────────────
# Token-bucket rate limiter that runs BEFORE auth (so unauthenticated
# requests are also rate-limited). Per-endpoint limits prevent a buggy
# agentic loop or malicious local process from hammering the backend.
# See rate_limit.py for the full design.
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from rate_limit import is_rate_allowed


class _RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        client = request.client.host if request.client else "127.0.0.1"
        if not is_rate_allowed(request.url.path, client):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please wait before sending more requests."
                },
            )
        return await call_next(request)


app.add_middleware(_RateLimitMiddleware)

app.add_middleware(_RateLimitMiddleware)

# Default global session logger for startup/shutdown and background tasks.
default_session_logger = SessionLogger()

# ── Provider + Model Registry (the interchangeable "pot") — the ONLY path ──
# The three cartridges (big/small/vision) all resolve through the provider
# registry. There are no .env cartridge factories. On first run with no
# providers.json, migrate_from_env() synthesizes providers from the legacy
# .env cartridge vars ONE LAST TIME so the user's previous config carries
# over; from then on the pot is the single source of truth and the .env vars
# are dead. build_role_client(role) builds the live client for whichever
# model the role points at, on whichever provider serves it — local Ollama,
# OpenRouter, OpenAI — all interchangeable. If the big role is unassigned we
# fail loud (no silent wrong-model fallback).
registry = ProviderRegistry.migrate_from_env()

ollama_client: LLMClient | None = None
vision_client: LLMClient | None = None
small_client: LLMClient | None = None
try:
    ollama_client = build_role_client("big", registry, default_session_logger)
    vision_client = build_role_client("vision", registry, default_session_logger)
    small_client = build_role_client("small", registry, default_session_logger)
except Exception as _reg_err:  # noqa: BLE001 — surface, then fall through to fail-loud path
    print(f"[WARN] provider registry client build failed: {_reg_err}")

if ollama_client is None:
    # Fail loud: no big model assigned. The user MUST configure one in
    # Settings -> AI Models & Providers. We still keep the backend process
    # alive so the settings UI is reachable, but chat will surface the error.
    from diagnostics import classify_error

    _llm_diag = classify_error(
        RuntimeError(
            "No model assigned to the 'big' cartridge in "
            "providers.json. Settings -> AI Models & Providers."
        ),
        {"stage": "startup"},
    )
    default_session_logger.log(
        "llm_no_big_model",
        {
            "category": _llm_diag.category.value,
            "user_message": _llm_diag.user_message,
        },
    )
    print(f"[CONFIG ERROR] {_llm_diag.user_message}")
    # Minimal sentinel so downstream attribute access doesn't AttributeError
    # before the user configures a model. is_running() -> False.
    from llm_client import LLMClient as _LC

    ollama_client = _LC()  # type: ignore[abstract]
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
except Exception as _searxng_err:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
    print(
        f"[startup] SearXNG backend disabled (Docker/SearxngManager unavailable: {_searxng_err})"
    )

search_client = FreeSearch(
    session_logger=default_session_logger,
    searxng_manager=searxng_manager,
)
vault_indexer = VaultIndexer(
    vault_path=os.getenv("VAULT_PATH", "."), session_logger=default_session_logger
)
vault_graph = VaultGraph(
    vault_path=os.getenv("VAULT_PATH", "."), session_logger=default_session_logger
)
note_creator = NoteCreator(
    vault_path=os.getenv("VAULT_PATH", "."),
    indexer=vault_indexer,
    session_logger=default_session_logger,
)

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
    vault_graph=vault_graph, session_logger=default_session_logger
)

# Checkpointer: persists the autonomous researcher's in-flight work so a
# crashed/restarted backend can resume mid-research instead of losing it.
# OpenHands event-sourcing pattern (arXiv:2511.03690). Instantiated BEFORE
# the autonomous researcher so the researcher can use it.
checkpointer = Checkpointer(
    checkpoint_dir=str(Path(__file__).with_name("checkpoints")),
    session_logger=default_session_logger,
)

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
    import json as _json
    from diagnostics import classify_error

    try:
        diag = classify_error(RuntimeError(error), {"stage": "autonomous researcher"})
        diag.user_message = (
            "VaultBot's autonomous researcher stopped unexpectedly. "
            "It won't fill knowledge gaps on its own until you restart. "
            "Your chat still works normally."
        )
        diag.remedy_hint = "Click Restart to start the researcher again."
        payload = _json.dumps({"type": "problem", "diagnosis": diag.to_dict()})
        # manager is module-level (defined below); at call time (crash) it
        # will already be assigned. The researcher thread outlives startup.
        if manager is not None and manager.active_connections:
            if main_event_loop is not None and main_event_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    manager.broadcast(payload), main_event_loop
                )
        default_session_logger.log(
            "problem_notified",
            {
                "category": diag.category.value,
                "user_message": diag.user_message,
                "source": "autonomous_researcher_crash",
            },
        )
    except Exception as notify_err:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        # The crash notification itself failed — log it loudly.
        # This is the one place where we MUST not silently pass:
        # if the user doesn't know the researcher crashed, they think
        # the vault is being maintained when it isn't.
        default_session_logger.log_exception(
            notify_err, context="autonomous_researcher_crash_notification"
        )
        print(
            f"[CRITICAL] Researcher crashed AND crash notification failed: {notify_err}"
        )


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

# Identity layer (IDENTITY.md): makes the agent feel like
# the same agent across days regardless of which model is in the slot.
identity = Identity(
    identity_dir=str(Path(__file__).with_name("identity")),
    ollama_client=ollama_client,
    session_logger=default_session_logger,
)

# Curated graph-op vocabulary (small, fixed, idempotent, verifiable): the 7
# ops the plan executor calls. Also exposed to the LLM for direct tool-calling.
graph_op_registry = GraphOpRegistry(
    vault_graph=vault_graph,
    vault_indexer=vault_indexer,
    note_creator=note_creator,
    research_engine=research_engine,
    ollama_client=ollama_client,
    session_logger=default_session_logger,
)

# Model-robust plan executor: takes a JSON plan of atomic idempotent subtasks,
# each with a deterministic verifier, executes them against the graph ops,
# and closes the loop with a judge — not the worker model's self-report.
plan_executor = PlanExecutor(
    op_registry=graph_op_registry.ops, session_logger=default_session_logger
)

# A-MEM note evolution (arXiv:2502.12110): when a new note is created, evolve
# its neighbors' tags/links so the vault "learns by refining."
amem = AMemeEvolution(
    vault_path=os.getenv("VAULT_PATH", "."),
    vault_graph=vault_graph,
    vault_indexer=vault_indexer,
    ollama_client=ollama_client,
    session_logger=default_session_logger,
)

# Fused retrieval (vector + wikilink graph + backlinks): replaces flat FAISS
# search in the chat loop so the vault reasons graph-awarely.  The embedding-
# drift layer (relevance feedback) is wired in so retrieval ranks by "what
# is this note good FOR" (accumulated feedback), not just "what is it similar
# to" — notes that proved helpful for similar queries drift toward them.
embedding_drift = EmbeddingDrift(
    state_path=Path(__file__).with_name("embedding_drift.json"),
    session_logger=default_session_logger,
)
fused_retriever = FusedRetriever(
    vault_graph=vault_graph,
    vault_indexer=vault_indexer,
    embedding_drift=embedding_drift,
    session_logger=default_session_logger,
)

# Chat-loop checkpoint/resume (multi-day sturdiness): snapshots an in-flight
# agentic turn (round idx, tool history, working memory, partial answer) so a
# crash/restart RESUMES mid-turn instead of restarting it. Distinct from the
# research `checkpointer` (which snapshots the autonomous researcher's gap
# list). One file, atomic writes, cleared on normal completion.
from chat_checkpoint import ChatLoopCheckpointer

# Legacy singleton for back-compat (non-session callers). Per-session
# checkpoints are created via ChatLoopCheckpointer.for_session(session_id)
# in chat_handler.py — this singleton is only used as a fallback.
chat_checkpointer = ChatLoopCheckpointer(
    state_path=Path(__file__).with_name("chat_loop_checkpoint.json"),
    session_logger=default_session_logger,
)

# Context management: a sliding window (not LLM-based compaction) bounds
# the conversation sent to the LLM. The vault IS the memory — chat history
# is persisted as notes (Memory/Chat/Chat-*.md) and the agent can walk the
# wikilink trail back via vault_search. working_memory.py keeps it on task
# (the plan_task / update_task tool pair replaces the old GOALS.md file).
# The old Compactor (LLM summarization of old messages) was
# lossy, costly, and introduced its own failure modes (summarization
# failure → silent degradation). The sliding window is deterministic,
# non-lossy, and zero-cost. See chat_handler._apply_sliding_window.
#
# The lazy_condenser + context_budgeter remain — they manage VAULT note
# density and retrieved-context size, not conversation history.

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
    ollama_client=ollama_client,
    session_logger=default_session_logger,
)

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
# On probe failure, fall back to the VAULTBOT_CONTEXT_LIMIT env var (which
# the user explicitly set). If that's also unset, log loudly — do NOT
# silently guess 128K (wrong for an 8K model).
_ctx_limit = 0
try:
    _ctx_limit = ollama_client.context_window() or 0
except Exception as _ctx_probe_err:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
    default_session_logger.log(
        "context_window_probe_failed",
        {
            "error": str(_ctx_probe_err),
        },
    )
    _ctx_limit = 0
if not _ctx_limit:
    _env_limit = int(os.getenv("VAULTBOT_CONTEXT_LIMIT", "0") or "0")
    if not _env_limit:
        # Neither probe nor env var gave us a limit. Default to 128K as a
        # generous ceiling for modern models, but log it so it's visible
        # — this is a notified default, not a silent guess.
        default_session_logger.log(
            "context_window_defaulted",
            {
                "reason": "probe failed and VAULTBOT_CONTEXT_LIMIT not set",
                "default": 131072,
            },
        )
        _env_limit = 131072
    _ctx_limit = _env_limit
context_budgeter = ContextBudgeter(model_context_limit=_ctx_limit)

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

    async def send_personal_message(
        self, message: str, websocket: WebSocket, session_logger: SessionLogger = None
    ):
        try:
            await websocket.send_text(message)
        except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
            # Client likely disconnected; don't crash the server
            if session_logger is not None:
                session_logger.log("websocket_send_failed", {"error": str(e)})
            return
        if session_logger is not None:
            # Skip per-message logging for high-frequency streaming events
            # (answer_chunk, thinking, heartbeat). These fire 50+/sec and
            # the per-token JSONL writes create disk I/O backpressure that
            # throttles the LLM's streaming throughput. The full conversation
            # is saved at session end via save_history().
            try:
                _msg = json.loads(message)
                _msg_type = _msg.get("type", "")
                if _msg_type not in ("answer_chunk", "thinking", "heartbeat"):
                    session_logger.log_message("out", _msg)
            except json.JSONDecodeError:
                session_logger.log_message("out", {"raw": message})

    async def broadcast(self, message: str, session_logger: SessionLogger = None):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
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
from app_state import set_services  # Phase 3: DI surface for routers
from conversation_index import ConversationIndexRegistry

# Conversation-aware retrieval: a per-session registry of searchable indexes
# of recent conversation turns.  Each tab gets its own index so cross-tab
# recall is impossible.  The legacy single-index behavior is preserved for
# callers that don't pass a session_id (the registry returns a throwaway).
conversation_index = ConversationIndexRegistry(ollama_client=ollama_client)
try:
    from conversation_state import load_history

    _prior_history = load_history()
    if _prior_history:
        # Rebuild into a temporary index for the log; the per-session
        # indexes are rebuilt on-demand when each tab connects.
        _temp_idx = ConversationIndexRegistry(ollama_client=ollama_client)
        _temp_idx.rebuild_from_history(_prior_history, session_id="_startup")
        default_session_logger.log(
            "conversation_index_restored",
            {
                "turns": _temp_idx.size,
            },
        )
except Exception as e:  # noqa: BLE001
    default_session_logger.log("conversation_index_restore_failed", {"error": str(e)})

svc = Services(
    ollama_client=ollama_client,
    vision_client=vision_client,
    small_client=small_client,
    registry=registry,
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
    lazy_condenser=lazy_condenser,
    context_budgeter=context_budgeter,
    conversation_index=conversation_index,
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
    _proc_idx = procedure_tracker.get_procedure_index(os.getenv("VAULT_PATH", "."))
    fused_retriever.procedure_status_index = {
        stem: entry.get("frontmatter", {}).get("status", "")
        for stem, entry in _proc_idx.items()
    }
    # Cache the stem index on the tracker so the chat handler's
    # deterministic procedure hint, the procedure surface's `provides`
    # expansion, and the preflight chain cartridge detection all work on
    # the FIRST turn after startup — not only after the first
    # execute_procedure call (which may never happen without the hint).
    # The lazy rebuild in chat_handler._run_procedure_direct stays as a
    # safety net for procedures created/edited mid-session.
    procedure_tracker._stem_index = _proc_idx
except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
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
from routers import tournament as _tournament_router

app.include_router(_system_router.router)
app.include_router(_llm_router.router)
app.include_router(_config_router.router)
app.include_router(_research_router.router)
app.include_router(_autonomous_router.router)
app.include_router(_custom_tools_router.router)
app.include_router(_task_router.router)
app.include_router(_identity_router.router)
app.include_router(_ws_router.router)
app.include_router(_tournament_router.router)


@app.post("/reload-plugin")
async def reload_plugin_endpoint():
    """Broadcast a reload_plugin WebSocket message so the Obsidian plugin
    reloads its main.js without the user manually toggling it in Settings.
    Used after editing the plugin code (main.js/styles.css) so changes take
    effect immediately. The backend stays running — only the plugin reloads.
    """
    import asyncio

    asyncio.ensure_future(
        manager.broadcast(json.dumps({"type": "reload_plugin"})), main_event_loop
    )
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
    except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
        logger.debug("swallowed: %s", e)

    def _terminate():
        try:
            # Give the HTTP response time to flush back to the client.
            import time

            time.sleep(0.25)
            # Run the graceful shutdown path synchronously (best effort).
            try:
                autonomous_researcher.stop()
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                logger.debug("swallowed: %s", e)
            try:
                loop = asyncio.get_event_loop()
                loop.run_until_complete(vault_indexer.stop_watching())
                loop.run_until_complete(vault_indexer.persist())
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
                logger.debug("swallowed: %s", e)
            try:
                release_lock()
            except Exception as e:  # noqa: BLE001 — best-effort — see CONTRIBUTING.md no-silent-fallbacks
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
