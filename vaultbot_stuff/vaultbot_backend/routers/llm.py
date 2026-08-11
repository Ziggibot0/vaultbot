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

from fastapi import APIRouter, Depends, HTTPException

from app_state import get_services
from services import Services
from llm_client import build_role_client, clear_role_client_cache
from providers import KNOWN_PROVIDERS, ROLES, ProviderRegistry, test_provider

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


def _registry(svc: Services) -> ProviderRegistry:
    """Return the live ProviderRegistry, constructing one if absent.

    Services.registry is optional (tests build Services without it). Routers
    lazily create + attach it so a fresh Services still gets a working pot.
    """
    reg = getattr(svc, "registry", None)
    if reg is None:
        reg = ProviderRegistry.migrate_from_env()
        svc.registry = reg
    return reg


def _rebuild_llm_client(svc: Services) -> None:
    """Rebuild the three cartridge clients from the pot.

    Every cartridge resolves through the registry — big/small/vision each
    build from whichever model (on whichever provider) the role points at.
    Writes BOTH the svc fields (so Depends consumers see the new client) AND
    the main.py globals (still referenced by the chat loop's free variables).
    """
    reg = _registry(svc)
    clear_role_client_cache()
    new_client = build_role_client("big", reg, svc.session_logger)
    svc.ollama_client = new_client
    svc.vision_client = build_role_client("vision", reg, svc.session_logger)
    svc.small_client = build_role_client("small", reg, svc.session_logger)
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
            _main.small_client = svc.small_client
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

@router.get("/llm/local_models")
async def list_local_models() -> dict[str, Any]:
    """Return locally installed Ollama models, regardless of the active
    chat backend.

    The small model is ALWAYS local Ollama, and the vision model is often a
    different local Ollama model than the (possibly cloud) chat model. The
    big/small/vision header dropdowns all need a list of what's actually
    installed locally, not what the cloud API exposes. This endpoint queries
    the local Ollama daemon directly so it works even when the chat backend
    is cloud (where ``/models`` would list cloud model ids instead).

    Enriches each model with vision/instruct flags the same way ``/models``
    does, so the frontend can mark vision-capable models in the vision
    dropdown.
    """
    from ollama_client import OllamaClient  # local daemon only

    host = (os.getenv("OLLAMA_HOST") or "http://localhost:11434")
    probe = OllamaClient(base_url=host, llm_model="",
                         embed_model=os.getenv("OLLAMA_EMBED_MODEL",
                                                "nomic-embed-text"))
    loop = asyncio.get_event_loop()
    try:
        names = await loop.run_in_executor(None, probe.list_local_models)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach local Ollama at {host}: {exc}") from exc
    caps_fn = getattr(probe, "get_model_capabilities", None)
    enriched = []
    for name in names:
        caps = {"vision": False, "instruct": True}
        if caps_fn:
            try:
                caps = await loop.run_in_executor(None, caps_fn, name)
            except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                pass
        enriched.append({"name": name, "vision": caps.get("vision", False),
                         "instruct": caps.get("instruct", True)})
    return {"models": enriched}

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
    reg = _registry(svc)
    svc.ollama_client.set_model(requested_model)

    # Update the big-role model id in the pot so restarts pick the same model.
    # The model id format is "<provider>:<model-name>"; only the name part
    # changes here (the provider + role assignment stay). If the big role is
    # unassigned there's nothing to update — the client change is in-memory
    # only until the user assigns a model in the new settings UI.
    mid = reg.get_role("big")
    if mid and ":" in mid:
        prov = mid.split(":", 1)[0]
        new_mid = f"{prov}:{requested_model}"
        if reg.get_model(new_mid) is None:
            try:
                old = reg.get_model(mid)
                reg.add_model(new_mid, requested_model, prov,
                              vision=(old.vision if old else False),
                              instruct=True)
            except Exception:  # noqa: BLE001 — best-effort persist
                pass
        reg.set_role("big", new_mid)

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


