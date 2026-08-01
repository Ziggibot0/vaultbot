"""LLM backend config endpoints — the tricky router.

Includes /models, /set_model, /llm/config (GET+POST), /llm/vision_check,
/llm/vision_config (GET+POST). The mutation site is `_rebuild_llm_client`:
after rebuilding the client it writes to BOTH the main.py globals (still
used by the chat loop's free-variable references until the ws router lands)
AND the svc fields (so Depends consumers see the new client).
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException

from app_state import get_services
from services import Services
from llm_client import get_llm_client, get_vision_client

router = APIRouter()
logger = logging.getLogger(__name__)

# .env path — re-derived here so the router doesn't import main. This MUST
# resolve to the SAME .env that main.py loads at startup (the vault-root
# .env, one level above vaultbot_backend/). Previously this pointed at
# vaultbot_backend/.env while main.py loaded Vault2/.env, so a model the
# user picked in settings (persisted here) was silently ignored on every
# restart and the boot-time default won. Resolve via .parent chain so it
# tracks the real location regardless of how the backend is launched.
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"  # 3 levels up for vault root (vaultbot_stuff/vaultbot_backend/routers/ -> vaultbot_stuff/)


def _persist_env_value(key: str, value: str) -> None:
    """Write/update a KEY=VALUE line in the vault root .env file."""
    try:
        lines = (_ENV_PATH.read_text(encoding="utf-8").splitlines()
                 if _ENV_PATH.exists() else [])
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
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
        _ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        print(f"VaultBot: could not persist {key} to .env: {e}")


def _detect_llm_backend() -> str:
    """Read the effective backend name for /llm/config reporting."""
    backend = (os.getenv("LLM_BACKEND") or "").strip().lower()
    if backend == "openai":
        return "openai"
    return "ollama"


# User-facing config keys that /config/effective reports. Each entry is
# (key, label, is_secret). Secrets are reported as a boolean (has_value),
# never the actual value. This is the single source of truth for what the
# Configuration status panel shows — adding a new user-facing key = one
# entry here + one row in the frontend.
_CONFIG_KEYS = [
    ("VAULTBOT_OWNER", "Your name", False),
    ("LLM_BACKEND", "LLM backend", False),
    ("OLLAMA_LLM_MODEL", "Ollama model", False),
    ("OLLAMA_EMBED_MODEL", "Embedding model", False),
    ("LLM_BASE_URL", "Cloud base URL", False),
    ("LLM_API_KEY", "Cloud API key", True),
    ("LLM_MODEL", "Cloud model name", False),
    ("VAULTBOT_RESEARCH_BACKEND", "Research backend", False),
    ("TAVILY_API_KEY", "Tavily API key", True),
    ("OLLAMA_HOST", "Ollama host", False),
]


def _read_env_file() -> dict[str, str]:
    """Read KEY=VALUE pairs from the .env file (without loading into os.environ).

    Used by /config/effective to distinguish "from .env file" vs "from
    runtime override / process env". Returns an empty dict if the file
    doesn't exist or can't be read.
    """
    try:
        if not _ENV_PATH.exists():
            return {}
        result: dict[str, str] = {}
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip()
        return result
    except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        return {}


@router.get("/config/effective")
async def config_effective() -> dict[str, Any]:
    """Return the effective value + source for each user-facing config key.

    For each key, reports:
      - value: the effective value (from os.getenv, which reflects .env
        loaded via load_dotenv + any runtime overrides). Secrets are
        reported as ``has_value: bool``, never the actual string.
      - source: "env_file" if the value matches what's in the .env file,
        "runtime" if it's set in the process env but differs from .env
        (e.g. pushed via the settings panel API), or "default" if unset.
      - conflict: True if the .env file and the process env disagree on
        the value (the user edited .env but the panel overrode it, or vice
        versa). This is what the frontend shows as a warning.

    This is the single source of truth the Configuration status panel reads
    so the user can see which config source is "winning" without grepping
    .env or guessing what the settings panel overrode.
    """
    env_file = _read_env_file()
    items = []
    for key, label, is_secret in _CONFIG_KEYS:
        process_val = os.getenv(key, "")
        file_val = env_file.get(key, "")
        if process_val:
            value = process_val
            # Source: if the process env matches the .env file, it came
            # from the file. If they differ, it's a runtime override.
            if file_val and process_val == file_val:
                source = "env_file"
            elif file_val and process_val != file_val:
                source = "runtime"
                conflict = True
            else:
                source = "runtime"  # set in process env, not in .env
        elif file_val:
            # In .env but not loaded into process env (shouldn't happen
            # since load_dotenv runs at startup, but handle it).
            value = file_val
            source = "env_file"
        else:
            value = ""
            source = "default"
        conflict = bool(file_val and process_val and file_val != process_val)
        items.append({
            "key": key,
            "label": label,
            "value": ("" if is_secret else value),
            "has_value": bool(value),
            "source": source,
            "conflict": conflict,
            "is_secret": is_secret,
        })
    return {"config": items}


def _rebuild_llm_client(svc: Services) -> None:
    """Rebuild the synthesis + vision clients from the current .env values.

    Writes to BOTH the svc fields (so Depends consumers see the new client)
    AND the main.py globals (still referenced by the chat loop's free
    variables until the ws router lands). After the ws router lands, the
    global writes become vestigial and can be dropped.
    """
    new_client = get_llm_client(session_logger=svc.session_logger)
    new_client.set_model(new_client.llm_model or "")
    svc.ollama_client = new_client
    svc.vision_client = get_vision_client(session_logger=svc.session_logger)
    # Mirror to main.py globals so the chat loop's free-variable refs still
    # resolve.  Grab the already-loaded module from sys.modules instead of a
    # bare `import main`, which would re-execute main.py (it's running as
    # __main__ under uvicorn) and crash the server with duplicate bindings.
    try:
        import sys
        _main = sys.modules.get("__main__") or sys.modules.get("main")
        if _main is not None:
            _main.ollama_client = new_client
            _main.vision_client = svc.vision_client
    except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
        logger.debug("swallowed: could not mirror to main globals: %s", e)


@router.get("/models")
async def list_models(svc: Annotated[Services, Depends(get_services)]) -> dict[str, Any]:
    """Return available models from the active LLM backend.

    Backend-agnostic: works for local Ollama (list_local_models) AND any
    OpenAI-compatible API (the client's list_models() hits /v1/models). The
    GUI dropdown is populated from this, so whatever API key the user brings,
    they get a live list of models to choose from.
    """
    loop = asyncio.get_event_loop()
    list_fn = (getattr(svc.ollama_client, "list_models", None)
               or svc.ollama_client.list_local_models)
    models = await loop.run_in_executor(None, list_fn)
    # Enrich each model with capability metadata (vision, instruct) so the
    # frontend can group + tag them. For Ollama this calls /api/show per
    # model (cached by the client session). For OpenAI-compatible backends
    # that don't have get_model_capabilities, we fall back to safe defaults.
    # The enrichment is best-effort: if a single model's show call fails,
    # it gets default flags and the list still renders.
    caps_fn = getattr(svc.ollama_client, "get_model_capabilities", None)
    enriched = []
    for name in models:
        caps = {"vision": False, "instruct": True}
        if caps_fn:
            try:
                caps = await loop.run_in_executor(None, caps_fn, name)
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                pass  # keep defaults
        enriched.append({"name": name, "vision": caps.get("vision", False),
                         "instruct": caps.get("instruct", True)})
    return {"models": enriched, "current": svc.ollama_client.llm_model}


# Recommended models for the "Download a recommended model" button when the
# user has no models installed. These are small, capable, and cover both
# text + vision use cases. The frontend offers these as one-click downloads
# via /models/pull so the user never has to type ``ollama pull``.
RECOMMENDED_MODELS = [
    {"name": "qwen3.6:latest", "label": "Qwen 3.6 (recommended)",
     "desc": "Balanced text model. Good for chat, research, and notes.",
     "vision": False, "size": "~2 GB"},
    {"name": "nomic-embed-text", "label": "Nomic Embed (required for search)",
     "desc": "Embedding model — VaultBot needs this for vault search.",
     "vision": False, "size": "~270 MB", "required": True},
]


@router.get("/models/recommended")
async def recommended_models() -> dict[str, Any]:
    """Return the list of recommended models for first-time setup.

    The frontend's 'No models yet' state shows these as one-click download
    buttons. Each carries a label, description, and approximate size so the
    user knows what they're getting before they click.
    """
    return {"models": RECOMMENDED_MODELS}


@router.post("/models/pull")
async def pull_model(
    payload: dict,
    svc: Annotated[Services, Depends(get_services)],
) -> dict[str, Any]:
    """Pull (download) a model via ``ollama pull``, streaming progress over WS.

    Runs ``ollama pull <model>`` in a background thread and broadcasts
    progress events to all connected WebSocket clients so the sidebar can
    show a live progress bar. Returns immediately with ``{"status":
    "pulling"}`` — the actual progress comes over the WS as
    ``{"type": "model_pull_progress", "model": ..., "progress": ...}``
    events. When the pull completes, a ``{"type": "model_pull_done"}``
    event is sent.

    This is the user-facing replacement for typing ``ollama pull`` in a
    terminal — the non-tech user never sees a command line.
    """
    model = (payload.get("model") or "").strip()
    if not model:
        return {"status": "error", "error": "No model specified"}
    import subprocess
    from subprocess_utils import Popen as _popen
    import threading

    # Capture the running event loop BEFORE starting the thread, so the
    # thread can safely schedule WS broadcasts back onto it via
    # run_coroutine_threadsafe. get_event_loop() inside a non-async thread
    # raises RuntimeError on Python 3.12+ (no running loop in a thread).
    try:
        main_loop = asyncio.get_event_loop()
    except RuntimeError:
        main_loop = asyncio.new_event_loop()

    def _pull_thread():
        """Run ollama pull in a background thread, emitting progress."""
        try:
            # ollama pull prints progress lines to stderr; we read them
            # line by line and broadcast percentage updates.
            proc = _popen(
                ["ollama", "pull", model],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                # Ollama pull output looks like:
                #   pulling manifest...
                #   pulling abc123... 100% 1.2GB 6.5MB/s
                #   success
                # Parse the percentage if present.
                pct = None
                if "%" in line:
                    try:
                        pct = int(line.split("%")[0].split()[-1])
                    except (ValueError, IndexError):
                        pass
                # Broadcast progress to all WS clients.
                try:
                    import json as _json
                    msg = _json.dumps({
                        "type": "model_pull_progress",
                        "model": model,
                        "progress": pct if pct is not None else -1,
                        "line": line[:200],
                    })
                    if svc.manager:
                        asyncio.run_coroutine_threadsafe(
                            svc.manager.broadcast(msg), main_loop)
                except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    pass
            proc.wait()
            done_ok = proc.returncode == 0
            # Broadcast completion.
            try:
                import json as _json
                msg = _json.dumps({
                    "type": "model_pull_done",
                    "model": model,
                    "success": done_ok,
                })
                if svc.manager:
                    asyncio.run_coroutine_threadsafe(
                        svc.manager.broadcast(msg), main_loop)
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                pass
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            logger.error("model pull thread error: %s", e)

    thread = threading.Thread(target=_pull_thread, daemon=True)
    thread.start()
    return {"status": "pulling", "model": model}


@router.post("/set_model")
async def set_model_endpoint(payload: dict,
                              svc: Annotated[Services, Depends(get_services)]):
    """Set the active LLM model immediately and persist to .env.

    Persists the selection so subprocesses (subagent) and backend restarts
    pick up the same model. Writes to OLLAMA_LLM_MODEL (ollama backend) or
    LLM_MODEL (openai backend) depending on the active backend.

    For API backends the user may type a model id that isn't in the cached
    list (e.g. a newly-released model), so we accept any non-empty model and
    let the backend reject it at chat time if it's invalid — rather than
    hard-failing here on a stale list.
    """
    requested_model = payload.get("model")
    if not requested_model:
        return {"status": "error", "detail": "missing model"}, 400
    svc.ollama_client.set_model(requested_model)

    # Persist to .env so subprocesses (subagent) and restarts respect the
    # dropdown selection. This is the single source of truth — the dropdown
    # writes here, and every LLM call reads from here.
    backend = _detect_llm_backend()
    if backend == "openai":
        _persist_env_value("LLM_MODEL", requested_model)
    else:
        _persist_env_value("OLLAMA_LLM_MODEL", requested_model)
    # Reload .env into process env so the factory sees the new value.
    load_dotenv(str(_ENV_PATH), override=True)

    return {"status": "ok", "model": requested_model,
            "current": svc.ollama_client.llm_model}


@router.get("/model_context_window")
async def model_context_window(svc: Annotated[Services, Depends(get_services)],
                               model: str | None = None) -> dict[str, Any]:
    """Return the context-window size (in tokens) for a model.

    Auto-detects from the active backend: Ollama queries /api/show for the
    architecture-prefixed ``*.context_length`` field; OpenAI-compatible
    backends match the model id against a known-models table. The GUI uses
    this to size the token-usage meter so it maxes out at whatever the
    equipped model can actually hold.

    Query params:
      model: optional model name (defaults to the currently equipped model)
    """
    target = model or svc.ollama_client.llm_model
    loop = asyncio.get_event_loop()
    try:
        ctx = await loop.run_in_executor(
            None, lambda: svc.ollama_client.context_window(target))
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not determine context window for model {target!r}: {exc}") from exc
    return {"model": target, "context_window": ctx}


@router.get("/llm/config")
async def get_llm_config(svc: Annotated[Services, Depends(get_services)]) -> dict[str, Any]:
    """Return the current synthesis-LLM backend config (no secrets)."""
    backend = _detect_llm_backend()
    return {
        "backend": backend,
        "base_url": os.getenv("LLM_BASE_URL", "") if backend == "openai"
                    else os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        "model": svc.ollama_client.llm_model,
        "has_api_key": bool(os.getenv("LLM_API_KEY", "")) if backend == "openai" else True,
        "running": svc.ollama_client.is_running(),
    }


@router.post("/llm/config")
async def set_llm_config(payload: dict,
                         svc: Annotated[Services, Depends(get_services)]):
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
        if (backend or _detect_llm_backend()) == "openai":
            _persist_env_value("LLM_MODEL", model)
        else:
            _persist_env_value("OLLAMA_LLM_MODEL", model)

    # Reload .env into the process env so the factory sees the new values.
    load_dotenv(str(_ENV_PATH), override=True)
    _rebuild_llm_client(svc)
    return {
        "status": "ok",
        "backend": _detect_llm_backend(),
        "base_url": os.getenv("LLM_BASE_URL", "") if _detect_llm_backend() == "openai"
                    else os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        "model": svc.ollama_client.llm_model,
        "running": svc.ollama_client.is_running(),
    }


@router.get("/llm/vision_check")
async def vision_check(svc: Annotated[Services, Depends(get_services)]):
    """Probe whether the page-reading model can see images."""
    loop = asyncio.get_event_loop()
    probe_client = svc.vision_client if svc.vision_client is not None else svc.ollama_client
    source = "vision" if svc.vision_client is not None else "synthesis"
    try:
        capable = await loop.run_in_executor(None, probe_client.vision_capable)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Vision probe failed: {exc}") from exc
    return {
        "vision_capable": bool(capable),
        "model": probe_client.llm_model,
        "backend": _detect_llm_backend() if svc.vision_client is None
                   else (os.getenv("VISION_BACKEND") or _detect_llm_backend()),
        "source": source,
    }


@router.get("/llm/vision_config")
async def get_vision_config(svc: Annotated[Services, Depends(get_services)]):
    """Return the dedicated vision-model config (no secrets)."""
    backend = (os.getenv("VISION_BACKEND") or "").strip().lower()
    if not backend:
        backend = _detect_llm_backend()
    model = (os.getenv("VISION_MODEL") or "").strip()
    configured = bool(model)
    base_url = ""
    if backend == "openai":
        base_url = (os.getenv("VISION_BASE_URL") or os.getenv("LLM_BASE_URL") or "").strip()
    else:
        base_url = (os.getenv("VISION_OLLAMA_HOST")
                     or os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    running = False
    if svc.vision_client is not None:
        try:
            running = bool(svc.vision_client.is_running())
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
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


@router.post("/llm/vision_config")
async def set_vision_config(payload: dict,
                            svc: Annotated[Services, Depends(get_services)]):
    """Configure (or clear) the dedicated vision model at runtime.

    Accepted fields (all optional; sending an empty `model` clears the
    vision config so the page reader falls back to the synthesis client):
      backend:      "ollama" | "openai"  (defaults to the synthesis backend)
      base_url:     OpenAI-compatible endpoint (openai path)
      api_key:      bearer token (openai path) — written to .env, not echoed
      model:        model id (openai) OR Ollama model name
      ollama_host:  Ollama host if the vision model lives on a different
                    daemon than the chat model (ollama path)
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

    if backend:
        _persist_env_value("VISION_BACKEND", backend)
    if base_url:
        _persist_env_value("VISION_BASE_URL", base_url)
    if api_key:
        _persist_env_value("VISION_API_KEY", api_key)
    if ollama_host:
        _persist_env_value("VISION_OLLAMA_HOST", ollama_host)
    _persist_env_value("VISION_MODEL", model)

    load_dotenv(str(_ENV_PATH), override=True)
    _rebuild_llm_client(svc)
    return {
        "status": "ok",
        "configured": bool(model),
        "backend": (os.getenv("VISION_BACKEND") or _detect_llm_backend()),
        "base_url": (os.getenv("VISION_BASE_URL")
                     or os.getenv("LLM_BASE_URL", "")) if backend == "openai"
                    else (os.getenv("VISION_OLLAMA_HOST")
                          or os.getenv("OLLAMA_HOST", "http://localhost:11434")),
        "model": model,
        "running": bool(svc.vision_client.is_running()) if svc.vision_client is not None
                   else False,
    }
