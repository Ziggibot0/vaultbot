import logging
import os
import time
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


def is_cloud_model(model: str) -> bool:
    """Return True if ``model`` is an Ollama cloud model.

    Ollama names cloud models with either a ``:cloud`` tag (``glm-5.3:cloud``)
    or a ``-cloud`` suffix (``gemma4:31b-cloud``). Cloud models are always
    resident on Ollama's servers, so they never need a local load/wait. This
    helper is the single source of truth for that check so callers don't
    duplicate a fragile string match.
    """
    if not model:
        return False
    return model.endswith(":cloud") or model.endswith("-cloud") or ":cloud:" in model


class OllamaRuntime:
    _ctx_win_cache: ClassVar[dict[str, int]] = {}
    _ctx_win_fail_cache: ClassVar[dict[str, float]] = {}

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def is_model_loaded(self, model: str | None = None) -> bool:
        owner = self._owner
        model = model or owner.llm_model
        if not model:
            return False
        if is_cloud_model(model):
            return True
        try:
            resp = owner._session.get(f"{owner.base_url}/api/ps", timeout=5)
            resp.raise_for_status()
            loaded = resp.json().get("models", [])
            for loaded_model in loaded:
                name = loaded_model.get("name", "") or loaded_model.get("model", "")
                if name == model or name.startswith(model + ":"):
                    return True
            return False
        except Exception as exc:  # noqa: BLE001 -- best-effort availability probe
            logger.debug("is_model_loaded error for %r: %s", model, exc)
            if owner.session_logger:
                owner.session_logger.log("ollama_is_loaded_error", {"model": model})
            return False

    def preload_model(
        self, model: str | None = None, keep_alive: str | None = None
    ) -> bool:
        owner = self._owner
        model = model or owner.llm_model
        if not model:
            return False
        if self.is_model_loaded(model):
            if owner.session_logger is not None:
                owner.session_logger.log(
                    "model_preload_skipped",
                    {"model": model, "reason": "already_loaded"},
                )
            return True
        selected_keep_alive = keep_alive or owner._keep_alive
        options = {"num_predict": 1, "temperature": 0}
        try:
            context = self.context_window(model)
            cap = int(os.environ.get("VAULTBOT_NUM_CTX_CAP", "32768"))
            if cap > 0 and context and context > cap:
                context = cap
            if context and context > 0:
                options["num_ctx"] = context
        except Exception:  # noqa: BLE001 -- best-effort context detection
            pass
        started_at = time.time()
        try:
            payload = {
                "model": model,
                "prompt": "",
                "stream": False,
                "options": options,
                "keep_alive": selected_keep_alive,
            }
            resp = owner._session.post(
                f"{owner.base_url}/api/generate", json=payload, timeout=600
            )
            resp.raise_for_status()
            loaded = self.is_model_loaded(model)
            if owner.session_logger is not None:
                owner.session_logger.log(
                    "model_preloaded",
                    {
                        "model": model,
                        "keep_alive": selected_keep_alive,
                        "already_loaded": loaded,
                        "duration_ms": (time.time() - started_at) * 1000,
                    },
                )
            return True
        except Exception as exc:  # noqa: BLE001 -- preload reports failure as False
            if owner.session_logger is not None:
                owner.session_logger.log(
                    "model_preload_failed",
                    {
                        "model": model,
                        "error": str(exc),
                        "duration_ms": (time.time() - started_at) * 1000,
                    },
                )
            return False

    def context_window(self, model: str | None = None) -> int:
        owner = self._owner
        model = model or owner.llm_model
        if not model:
            raise ValueError(
                "context_window: no model specified and no default model set"
            )
        if model in self._ctx_win_cache:
            return self._ctx_win_cache[model]
        failed_at = self._ctx_win_fail_cache.get(model)
        if failed_at is not None:
            ttl = float(os.environ.get("VAULTBOT_CTX_PROBE_FAIL_TTL", "300") or "300")
            if (time.monotonic() - failed_at) < ttl:
                raise RuntimeError(
                    f"context_window: probe for {model!r} failed recently "
                    f"(negative-cached); not re-probing until TTL "
                    f"({ttl:.0f}s) expires"
                )
            del self._ctx_win_fail_cache[model]
        try:
            resp = owner._session.post(
                f"{owner.base_url}/api/show", json={"model": model}, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            info = data.get("model_info") or {}
            for key, value in info.items():
                if key.endswith(".context_length") and isinstance(value, (int, float)):
                    result = int(value)
                    self._ctx_win_cache[model] = result
                    self._ctx_win_fail_cache.pop(model, None)
                    return result
            params = data.get("parameters") or ""
            if isinstance(params, str):
                for token in params.split():
                    if token.startswith("num_ctx") and ":" in token:
                        result = int(token.split(":")[-1])
                        self._ctx_win_cache[model] = result
                        self._ctx_win_fail_cache.pop(model, None)
                        return result
            raise RuntimeError(
                "context_window: /api/show returned no context_length "
                f"for model {model!r}"
            )
        except Exception as exc:  # noqa: BLE001 -- log and preserve probe failure
            self._ctx_win_fail_cache[model] = time.monotonic()
            owner._log_tool("context_window", {"model": model}, error=str(exc))
            raise

    def get_model_capabilities(self, model: str | None = None) -> dict[str, bool]:
        owner = self._owner
        if model is None:
            model = owner.llm_model
        if not model:
            return {"vision": False, "instruct": True}
        try:
            resp = owner._session.post(
                f"{owner.base_url}/api/show", json={"model": model}, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            vision = any(key.startswith("projector_info") for key in data)
            if not vision:
                info = data.get("model_info") or {}
                vision = any(
                    "projector" in key.lower() or "vision" in key.lower()
                    for key in info
                )
            instruct = "embed" not in model.lower()
            templates = data.get("templates") or {}
            if (
                isinstance(templates, dict)
                and not templates.get("chat")
                and "base" in model.lower()
            ):
                instruct = False
            return {"vision": vision, "instruct": instruct}
        except Exception as exc:  # noqa: BLE001 -- log and preserve API failure
            owner._log_tool("get_model_capabilities", {"model": model}, error=str(exc))
            raise