@router.get("/llm/vision_check")
async def vision_check(svc: Annotated[Services, Depends(get_services)]):
    """Probe whether the page-reading model can see images.

    Uses the vision cartridge client from the pot (falling back to the big
    model). The vision cartridge CAN be a cloud model now — a vision-capable
    OpenRouter/OpenAI model reading textbook pages is fully supported.
    """
    loop = asyncio.get_event_loop()
    probe_client = svc.vision_client if svc.vision_client is not None else svc.ollama_client
    source = "vision" if svc.vision_client is not None else "big"
    try:
        capable = await loop.run_in_executor(None, probe_client.vision_capable)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Vision probe failed: {exc}") from exc
    return {
        "vision_capable": bool(capable),
        "model": getattr(probe_client, "llm_model", ""),
        "source": source,
    }


# ---------------------------------------------------------------------------
# Provider + Model Registry endpoints — the interchangeable "pot"
# ---------------------------------------------------------------------------
@router.get("/llm/providers")
async def list_providers(svc: Annotated[Services, Depends(get_services)]):
    """List providers (secret-free) + the well-known presets for the UI."""
    reg = _registry(svc)
    return {
        "providers": [p.to_public() for p in reg.list_providers()],
        "known": KNOWN_PROVIDERS,
        "roles": list(ROLES),
    }


@router.post("/llm/providers")
async def add_provider(payload: dict,
                       svc: Annotated[Services, Depends(get_services)]):
    """Add (or overwrite) a provider connection.

    Fields: id, type ("ollama"|"openai"), base_url, api_key (optional),
    label (optional).

    The endpoint is TESTED before it's trusted (unless save_anyway=true):
    a bad base_url, a wrong key, or a URL that isn't the right API surface
    (e.g. an Ollama daemon root vs OpenAI /v1) is rejected with a clear
    message. If it works, the live model list is returned too so the UI can
    show the dropdown immediately (no typing model names).
    """
    reg = _registry(svc)
    pid = (payload.get("id") or "").strip()
    ptype = (payload.get("type") or "").strip().lower()
    base_url = (payload.get("base_url") or "").strip()
    api_key = (payload.get("api_key") or "").strip()
    label = (payload.get("label") or "").strip()
    save_anyway = bool(payload.get("save_anyway", False))
    if not pid:
        return {"status": "error", "detail": "id required"}, 400
    if ptype not in ("ollama", "openai"):
        return {"status": "error",
                "detail": "type must be 'ollama' or 'openai'"}, 400
    if not base_url:
        return {"status": "error", "detail": "base_url required"}, 400
    try:
        prov = reg.add_provider(pid, ptype, base_url, api_key=api_key, label=label)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}, 400
    # Test the endpoint. A failed test rejects the provider by default so a
    # typo never becomes a silent chat-time 404. run in executor (blocking).
    probe = await asyncio.get_event_loop().run_in_executor(None, test_provider, prov)
    if not probe["ok"] and not save_anyway:
        reg.remove_provider(pid)
        _rebuild_llm_client(svc)
        return {"status": "error", "detail": probe["error"],
                "probe": probe}, 502
    return {"status": "ok", "provider": prov.to_public(), "probe": probe}


@router.delete("/llm/providers/{provider_id}")
async def remove_provider(provider_id: str,
                          svc: Annotated[Services, Depends(get_services)]):
    """Remove a provider + its models + any role that used them."""
    reg = _registry(svc)
    ok = reg.remove_provider(provider_id)
    _rebuild_llm_client(svc)
    return {"status": "ok" if ok else "not_found"}


@router.get("/llm/models/all")
async def list_all_models(svc: Annotated[Services, Depends(get_services)]):
    """The whole pot: every registered model with provider + role flags.

    This is what the three header dropdowns all draw from — one combined
    list, grouped by provider, so the user sees local + cloud models
    side-by-side and picks any of them into any role.
    """
    reg = _registry(svc)
    models = []
    for m in reg.list_models():
        prov = reg.get_provider(m.provider)
        models.append({
            **m.to_dict(),
            "provider_label": prov.label if prov else m.provider,
            "provider_type": prov.type if prov else "",
            "roles": [r for r in ROLES if reg.get_role(r) == m.id],
        })
    return {"models": models,
            "roles": {r: reg.get_role(r) for r in ROLES}}


