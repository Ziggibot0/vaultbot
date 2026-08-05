"""
Provider + Model Registry — the single "pot" of LLM connections and models.

Why
---
Previously VaultBot scattered LLM config across many .env keys and three
separate code paths (big via ``get_llm_client``, vision via
``get_vision_client``, small local-only via ``get_small_client``). Adding a
new provider meant new env vars, new factory branches, and a new code path —
and the three roles were NOT interchangeable (the small model was forced to
local Ollama even if the user wanted it on a cheap cloud model).

This module replaces that with ONE registry:

  - **Providers** — named connections to an LLM source. Each provider is a
    ``{id, type, base_url, api_key}`` tuple. Types: ``"ollama"`` (local or
    Ollama-cloud) and ``"openai"`` (any OpenAI-compatible endpoint: OpenAI,
    OpenRouter, Gemini proxy, vLLM, LM Studio). Defined ONCE, reused by any
    number of models.
  - **Models** — entries that point at a provider by id + carry a model id
    and capability flags (``vision``). This is the pot. Every model the user
    has added — local Ollama models, OpenRouter cloud models, OpenAI models —
    lives in this single list, regardless of which provider serves it.
  - **Roles** — the three cartridges (``big`` / ``small`` / ``vision``). A role
    is just an assignment that names a model in the pot. All three roles draw
    from the SAME pot, so a user can map Ollama→big, OpenRouter→vision,
    OpenAI→small, all through the same picker UI and the same factory.

The registry is persisted to ``providers.json`` at the vault root (next to
``.env``). Secrets (api keys) live in that file — which is gitignored exactly
like ``.env``. On first run with no ``providers.json``, the registry
auto-migrates from the legacy ``.env`` values so existing installs keep
working with zero manual steps.

Contract
--------
``ProviderRegistry`` is a small dataclass-collection with:

  - ``list_providers() -> list[Provider]``
  - ``list_models() -> list[ModelEntry]``        (the pot)
  - ``get_model(model_id) -> ModelEntry | None``
  - ``add_provider(...)`` / ``remove_provider(id)``
  - ``add_model(...)`` / ``remove_model(id)``
  - ``get_role(role) -> str | None``             (model id assigned to role)
  - ``set_role(role, model_id)``                 (role <- any model in pot)
  - ``models_for_role(role) -> list[ModelEntry]``(pot, flagged for the role)
  - ``save()`` / ``load()``

It never performs network I/O. Live model *listing* (what a provider actually
has installed) belongs to ``llm_client.get_provider_client``; the registry
only stores what the user has declared.

Base URLs are NORMALIZED on add: a trailing ``/v1`` (or ``/v1/``) is stripped
and the type decides the real API surface at call time:
  - ``ollama`` providers talk the native Ollama API (``/api/chat``,
    ``/api/tags``). Their base_url must be the daemon ROOT (no ``/v1``).
  - ``openai`` providers talk the OpenAI-compatible API. ``llm_client`` appends
    ``/v1/chat/completions`` / ``/v1/models`` itself, so the stored base must
    NOT carry ``/v1`` or it would double up to ``/v1/v1/...`` (the exact 404
    Sean hit). Normalizing here makes `changed ollama endpoint to /v1` a
    no-op instead of a break.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

# The registry file lives at the vault root, one level above
# vaultbot_backend/. Resolved via __file__ so it tracks the real install
# location regardless of cwd. providers.json holds api keys, so it must be
# gitignored exactly like .env (it is — see .gitignore).
_REGISTRY_PATH = Path(__file__).resolve().parent.parent.parent / "providers.json"

# The three role cartridges. A role is just a name; the registry maps each to
# a model id drawn from the pot. Adding a new role = append to this tuple.
ROLES: tuple[str, ...] = ("big", "small", "vision")

# Valid provider types. "ollama" covers local Ollama AND Ollama-cloud (same
# /api/* surface, just a remote host). "openai" covers any OpenAI-compatible
# /v1/chat/completions endpoint.
PROVIDER_TYPES: tuple[str, ...] = ("ollama", "openai")

# Well-known base URLs so the UI can offer one-click provider presets.
# The user can always type a custom base_url — these are just conveniences.
KNOWN_PROVIDERS: dict[str, dict[str, str]] = {
    "ollama-local": {"type": "ollama", "base_url": "http://localhost:11434",
                     "label": "Ollama (this machine)"},
    "ollama-cloud": {"type": "ollama", "base_url": "https://ollama.com",
                     "label": "Ollama Cloud"},
    "openai": {"type": "openai", "base_url": "https://api.openai.com",
               "label": "OpenAI"},
    "openrouter": {"type": "openai", "base_url": "https://openrouter.ai/api",
                   "label": "OpenRouter"},
    "gemini": {"type": "openai",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "label": "Google Gemini"},
    "groq": {"type": "openai", "base_url": "https://api.groq.com/openai",
             "label": "Groq"},
    "lm-studio": {"type": "openai", "base_url": "http://localhost:1234",
                  "label": "LM Studio (local)"},
}


@dataclass
class Provider:
    """A named connection to an LLM source.

    ``api_key`` is empty for Ollama (local daemons don't need one). For
    OpenAI-compatible providers it is the bearer token. Stored in
    providers.json (gitignored).
    """
    id: str
    type: str                # "ollama" | "openai"
    base_url: str
    api_key: str = ""
    label: str = ""          # human-facing name for the UI

    def to_public(self) -> dict[str, Any]:
        """Secret-free view for the settings UI (has_key bool instead of key)."""
        return {
            "id": self.id,
            "type": self.type,
            "base_url": self.base_url,
            "label": self.label or self.id,
            "has_key": bool(self.api_key),
        }


_V1_SUFFIXES = ("/v1", "/v1/", "/v1beta", "/openai/v1")


def normalize_base_url(url: str, type_: str) -> str:
    """Normalize a provider base_url so call-time path joining is unambiguous.

    Strips a trailing slash and any OpenAI-style ``/v1`` segment:
      - "openai" providers: the client appends ``/v1/...`` at call time, so the
        stored base must not carry it (or you'd get /v1/v1/chat/completions).
      - "ollama" providers: uses the native /api/* surface; a stray /v1 is
        meaningless and stripped too.
    Raises ValueError on an empty result so a typo never becomes a silent 404.
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        raise ValueError("base_url required")
    low = u.lower()
    for suf in sorted(_V1_SUFFIXES, key=len, reverse=True):
        if low.endswith(suf):
            u = u[: -len(suf)].rstrip("/")   # strip once
            break
    if not u:
        raise ValueError(f"base_url {url!r} reduced to nothing after normalization")
    return u


def test_provider(prov: "Provider", timeout: float = 8.0) -> dict[str, Any]:
    """Probe whether a provider endpoint is reachable and lists models.

    Returns a dict: {"ok": bool, "models": [str], "count": int,
    "latency_ms": float, "error": str|None}. Never raises — the UI shows
    success/failure so a bad base_url or dead key is caught BEFORE the user
    tries (and fails) to chat with a model from it. This is the
    'test the endpoint' path Sean asked for: if it works, you ALSO get the
    live model list for the dropdown.
    """
    base = prov.base_url.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if prov.api_key:
        headers["Authorization"] = f"Bearer {prov.api_key}"
    t0 = time.time()
    try:
        if prov.type == "openai":
            r = requests.get(f"{base}/v1/models", headers=headers,
                             timeout=timeout)
            if r.status_code in (401, 403):
                return _probe_fail(t0, "auth rejected (check the API key)")
            if r.status_code == 404:
                return _probe_fail(t0, "no /v1/models — is this really an OpenAI-compatible endpoint?")
            r.raise_for_status()
            data = r.json().get("data", [])
            names = [m.get("id", "") for m in data if m.get("id")]
            return _probe_ok(t0, names)
        # ollama native
        r = requests.get(f"{base}/api/tags", headers=headers, timeout=timeout)
        if r.status_code == 404:
            return _probe_fail(t0, "no /api/tags — is an Ollama daemon at this URL? (strip any /v1)")
        r.raise_for_status()
        models = r.json().get("models", [])
        names = [m.get("name", "") for m in models if m.get("name")]
        return _probe_ok(t0, names)
    except requests.exceptions.ConnectionError:
        return _probe_fail(t0, f"cannot connect to {base}")
    except requests.exceptions.Timeout:
        return _probe_fail(t0, f"timed out after {timeout:.0f}s")
    except Exception as e:  # noqa: BLE001
        return _probe_fail(t0, f"{type(e).__name__}: {e}")


def _probe_ok(t0: float, models: list[str]) -> dict[str, Any]:
    return {"ok": True, "models": models, "count": len(models),
            "latency_ms": round((time.time() - t0) * 1000, 1), "error": None}


def _probe_fail(t0: float, error: str) -> dict[str, Any]:
    return {"ok": False, "models": [], "count": 0,
            "latency_ms": round((time.time() - t0) * 1000, 1), "error": error}


@dataclass
class ModelEntry:
    """One model in the pot.

    ``provider`` is the id of the serving Provider. ``vision`` marks the
    model as image-capable (used to filter / flag it for the vision role).
    ``instruct`` marks it as a chat/instruct model (vs an embedding model);
    embedding models are kept OUT of the role pickers. ``free`` marks a
    model as free-tier (OpenRouter ":free" suffix, Ollama local) so the UI can
    warn before assigning a PAID model to a money-spending role.
    """
    id: str                  # unique registry id, e.g. "openrouter:qwen/qwen-2.5-vl"
    model: str               # the provider's model id, e.g. "qwen/qwen-2.5-vl"
    provider: str            # provider id
    vision: bool = False
    instruct: bool = True
    free: bool = False
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_free_model(model_name: str, provider_type: str) -> bool:
    """Best-effort free-tier detection so the UI can flag paid models.

    - Ollama (local daemon): always free (runs on your own hardware).
    - OpenRouter: model ids ending in ":free" are free-tier.
    - Other OpenAI-compatible: assume paid unless we can tell otherwise.
    This is a HEURISTIC, not authoritative — OpenRouter's /v1/models does not
    expose pricing. The flag is advisory; the UI warns, it doesn't block.
    """
    if provider_type == "ollama":
        return True
    return model_name.lower().endswith(":free")


class ProviderRegistry:
    """Thread-safe, file-backed registry of providers + models + roles.

    A single instance lives on Services. Mutations are persisted immediately
    to ``providers.json`` so they survive restart, matching how the legacy
    endpoints persisted to ``.env``.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _REGISTRY_PATH
        self._lock = threading.RLock()
        self._providers: dict[str, Provider] = {}
        self._models: dict[str, ModelEntry] = {}
        self._roles: dict[str, str] = {}        # role -> model id
        self.load()
        # load() heals any stale base_urls (strips stray /v1) in memory; persist
        # the heal so the file on disk matches and the next boot is a no-op.
        if self._providers:
            self.save()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def load(self) -> None:
        """Load from providers.json. Missing/corrupt file -> empty registry.

        Also RE-NORMALIZES every provider base_url on load so a stale
        providers.json written before normalize_base_url existed (or one a
        user hand-edited with a /v1 suffix) is healed on the next boot. The
        heal is persisted on the next save() so it sticks.
        """
        with self._lock:
            try:
                if not self._path.exists():
                    return
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for p in data.get("providers", []):
                    prov = Provider(**p)
                    # Heal any stray /v1 (or trailing slash) so call-time path
                    # joining is unambiguous (Sean's /v1 Ollama case).
                    try:
                        prov.base_url = normalize_base_url(prov.base_url, prov.type)
                    except ValueError:
                        pass  # keep as-is if normalization fails; add_provider rejects bad URLs going forward
                    self._providers[prov.id] = prov
                for m in data.get("models", []):
                    entry = ModelEntry(**m)
                    # Re-tag the `free` flag using the provider-type heuristic
                    # so entries written before the flag existed (or saved with
                    # a stale False) are corrected on load. This is a heal,
                    # persisted on the next save() (the __init__ save-on-load).
                    prov = self._providers.get(entry.provider)
                    entry.free = _is_free_model(entry.model,
                                                 prov.type if prov else "openai")
                    self._models[entry.id] = entry
                for role, mid in (data.get("roles") or {}).items():
                    if role in ROLES and isinstance(mid, str):
                        self._roles[role] = mid
            except Exception as e:  # noqa: BLE001 — a broken registry must never crash boot
                print(f"[WARN] ProviderRegistry.load failed ({e}); starting empty.")

    def save(self) -> None:
        """Persist to providers.json (atomic: temp + replace)."""
        with self._lock:
            data = {
                "providers": [asdict(p) for p in self._providers.values()],
                "models": [m.to_dict() for m in self._models.values()],
                "roles": dict(self._roles),
            }
            tmp = self._path.with_suffix(".json.tmp")
            try:
                tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                os.replace(tmp, self._path)
            except Exception as e:  # noqa: BLE001 — best-effort persist; surfacing is the endpoint's job
                print(f"[WARN] ProviderRegistry.save failed: {e}")

    # ------------------------------------------------------------------
    # providers
    # ------------------------------------------------------------------
    def list_providers(self) -> list[Provider]:
        with self._lock:
            return list(self._providers.values())

    def get_provider(self, provider_id: str) -> Provider | None:
        with self._lock:
            return self._providers.get(provider_id)

    def add_provider(self, provider_id: str, type_: str, base_url: str,
                     api_key: str = "", label: str = "") -> Provider:
        if type_ not in PROVIDER_TYPES:
            raise ValueError(f"type must be one of {PROVIDER_TYPES}")
        base_url = normalize_base_url(base_url, type_)   # strips /v1 + trailing /
        with self._lock:
            prov = Provider(id=provider_id, type=type_, base_url=base_url,
                            api_key=api_key, label=label)
            self._providers[provider_id] = prov
            self.save()
            return prov

    def remove_provider(self, provider_id: str) -> bool:
        with self._lock:
            if provider_id not in self._providers:
                return False
            del self._providers[provider_id]
            # Drop models that pointed at the removed provider, and any role
            # that referenced those models.
            dead_models = [mid for mid, m in self._models.items()
                           if m.provider == provider_id]
            for mid in dead_models:
                del self._models[mid]
            for role, mid in list(self._roles.items()):
                if mid in dead_models or mid not in self._models:
                    del self._roles[role]
            self.save()
            return True

    # ------------------------------------------------------------------
    # models (the pot)
    # ------------------------------------------------------------------
    def list_models(self) -> list[ModelEntry]:
        with self._lock:
            return list(self._models.values())

    def get_model(self, model_id: str) -> ModelEntry | None:
        with self._lock:
            return self._models.get(model_id)

    def add_model(self, model_id: str, model: str, provider_id: str,
                  vision: bool = False, instruct: bool = True,
                  free: bool = False, label: str = "") -> ModelEntry:
        if provider_id not in self._providers:
            raise ValueError(f"unknown provider {provider_id!r}")
        with self._lock:
            entry = ModelEntry(id=model_id, model=model, provider=provider_id,
                               vision=vision, instruct=instruct, free=free,
                               label=label)
            self._models[model_id] = entry
            self.save()
            return entry

    def remove_model(self, model_id: str) -> bool:
        with self._lock:
            if model_id not in self._models:
                return False
            del self._models[model_id]
            for role, mid in list(self._roles.items()):
                if mid == model_id:
                    del self._roles[role]
            self.save()
            return True

    # ------------------------------------------------------------------
    # roles
    # ------------------------------------------------------------------
    def get_role(self, role: str) -> str | None:
        """Return the model id assigned to a role, or None if unset."""
        with self._lock:
            return self._roles.get(role)

    def set_role(self, role: str, model_id: str) -> None:
        """Assign a role to a model in the pot. Empty model_id clears the role."""
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        with self._lock:
            if not model_id:
                self._roles.pop(role, None)
            else:
                if model_id not in self._models:
                    raise ValueError(f"unknown model {model_id!r}")
                self._roles[role] = model_id
            self.save()

    def resolve_role(self, role: str) -> ModelEntry | None:
        """Return the ModelEntry currently assigned to a role (or None)."""
        with self._lock:
            mid = self._roles.get(role)
            return self._models.get(mid) if mid else None

    def models_for_role(self, role: str) -> list[ModelEntry]:
        """The pot, filtered/flagged for a role picker.

        - vision role: only vision-capable instruct models.
        - big/small: all instruct models (any vision model can also do text).
        Embedding models (instruct=False) are excluded from every picker.
        """
        pot = [m for m in self.list_models() if m.instruct]
        if role == "vision":
            return [m for m in pot if m.vision]
        return pot

    # ------------------------------------------------------------------
    # migration from legacy .env
    # ------------------------------------------------------------------
    @classmethod
    def migrate_from_env(cls, path: Path | None = None) -> "ProviderRegistry":
        """Build a registry, migrating legacy .env values if no file exists.

        If ``providers.json`` already exists, it wins and migration is skipped.
        Otherwise we synthesize:
          - an ``ollama-local`` provider from OLLAMA_HOST (+ the chat model from
            OLLAMA_LLM_MODEL, vision from VISION_MODEL, small from SMALL_MODEL),
          - an ``openai`` provider if LLM_BACKEND=openai (key/url/model from
            LLM_API_KEY / LLM_BASE_URL / LLM_MODEL),
        and map the three roles to the migrated models.
        """
        reg = cls(path)
        if reg._path.exists():
            return reg
        with reg._lock:
            # Ollama local provider + its three legacy cartridge models.
            host = (os.getenv("OLLAMA_HOST") or "http://localhost:11434").strip()
            try:
                host = normalize_base_url(host, "ollama")
            except ValueError:
                host = "http://localhost:11434"
            reg._providers["ollama-local"] = Provider(
                id="ollama-local", type="ollama", base_url=host,
                label="Ollama (this machine)")

            big_model = (os.getenv("OLLAMA_LLM_MODEL") or "").strip()
            if big_model:
                mid = f"ollama-local:{big_model}"
                reg._models[mid] = ModelEntry(
                    id=mid, model=big_model, provider="ollama-local",
                    vision=_guess_vision(big_model), instruct=True)
                reg._roles["big"] = mid

            vision_model = (os.getenv("VISION_MODEL") or "").strip()
            if vision_model:
                backend = (os.getenv("VISION_BACKEND") or
                           os.getenv("LLM_BACKEND") or "ollama").strip().lower()
                if backend == "openai":
                    prov_id = reg._ensure_openai_provider()
                    mid = f"{prov_id}:{vision_model}"
                    reg._models[mid] = ModelEntry(
                        id=mid, model=vision_model, provider=prov_id,
                        vision=True, instruct=True)
                else:
                    mid = f"ollama-local:{vision_model}"
                    reg._models[mid] = ModelEntry(
                        id=mid, model=vision_model, provider="ollama-local",
                        vision=True, instruct=True)
                reg._roles["vision"] = mid

            small_model = (os.getenv("SMALL_MODEL") or "").strip()
            if small_model:
                mid = f"ollama-local:{small_model}"
                reg._models[mid] = ModelEntry(
                    id=mid, model=small_model, provider="ollama-local",
                    vision=False, instruct=True)
                reg._roles["small"] = mid

            # A legacy cloud chat backend becomes the big role.
            if (os.getenv("LLM_BACKEND") or "").strip().lower() == "openai":
                cloud_model = (os.getenv("LLM_MODEL") or "").strip()
                if cloud_model:
                    prov_id = reg._ensure_openai_provider()
                    mid = f"{prov_id}:{cloud_model}"
                    reg._models[mid] = ModelEntry(
                        id=mid, model=cloud_model, provider=prov_id,
                        vision=_guess_vision(cloud_model), instruct=True)
                    reg._roles["big"] = mid

            reg.save()
        return reg

    def _ensure_openai_provider(self) -> str:
        """Create (or reuse) the migrated OpenAI provider; return its id."""
        prov_id = "openai"
        if prov_id in self._providers:
            return prov_id
        base_url = (os.getenv("LLM_BASE_URL") or "https://api.openai.com").strip()
        api_key = (os.getenv("LLM_API_KEY") or "").strip()
        try:
            base_url = normalize_base_url(base_url, "openai")
        except ValueError:
            base_url = "https://api.openai.com"
        self._providers[prov_id] = Provider(
            id=prov_id, type="openai", base_url=base_url, api_key=api_key,
            label="OpenAI")
        return prov_id


def _guess_vision(model: str) -> bool:
    """Best-effort vision flag from a model name (for migration + UI defaults).

    Live capability detection (Ollama /api/show, or a vision probe) refines
    this; the guess only seeds a sensible default so migrated vision models
    land in the vision picker.
    """
    m = model.lower()
    needles = ("vl", "vision", "llava", "minicpm-v", "-v:", "-v-", "gpt-4o",
               "gpt-4-vision", "gemini", "claude", "qwen-vl")
    return any(n in m for n in needles)
