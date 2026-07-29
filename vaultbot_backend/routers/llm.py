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
from fastapi import APIRouter, Depends

from app_state import get_services
from services import Services
from llm_client import get_llm_client, get_vision_client

router = APIRouter()
logger = logging.getLogger(__name__)

# .env path — re-derived here so the router doesn't import main.
_ENV_PATH = Path(__file__).with_name("..") / ".env"


def _persist_env_value(key: str, value: str) -> None:
    """Write/update a KEY=VALUE line in the vault root .env file."""
    try:
        lines = (_ENV_PATH.read_text(encoding="utf-8").splitlines()
                 if _ENV_PATH.exists() else [])
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
        _ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"VaultBot: could not persist {key} to .env: {e}")


def _detect_llm_backend() -> str:
    """Read the effective backend name for /llm/config reporting."""
    backend = (os.getenv("LLM_BACKEND") or "").strip().lower()
    if backend == "openai":
        return "openai"
    return "ollama"


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
    # resolve.  Imported lazily to avoid a cycle at module load.
    try:
        import main as _main
        _main.ollama_client = new_client
        _main.vision_client = svc.vision_client
    except Exception as e:
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
    return {"models": models, "current": svc.ollama_client.llm_model}


@router.post("/set_model")
async def set_model_endpoint(payload: dict,
                              svc: Annotated[Services, Depends(get_services)]):
    """Set the active LLM model immediately.

    For API backends the user may type a model id that isn't in the cached
    list (e.g. a newly-released model), so we accept any non-empty model and
    let the backend reject it at chat time if it's invalid — rather than
    hard-failing here on a stale list.
    """
    requested_model = payload.get("model")
    if not requested_model:
        return {"status": "error", "detail": "missing model"}, 400
    svc.ollama_client.set_model(requested_model)
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
    ctx = await loop.run_in_executor(
        None, lambda: svc.ollama_client.context_window(target))
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
    capable = await loop.run_in_executor(None, probe_client.vision_capable)
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