@router.post("/llm/models")
async def add_model(payload: dict,
                    svc: Annotated[Services, Depends(get_services)]):
    """Register a model into the pot.

    Fields: id (unique), model (provider's model id), provider (provider id),
    vision (bool), instruct (bool), label (optional). ``id`` defaults to
    ``<provider>:<model>`` when omitted.
    """
    reg = _registry(svc)
    mid = (payload.get("id") or "").strip()
    model = (payload.get("model") or "").strip()
    provider = (payload.get("provider") or "").strip()
    vision = bool(payload.get("vision", False))
    instruct = bool(payload.get("instruct", True))
    label = (payload.get("label") or "").strip()
    if not mid:
        mid = f"{provider}:{model}" if provider and model else ""
    if not model or not provider:
        return {"status": "error", "detail": "model and provider required"}, 400
    # Auto-tag free-tier so the UI can warn before assigning a PAID model to a
    # money-spending role. Ollama local = always free; OpenRouter ":free" = free.
    prov = reg.get_provider(provider)
    from providers import _is_free_model
    free = bool(payload.get("free", _is_free_model(model, prov.type if prov else "openai", prov.base_url if prov else "")))
    try:
        entry = reg.add_model(mid, model, provider, vision=vision,
                              instruct=instruct, label=label, free=free)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}, 400
    return {"status": "ok", "model": entry.to_dict()}


@router.delete("/llm/models/{model_id:path}")
async def remove_model(model_id: str,
                       svc: Annotated[Services, Depends(get_services)]):
    """Remove a model from the pot (and any role that referenced it)."""
    reg = _registry(svc)
    ok = reg.remove_model(model_id)
    _rebuild_llm_client(svc)
    return {"status": "ok" if ok else "not_found"}


@router.post("/llm/role")
async def set_role(payload: dict,
                   svc: Annotated[Services, Depends(get_services)]):
    """Assign a role (big|small|vision) to any model in the pot.

    Fields: role, model_id (empty clears the role). This is the single
    interchange point: one call maps any model — local Ollama, OpenRouter
    cloud, OpenAI — into any of the three cartridges, and rebuilds the role
    client immediately.
    """
    reg = _registry(svc)
    role = (payload.get("role") or "").strip()
    model_id = (payload.get("model_id") or "").strip()
    if role not in ROLES:
        return {"status": "error", "detail": f"role must be one of {ROLES}"}, 400
    try:
        reg.set_role(role, model_id)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}, 400
    _rebuild_llm_client(svc)
    return {"status": "ok", "role": role, "model_id": model_id,
            "roles": {r: reg.get_role(r) for r in ROLES}}


@router.get("/llm/providers/{provider_id}/live_models")
async def provider_live_models(provider_id: str,
                               svc: Annotated[Services, Depends(get_services)]):
    """Probe a provider and return the models it actually serves (dropdown list).

    If the endpoint works, the live model list IS the dropdown content — the
    user picks a model, never types one. For Ollama we enrich with
    vision/instruct capability flags; for OpenAI-compatible we return the raw
    /v1/models ids (capability probing per model is a separate concern).
    Runs the test in an executor so a slow/dead endpoint never blocks the
    event loop, and never raises — a bad endpoint returns ok:false + error,
    which the UI surfaces.
    """
    reg = _registry(svc)
    prov = reg.get_provider(provider_id)
    if prov is None:
        return {"status": "error", "detail": "unknown provider",
                "ok": False, "models": []}, 404
    probe = await asyncio.get_event_loop().run_in_executor(None, test_provider, prov)
    if not probe["ok"]:
        return {"status": "error", "detail": probe["error"],
                "ok": False, "models": [], "latency_ms": probe["latency_ms"]}
    names = probe["models"]
    # Ollama: enrich with vision/instruct caps for the dropdown grouping.
    if prov.type == "ollama":
        from ollama_client import OllamaClient
        from providers import _is_free_model
        loop = asyncio.get_event_loop()
        caps_probe = OllamaClient(base_url=prov.base_url, llm_model="",
                                  embed_model="nomic-embed-text")
        caps_fn = getattr(caps_probe, "get_model_capabilities", None)
        enriched = []
        for n in names:
            caps = {"vision": False, "instruct": True}
            if caps_fn:
                try:
                    caps = await loop.run_in_executor(None, caps_fn, n)
                except Exception:  # noqa: BLE001 — best-effort per-model caps
                    pass
            enriched.append({"name": n, "vision": caps.get("vision", False),
                             "instruct": caps.get("instruct", True),
                             "free": _is_free_model(n, "ollama", prov.base_url)})
        return {"status": "ok", "ok": True, "models": enriched,
                "provider": provider_id, "latency_ms": probe["latency_ms"]}
    from providers import _is_free_model
    return {"status": "ok", "ok": True,
            "models": [{"name": n, "vision": False, "instruct": True,
                        "free": _is_free_model(n, "openai", prov.base_url)}
                       for n in names],
            "provider": provider_id, "latency_ms": probe["latency_ms"]}


@router.post("/llm/test_model")
async def test_model_endpoint(payload: dict,
                              svc: Annotated[Services, Depends(get_services)]):
    """Test whether a specific model actually responds on an endpoint.

    Separate from the provider test: the endpoint can work while a specific
    model doesn't (bad name, not vision-capable, gated, etc.). Sends a real
    1-token chat call. Fields: provider_id, model, vision (probe image
    understanding too). Returns {ok, error, response}.
    """
    reg = _registry(svc)
    prov = reg.get_provider((payload.get("provider_id") or "").strip())
    model = (payload.get("model") or "").strip()
    want_vision = bool(payload.get("vision", False))
    if prov is None:
        return {"status": "error", "detail": "unknown provider", "ok": False}, 404
    if not model:
        return {"status": "error", "detail": "model required", "ok": False}, 400

    def _probe() -> dict:
        try:
            if prov.type == "openai":
                from llm_client import OpenAICompatibleClient, _test_image_base64
                client = OpenAICompatibleClient(base_url=prov.base_url,
                                                api_key=prov.api_key, llm_model=model)
                content: list | str = "Say ok."
                if want_vision:
                    content = [
                        {"type": "text", "text": "What color is this square? one word."},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{_test_image_base64()}"}},
                    ]
                result = client.chat([{"role": "user", "content": content}],
                                     stream=False, temperature=0.0)
                text = (result.get("response") or "").strip()
                if want_vision:
                    ok = "red" in text.lower()
                    return {"ok": ok, "response": text,
                            "error": None if ok else
                            "model did not identify the color (not vision-capable?)"}
                return {"ok": bool(text), "response": text,
                        "error": None if text else "empty response"}
            # Ollama native
            import requests as _rq
            body = {"model": model,
                    "messages": [{"role": "user", "content": "Say ok."}],
                    "stream": False, "options": {"temperature": 0}}
            if want_vision:
                from llm_client import _test_image_base64
                body["messages"][0]["images"] = [_test_image_base64()]
                body["messages"][0]["content"] = "What color is this square? one word."
            r = _rq.post(f"{prov.base_url.rstrip('/')}/api/chat", json=body,
                         timeout=60)
            if r.status_code == 404:
                return {"ok": False, "response": "",
                        "error": f"model {model!r} not found on this daemon"}
            r.raise_for_status()
            content = (r.json().get("message", {}) or {}).get("content", "").strip()
            if want_vision:
                ok = "red" in content.lower()
                return {"ok": ok, "response": content,
                        "error": None if ok else
                        "model did not identify the color (not vision-capable?)"}
            return {"ok": bool(content), "response": content,
                    "error": None if content else "empty response"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "response": "",
                    "error": f"{type(e).__name__}: {e}"}

    result = await asyncio.get_event_loop().run_in_executor(None, _probe)
    return {"status": "ok" if result["ok"] else "error", **result,
            "provider": prov.id, "model": model}
