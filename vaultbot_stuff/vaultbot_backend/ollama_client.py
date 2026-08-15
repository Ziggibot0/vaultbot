"""Ollama API client — chat, generate, embeddings, model preloading, and stats.

Wraps the Ollama REST API (/api/chat, /api/generate, /api/embeddings) with
streaming support, tool-call parsing, vision capability detection, and
automatic model preloading for first-token latency optimization.
"""

import json
import logging
import os
import time
from collections.abc import Generator
from typing import Any

import requests

logger = logging.getLogger(__name__)

try:
    # The synthesis LLM abstraction (llm_client.py) treats OllamaClient as
    # one of two interchangeable backends (the other is OpenAICompatibleClient).
    # Importing the base is optional so ollama_client stays usable standalone.
    from llm_client import LLMClient

    _BASE = LLMClient
except Exception:  # pragma: no cover - circular-import safety
    _BASE = object


class OllamaClient(_BASE):
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        llm_model: str = "",
        embed_model: str = "nomic-embed-text",
        session_logger=None,
    ):
        self.base_url = base_url
        self.llm_model = llm_model
        self.embed_model = embed_model
        self.session_logger = session_logger
        # Reuse a single requests.Session across all calls so HTTP keep-alive
        # can pool the TCP connection to the local Ollama daemon.  Embedding
        # batches (8 concurrent) and the streaming chat loop no longer pay a
        # fresh connection handshake per request.
        self._session = requests.Session()
        # keep_alive duration sent with every chat/generate request so Ollama
        # keeps the model resident in GPU memory between calls.  Default 30m
        # covers a typical session's idle gaps.  Override with
        # VAULTBOT_OLLAMA_KEEP_ALIVE (Ollama duration string: "30m", "2h", "-1"
        # for forever, "0" to unload immediately after each call).
        self._keep_alive = os.environ.get("VAULTBOT_OLLAMA_KEEP_ALIVE", "30m")
        # Active streaming response — stored so the stop button can close the
        # HTTP connection from another thread, unblocking response.iter_lines()
        # which otherwise keeps the executor thread alive for minutes.
        self._active_stream_response: requests.Response | None = None

    def cancel_active_stream(self) -> None:
        """Close the active streaming HTTP response to unblock the executor thread.

        When the user presses Stop, the asyncio task is cancelled but the
        executor thread is blocked in response.iter_lines() — CancelledError
        doesn't propagate into threads. Closing the response from the main
        thread causes iter_lines() to raise ConnectionError, unblocking the
        thread immediately so the task can unwind.
        """
        resp = self._active_stream_response
        if resp is not None:
            try:
                resp.close()
                resp.raw.close()
            except Exception:  # noqa: BLE001 — best-effort
                pass
            finally:
                self._active_stream_response = None

    def set_model(self, model: str) -> None:
        """Switch the active LLM model at runtime."""
        self.llm_model = model
        if self.session_logger is not None:
            self.session_logger.log("model_changed", {"model": model})

    # ── Model preloading ───────────────────────────────────────────────
    # Ollama loads models lazily: the first request to a cold model triggers
    # a full load from disk into GPU/CPU memory (up to 5 min for a 27B model).
    # After the last request, the model stays resident for the keep_alive
    # window (default 5m), then is evicted.  The next request after eviction
    # pays the full load cost again — this is the "first chat of a new session
    # takes 5 minutes" problem.
    #
    # preload_model() sends a 1-token generate request that forces Ollama to
    # load the model NOW, with a long keep_alive so it stays resident.  This
    # is cheap (1 token of compute) but the load itself can take minutes for
    # large models — so callers should run it in a background thread.
    # is_model_loaded() checks /api/ps to see if the model is already
    # resident, so we can skip a redundant preload.

    def is_model_loaded(self, model: str | None = None) -> bool:
        """Check whether the model is currently resident in Ollama's memory.

        Queries /api/ps (running models).  Returns True if the model is
        loaded and ready, False if it's cold (or Ollama is unreachable).

        Cloud models (``:cloud`` suffix, e.g. ``glm-5.2:cloud``) are proxied
        through Ollama but never resident in local memory — /api/ps will
        never list them.  Return True immediately so the chat-loop model-
        load wait doesn't spin the full 300s timeout on every turn.
        """
        model = model or self.llm_model
        if not model:
            return False
        if model.endswith(":cloud") or ":cloud:" in model:
            return True
        try:
            resp = self._session.get(f"{self.base_url}/api/ps", timeout=5)
            resp.raise_for_status()
            loaded = resp.json().get("models", [])
            # /api/ps returns model names with ":latest" expanded; compare
            # by prefix so "qwen3.6:27b" matches "qwen3.6:27b" exactly.
            for m in loaded:
                name = m.get("name", "") or m.get("model", "")
                if name == model or name.startswith(model + ":"):
                    return True
            return False
        except Exception as e:  # noqa: BLE001 — best-effort probe, returns False — see CONTRIBUTING.md no-silent-fallbacks
            # Always surface: log to the session logger if set, and to the
            # module logger regardless, so an Ollama-down or a programming
            # bug is never silently swallowed as "model not loaded".
            logger.debug("is_model_loaded error for %r: %s", model, e)
            if self.session_logger:
                self.session_logger.log("ollama_is_loaded_error", {"model": model})
            return False

    def preload_model(
        self, model: str | None = None, keep_alive: str | None = None
    ) -> bool:
        """Force-load the model into Ollama's memory so the next chat request
        doesn't pay the cold-load latency (up to 5 min for large models).

        Sends a minimal /api/generate request (1 token, no system prompt)
        with a long keep_alive.  The request itself returns quickly once the
        model is loaded — the time is spent on the load, not the generation.

        Returns True if the model is now loaded, False on any failure
        (Ollama down, model not found, etc.).  Never raises — callers run
        this in background threads and can't handle exceptions there.
        """
        model = model or self.llm_model
        if not model:
            return False
        # Already resident? Skip the round-trip.
        if self.is_model_loaded(model):
            if self.session_logger is not None:
                self.session_logger.log(
                    "model_preload_skipped",
                    {"model": model, "reason": "already_loaded"},
                )
            return True
        ka = keep_alive or self._keep_alive
        # Pass num_ctx so the model is loaded with the right context window
        # allocated upfront — otherwise the first real chat request triggers
        # a context resize (unload + reload) even after preload.
        #
        # CRITICAL: apply the SAME VAULTBOT_NUM_CTX_CAP as chat() does (line
        # ~803). If preload uses the uncapped native context (e.g. 262144)
        # but chat() caps it (32768), Ollama must unload+reload the model on
        # the first chat request. For dense models like qwen3.8 this reload
        # takes 30+ seconds; combined with a cold load it exceeds Ollama's
        # 60s internal timeout and returns a 500. MoE models like qwen3.6
        # can resize in-place (~2.7s) so the mismatch was invisible there.
        _opts = {"num_predict": 1, "temperature": 0}
        try:
            _ctx = self.context_window(model)
            _cap = int(os.environ.get("VAULTBOT_NUM_CTX_CAP", "32768"))
            if _cap > 0 and _ctx and _ctx > _cap:
                _ctx = _cap
            if _ctx and _ctx > 0:
                _opts["num_ctx"] = _ctx
        except Exception:  # noqa: BLE001 — best-effort context detection
            pass
        t0 = time.time()
        try:
            resp = self._session.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": "",
                    "stream": False,
                    "options": _opts,
                    "keep_alive": ka,
                },
                timeout=600,  # large models can take minutes to load
            )
            resp.raise_for_status()
            loaded = self.is_model_loaded(model)
            if self.session_logger is not None:
                self.session_logger.log(
                    "model_preloaded",
                    {
                        "model": model,
                        "keep_alive": ka,
                        "already_loaded": loaded,
                        "duration_ms": (time.time() - t0) * 1000,
                    },
                )
            return True
        except Exception as e:
            if self.session_logger is not None:
                self.session_logger.log(
                    "model_preload_failed",
                    {
                        "model": model,
                        "error": str(e),
                        "duration_ms": (time.time() - t0) * 1000,
                    },
                )
            return False

    def list_local_models(self) -> list[str]:
        """Return model names installed in the local Ollama daemon.

        Uses the HTTP API exclusively — no CLI fallback.  If Ollama is
        unreachable, this raises so the caller (and ultimately the user)
        sees that Ollama is down rather than silently getting an empty
        model list.
        """
        try:
            resp = self._session.get(f"{self.base_url}/api/tags", timeout=10)
            resp.raise_for_status()
            return [m["name"] for m in resp.json().get("models", [])]
        except Exception as e:
            self._log_tool("list_local_models", {}, error=str(e))
            raise

    # Backend-agnostic alias used by llm_client.LLMClient and the /models
    # endpoint. Same as list_local_models; the alias lets /models call
    # .list_models() uniformly across Ollama and OpenAI-compatible backends.
    def list_models(self) -> list[str]:
        return self.list_local_models()

    # Per-instance cache: model name → context window size.  A model's
    # context window never changes at runtime, but /api/show is a blocking
    # HTTP call that takes 1-5s for cloud models.  Caching it saves 7-10
    # round-trips per chat turn (called from chat(), generate(), preflight
    # compression, and the token meter).
    _ctx_win_cache: dict[str, int] = {}

    def context_window(self, model: str | None = None) -> int:
        """Return the model's native context-window size in tokens.

        Queries Ollama's /api/show for the model and extracts the
        architecture-prefixed ``*.context_length`` field from ``model_info``
        (e.g. ``qwen35moe.context_length``, ``glm5.2.context_length``).
        Works for both local and cloud (``:cloud``) Ollama models — both
        return the same show metadata shape.

        Results are cached per-instance — a model's context window never
        changes at runtime, so we only hit /api/show once per model.

        Raises on any failure — the caller must know the context window is
        unknown rather than silently getting a wrong 32768.
        """
        model = model or self.llm_model
        if not model:
            raise ValueError(
                "context_window: no model specified and no default model set"
            )
        if model in self._ctx_win_cache:
            return self._ctx_win_cache[model]
        try:
            resp = self._session.post(
                f"{self.base_url}/api/show", json={"model": model}, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            info = data.get("model_info") or {}
            # The key is "<arch>.context_length" — find it generically.
            for key, val in info.items():
                if key.endswith(".context_length") and isinstance(val, (int, float)):
                    result = int(val)
                    self._ctx_win_cache[model] = result
                    return result
            # Some older Ollama builds expose it under parameters as a
            # "num_ctx" string like "262144".
            params = data.get("parameters") or ""
            if isinstance(params, str):
                for tok in params.split():
                    if tok.startswith("num_ctx"):
                        # "num_ctx" or "num_ctx:262144"
                        if ":" in tok:
                            result = int(tok.split(":")[-1])
                            self._ctx_win_cache[model] = result
                            return result
            raise RuntimeError(
                f"context_window: /api/show returned no context_length for model {model!r}"
            )
        except Exception as e:
            self._log_tool("context_window", {"model": model}, error=str(e))
            raise

    def get_model_capabilities(self, model: str | None = None) -> dict[str, bool]:
        """Return capability flags (vision, instruct) for a model.

        Queries Ollama's ``/api/show`` and inspects the response:
          - ``vision``: True if the response contains a ``projector_info``
            section (Ollama attaches this only when a vision projector is
            present). We check for any key starting with ``projector_info``
            because the exact sub-keys vary by architecture.
          - ``instruct``: True if the model family/name suggests it's a
            chat/instruct model (not a base/embed model). We check the
            families list + model name for common instruct markers. This
            is a heuristic — Ollama doesn't expose an explicit "instruct"
            flag — but it's good enough to keep embed models out of the
            "recommended" group in the dropdown.

        Falls back to ``{vision: False, instruct: True}`` only when no model
        is configured (empty string). On any actual API error, raises so
        the dropdown doesn't silently show wrong capability flags.
        """
        # None → use the configured model (convenience for callers that
        # don't specify). Empty string → genuinely no model, return defaults.
        if model is None:
            model = self.llm_model
        if not model:
            return {"vision": False, "instruct": True}
        try:
            resp = self._session.post(
                f"{self.base_url}/api/show", json={"model": model}, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            # Vision: Ollama includes projector_info only when a vision
            # projector is attached to the model.
            vision = False
            for key in data:
                if key.startswith("projector_info"):
                    vision = True
                    break
            # Also check model_info for a vision-specific arch key
            # (some models embed the projector info under model_info).
            if not vision:
                info = data.get("model_info") or {}
                for key in info:
                    if "projector" in key.lower() or "vision" in key.lower():
                        vision = True
                        break
            # Instruct: heuristic — check families + model name for
            # instruct/chat markers, and exclude embed models.
            instruct = True
            details = data.get("details") or {}
            details.get("families") or []
            name_lower = (model or "").lower()
            # Embed models are not instruct models.
            if "embed" in name_lower:
                instruct = False
            # Base models (no chat template) are not instruct models.
            # Ollama exposes templates; if there's no chat template, it's
            # likely a base model. We check the templates field.
            templates = data.get("templates") or {}
            if isinstance(templates, dict) and not templates.get("chat"):
                # No chat template → probably a base/completion model.
                # But some models use a different template key, so only
                # flag as non-instruct if we also see "base" in the name.
                if "base" in name_lower:
                    instruct = False
            return {"vision": vision, "instruct": instruct}
        except Exception as e:
            self._log_tool("get_model_capabilities", {"model": model}, error=str(e))
            raise

    def _log_tool(
        self,
        method: str,
        inputs: dict[str, Any],
        outputs: Any = None,
        duration_ms: float | None = None,
        error: str | None = None,
    ):
        if self.session_logger is None:
            return
        self.session_logger.log_tool_call(
            tool="ollama",
            method=method,
            inputs=inputs,
            outputs=outputs,
            duration_ms=duration_ms,
            error=error,
        )

    def _chat_log_summary(self, payload: dict, stream: bool) -> dict:
        """Build a compact log summary of the chat payload.

        The full payload (all message content + thinking + tool_calls) is
        NEVER logged — it's the source of the 41 MB session log that fills
        disk over a days-long autonomous session. Instead we log the model,
        message count, per-message role + content length, and tool count.
        That's enough to debug retrieval/compaction issues without the bloat.
        """
        msgs = payload.get("messages", [])
        msg_summary = [
            {
                "role": m.get("role", "?"),
                "chars": len(str(m.get("content", "") or "")),
                "thinking_chars": len(str(m.get("thinking", "") or "")),
                "has_tool_calls": bool(m.get("tool_calls")),
            }
            for m in msgs
            if isinstance(m, dict)
        ]
        return {
            "model": payload.get("model", ""),
            "stream": stream,
            "message_count": len(msgs),
            "total_content_chars": sum(m["chars"] for m in msg_summary),
            "messages": msg_summary,
            "has_tools": bool(payload.get("tools")),
        }

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        stream: bool = False,
        think: bool | None = None,
    ) -> dict | Generator:
        """
        Generate text from the LLM.
        If stream=True, returns a generator that yields chunks.
        Each chunk is a dict with keys: 'response' (the text chunk) and optionally 'thinking' (the reasoning chunk).

        ``think=False`` disables chain-of-thought reasoning for this call —
        use it for bounded small-model tasks (query rewrite, rerank,
        section filter) where reasoning burns 60s for a one-line answer
        and the result is parsed by a guard, not shown to the user.
        """
        payload = {
            "model": self.llm_model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": temperature,
            },
            "keep_alive": self._keep_alive,
        }
        # Disable reasoning for bounded tasks. qwen3/lfm2.5-style models
        # stream a `thinking` field that can dominate latency on a 0.8b
        # model asked to rewrite a search query. think=False makes it
        # answer directly in content.
        if think is False:
            payload["think"] = False
        # Pass num_ctx so Ollama allocates the right context window upfront
        # (same rationale as chat() — see comment there).  Apply the SAME
        # VAULTBOT_NUM_CTX_CAP as chat() + preload_model() so generate()
        # never triggers a context resize (unload+reload) on a model that
        # was preloaded or chatted with a capped num_ctx.
        try:
            _ctx = self.context_window(self.llm_model)
            _cap = int(os.environ.get("VAULTBOT_NUM_CTX_CAP", "32768"))
            if _cap > 0 and _ctx and _ctx > _cap:
                _ctx = _cap
            if _ctx and _ctx > 0:
                payload["options"]["num_ctx"] = _ctx
        except Exception:  # noqa: BLE001 — best-effort context window
            pass
        if system:
            payload["system"] = system
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens

        t0 = time.time()
        try:
            response = self._session.post(
                f"{self.base_url}/api/generate",
                json=payload,
                stream=stream,
                timeout=300,
            )
            response.raise_for_status()
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log_tool(
                "generate",
                {"payload": payload, "stream": stream},
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )
            raise

        if stream:

            def generate_chunks():
                chunk_count = 0
                try:
                    for line in response.iter_lines():
                        if line:
                            data = json.loads(line)
                            chunk = {
                                "response": data.get("response", ""),
                                "thinking": data.get("thinking", ""),
                            }
                            yield chunk
                            chunk_count += 1
                            if data.get("done", False):
                                break
                except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    self._log_tool(
                        "generate",
                        {"payload": payload, "stream": stream},
                        error=str(e),
                        duration_ms=(time.time() - t0) * 1000,
                    )
                    raise
                finally:
                    self._log_tool(
                        "generate",
                        {"payload": payload, "stream": stream},
                        outputs={"chunks": chunk_count},
                        duration_ms=(time.time() - t0) * 1000,
                    )

            return generate_chunks()
        else:
            data = response.json()
            result = {
                "response": data.get("response", ""),
                "thinking": data.get("thinking", ""),
            }
            self._log_tool(
                "generate",
                {"payload": payload, "stream": stream},
                outputs=result,
                duration_ms=(time.time() - t0) * 1000,
            )
            return result

    def embeddings(self, text: str) -> list[float]:
        """
        Get embeddings for the given text using the embedding model.
        Returns a list of floats.
        """
        # Truncate very long text to avoid overloading Ollama.
        # nomic-embed-text has a ~6000-char practical limit; 8000 causes 500s.
        truncated = len(text) > 4000
        if truncated:
            text = text[:4000]
        payload = {"model": self.embed_model, "prompt": text}
        t0 = time.time()
        try:
            response = self._session.post(
                f"{self.base_url}/api/embeddings", json=payload
            )
            response.raise_for_status()
            data = response.json()
            embedding = data["embedding"]
            self._log_tool(
                "embeddings",
                {
                    "model": self.embed_model,
                    "truncated": truncated,
                    "text_length": len(payload["prompt"]),
                },
                outputs={"embedding_length": len(embedding)},
                duration_ms=(time.time() - t0) * 1000,
            )
            return embedding
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log_tool(
                "embeddings",
                {
                    "model": self.embed_model,
                    "truncated": truncated,
                    "text_length": len(payload["prompt"]),
                },
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )
            raise

    def batch_embeddings(
        self, texts: list[str], max_workers: int = 8
    ) -> list[list[float] | None]:
        """Get embeddings for multiple texts in parallel via ThreadPoolExecutor.

        Ollama's embedding endpoint is stateless and thread-safe — concurrent
        requests are handled by the Ollama server's internal queue.  This cuts
        a 282-note weave from ~282 sequential round-trips to ~36 batches of 8,
        roughly an 8x speedup.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: list[list[float] | None] = [None] * len(texts)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.embeddings, t): i for i, t in enumerate(texts)
            }
            for future in as_completed(futures):
                i = futures[future]
                try:
                    results[i] = future.result()
                except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    results[i] = None
        return results

    def is_running(self) -> bool:
        """Check if the Ollama server is running."""
        try:
            response = self._session.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except:
            return False

    def get_ollama_stats(self) -> dict:
        """Return a snapshot of Ollama's runtime status for the GUI.

        Combines /api/ps (loaded models with VRAM + context + expiry) and
        /api/version into a single dict the plugin renders as a live stats
        bar.  Never raises — best-effort so a stats fetch failure never
        blocks the chat loop.
        """
        stats: dict[str, Any] = {
            "running": False,
            "version": None,
            "models": [],
        }
        try:
            resp = self._session.get(f"{self.base_url}/api/version", timeout=5)
            if resp.status_code == 200:
                stats["version"] = resp.json().get("version", "")
        except Exception:  # noqa: BLE001 — best-effort stats
            pass
        try:
            resp = self._session.get(f"{self.base_url}/api/ps", timeout=5)
            if resp.status_code == 200:
                stats["running"] = True
                for m in resp.json().get("models", []):
                    stats["models"].append(
                        {
                            "name": m.get("name", ""),
                            "size_vram": m.get("size_vram", 0),
                            "size_total": m.get("size", 0),
                            "context_length": m.get("context_length", 0),
                            "expires_at": m.get("expires_at", ""),
                        }
                    )
        except Exception:  # noqa: BLE001 — best-effort stats
            pass
        return stats

    def vision_capable(self) -> bool:
        """Probe whether the current Ollama model can see images.

        Ollama accepts images in /api/chat via the per-message `images`
        field (a list of base64 strings). We send a tiny red test image and
        ask what color it is; True only if the reply mentions red. This is
        the human-centered check the GUI calls before ingest so it can alert
        the user to pick a vision model if their chat model is text-only.

        Thinking-model note: qwen3-style models stream reasoning into a
        separate `message.thinking` field and may spend the whole token
        budget reasoning before the answer lands in `message.content`. We
        (1) disable thinking for this probe via `"think": false` so the
        model answers directly in content, (2) bump num_predict so a model
        that still thinks has room to finish, and (3) check BOTH the
        thinking and content fields for "red" as a belt-and-suspenders — a
        vision model that actually saw the red square will mention "red" in
        its reasoning even if the final content got truncated.
        """
        from llm_client import _test_image_base64

        img_b64 = _test_image_base64()
        payload = {
            "model": self.llm_model,
            "messages": [
                {
                    "role": "user",
                    "content": "What color is the square in this image? Reply with one word.",
                    "images": [img_b64],
                }
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "num_predict": 64},
        }
        try:
            r = self._session.post(
                f"{self.base_url}/api/chat", json=payload, timeout=60
            )
            if r.status_code != 200:
                return False
            msg = r.json().get("message", {}) or {}
            content = (msg.get("content", "") or "").lower()
            thinking = (msg.get("thinking", "") or "").lower()
            return "red" in content or "red" in thinking
        except Exception:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            return False

    def _chat_native_no_think(
        self, payload: dict[str, Any], t0: float
    ) -> dict[str, Any]:
        """Fast path for bounded small-model calls (think=False, no tools).

        Ollama's OpenAI-compatible /v1/chat/completions endpoint IGNORES the
        ``think`` field — only the NATIVE /api/chat endpoint honors it. So a
        0.8b reasoning model asked to rewrite a search query keeps reasoning
        for 20s and times out, even with ``think=False`` on /v1. This method
        routes the call through /api/chat where ``think: false`` actually
        disables reasoning, so the same one-line rewrite returns in
        well under a second.

        /api/chat lacks finish_reason + reliable tool-call parsing, but
        bounded pre-filter calls (query rewrite, rerank judge, section
        filter) don't use tools and want a single non-stream response, so
        the raw endpoint is fine here. The main chat loop (which needs tools
        + finish_reason) still uses /v1 via chat().

        Returns the same dict shape as chat() non-stream: {response,
        thinking, tool_calls, finish_reason}.
        """
        _timeout = float(os.environ.get("VAULTBOT_SMALL_TIMEOUT_SECONDS", "20"))
        # /api/chat takes think + options at the top level (not under
        # options like /v1). payload already has think=False set by chat().
        # Strip /v1-only keys that /api/chat doesn't understand.
        native = {
            "model": payload["model"],
            "messages": payload["messages"],
            "stream": False,
            "think": False,
            "options": dict(payload.get("options") or {}),
            "keep_alive": payload.get("keep_alive", self._keep_alive),
        }
        try:
            r = self._session.post(
                f"{self.base_url}/api/chat", json=native, timeout=_timeout
            )
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001 — best-effort, raises to caller — see CONTRIBUTING.md no-silent-fallbacks
            self._log_tool(
                "chat",
                self._chat_log_summary(native, False),
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )
            raise
        data = r.json()
        msg = data.get("message", {}) or {}
        result = {
            "response": msg.get("content") or "",
            "thinking": msg.get("thinking") or "",
            "tool_calls": [],
            "finish_reason": "stop" if data.get("done") else "length",
        }
        self._log_tool(
            "chat",
            self._chat_log_summary(native, False),
            outputs={
                "response_len": len(result["response"]),
                "thinking_len": len(result["thinking"]),
                "tool_calls": 0,
                "finish_reason": result["finish_reason"],
                "endpoint": "/api/chat",
                "think": False,
            },
            duration_ms=(time.time() - t0) * 1000,
        )
        return result

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        stream: bool = False,
        think: bool | None = None,
        max_predict: int | None = None,
    ) -> dict | Generator:
        """
        Multi-turn chat completion via Ollama's OpenAI-compatible /v1/chat/completions
        endpoint, with optional tool-calling.

        This is the same endpoint Hermes uses — it provides proper finish_reason
        ("stop", "tool_calls", "length") and reliable tool-call parsing, which
        the raw /api/chat endpoint lacks. The raw endpoint doesn't return
        finish_reason at all (just done:true), and Ollama's tool-calling
        protocol on /api/chat has known issues where models emit text
        ("Let me check that...") instead of structured tool_calls.

        The /v1 endpoint gives us:
          - finish_reason: "stop" (model is done), "tool_calls" (model wants
            to call a tool), "length" (truncated, needs continuation)
          - delta.content / delta.reasoning / delta.tool_calls in streaming
          - message.content / message.reasoning / message.tool_calls in
            non-streaming

        For local models, no max_tokens limit is set — the model thinks as
        long as it needs (local inference is free). The chat handler's
        heartbeat keeps the user informed during long thinking passes.

        If stream=True, returns a generator yielding chunks with keys:
          'response' (text chunk), 'thinking' (reasoning chunk),
          'tool_calls' (list of tool call dicts, or []),
          'finish_reason' (only on the terminal chunk: "stop"|"tool_calls"|"length"),
          'done' (True only on the terminal sentinel chunk).
        If stream=False, returns a dict with 'response', 'thinking',
        'tool_calls', 'finish_reason'.
        """
        payload: dict[str, Any] = {
            "model": self.llm_model,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
        }
        # Disable reasoning for bounded small-model tasks (query rewrite,
        # rerank, section filter). These don't need chain-of-thought — a
        # 0.8b model reasoning on a one-line rewrite was the 60s-per-turn
        # bottleneck. think=False makes the model answer directly in
        # content, cutting a 60s timeout to sub-second.
        if think is False:
            payload["think"] = False
        # Cap output tokens for bounded tasks so a runaway small model can't
        # spend the whole budget rambling. Applied alongside num_ctx below.
        _extra_opts: dict[str, Any] = {}
        if max_predict is not None:
            _extra_opts["num_predict"] = max_predict
        # Pass num_ctx via options so Ollama allocates the full context window
        # upfront. Without this, Ollama defaults to a small num_ctx
        # (2048/4096) and when a large chat payload arrives it UNLOADS the
        # model, resizes the context buffer, and RELOADS — the "spit out and
        # reload" the user sees on every first message. By sending the
        # model's native context_length, the model is loaded once with the
        # right size and never needs to resize.
        try:
            _ctx = self.context_window(self.llm_model)
            _cap = int(os.environ.get("VAULTBOT_NUM_CTX_CAP", "32768"))
            if _cap > 0 and _ctx and _ctx > _cap:
                _ctx = _cap  # cap KV buffer: native 128k ctx allocates a 128k-token KV even for short turns
            if _ctx and _ctx > 0:
                _extra_opts["num_ctx"] = _ctx
        except Exception:  # noqa: BLE001 — best-effort context detection
            pass  # best-effort — if /api/show fails, Ollama uses its default
        if _extra_opts:
            payload["options"] = _extra_opts
        # keep_alive so the model stays resident between calls.
        payload["keep_alive"] = self._keep_alive
        if tools:
            payload["tools"] = tools
        # No max_tokens — local models are free. Let the model think and
        # generate as long as it needs. The chat handler's heartbeat keeps
        # the user informed during long passes.

        t0 = time.time()
        # ── think=False fast path ────────────────────────────────────────
        # Ollama's OpenAI-compatible /v1/chat/completions endpoint IGNORES
        # the ``think`` field — only the NATIVE /api/chat endpoint honors it.
        # (The vision probe already uses /api/chat for exactly this reason.)
        # So bounded small-model calls (query rewrite, rerank, filter) must
        # go through /api/chat when think=False, otherwise the 0.8b reasoning
        # model keeps reasoning for 20s and times out — the `think=False` we
        # added on /v1 was a no-op.
        #
        # /api/chat lacks finish_reason + reliable tool-call parsing, but
        # bounded pre-filter calls don't use tools and want a single
        # non-stream response, so the raw endpoint is fine here. The main
        # chat loop (which needs tools + finish_reason) still uses /v1.
        if think is False and not stream and not tools:
            return self._chat_native_no_think(payload, t0)
        try:
            if stream:
                _read_timeout = float(
                    os.environ.get("VAULTBOT_OLLAMA_READ_TIMEOUT_SECONDS", "600")
                )
                response = self._session.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    stream=True,
                    timeout=(5, _read_timeout),
                )
                # Store so the stop button can close this connection.
                self._active_stream_response = response
            else:
                # Bounded small-model tasks (think=False) should answer in
                # well under a second; cap the timeout so a stuck 0.8b model
                # can't block the whole turn for 60s. Default 20s covers a
                # cold load; lower via VAULTBOT_SMALL_TIMEOUT_SECONDS.
                _chat_timeout = 20.0 if think is False else 60.0
                if think is False:
                    _chat_timeout = float(
                        os.environ.get(
                            "VAULTBOT_SMALL_TIMEOUT_SECONDS", str(_chat_timeout)
                        )
                    )
                response = self._session.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    stream=False,
                    timeout=_chat_timeout,
                )
            response.raise_for_status()
        except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
            # Capture the 500 error body from Ollama for debugging.
            _err_body = ""
            try:
                if hasattr(e, "response") and e.response is not None:
                    _err_body = e.response.text[:1000]
            except Exception:  # noqa: BLE001 — best-effort error-body extraction; if this fails the outer except still logs + raises
                pass
            self._log_tool(
                "chat",
                {**self._chat_log_summary(payload, stream), "error_body": _err_body},
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )
            raise

        if stream:

            def chat_chunks():
                chunk_count = 0
                # Per-index accumulator for fragmented tool-call arguments.
                # OpenAI streaming sends tool-call arguments in fragments
                # across multiple chunks (delta.tool_calls[].function.arguments
                # is a string that grows chunk by chunk). We accumulate
                # them and emit the complete tool_calls list on the terminal
                # chunk, matching the Ollama raw API's one-shot shape.
                tc_acc: dict[int, dict[str, str]] = {}
                finish_reason: str | None = None
                try:
                    for raw in response.iter_lines():
                        if not raw:
                            continue
                        line = (
                            raw.decode("utf-8", "replace")
                            if isinstance(raw, (bytes, bytearray))
                            else raw
                        )
                        if not line.startswith("data:"):
                            continue
                        body = line[len("data:") :].strip()
                        if body == "[DONE]":
                            break
                        try:
                            data = json.loads(body)
                        except json.JSONDecodeError:
                            continue
                        choice = (data.get("choices") or [{}])[0]
                        delta = choice.get("delta", {}) or {}
                        content = delta.get("content") or ""
                        reasoning = delta.get("reasoning") or ""
                        # Accumulate tool-call fragments.
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            slot = tc_acc.setdefault(
                                idx, {"id": "", "name": "", "arguments_str": ""}
                            )
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function", {}) or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["arguments_str"] += fn["arguments"]
                        chunk = {
                            "response": content,
                            "thinking": reasoning,
                            "tool_calls": [],  # emitted only at the end
                        }
                        if content or reasoning:
                            yield chunk
                            chunk_count += 1
                        fr = choice.get("finish_reason")
                        if fr:
                            finish_reason = fr
                        if fr in ("tool_calls", "stop", "length"):
                            break
                except Exception as e:  # noqa: BLE001 — best-effort, returns error/empty to caller — see CONTRIBUTING.md no-silent-fallbacks
                    self._log_tool(
                        "chat",
                        self._chat_log_summary(payload, stream),
                        error=str(e),
                        duration_ms=(time.time() - t0) * 1000,
                    )
                    raise
                finally:
                    self._active_stream_response = None
                # Emit accumulated tool calls as one chunk (Ollama-style
                # one-shot), so the chat handler sees the same shape it
                # always has.
                if tc_acc:
                    assembled = []
                    for idx in sorted(tc_acc):
                        slot = tc_acc[idx]
                        assembled.append(
                            {
                                "id": slot["id"] or f"call_{idx}",
                                "function": {
                                    "name": slot["name"],
                                    "arguments": slot["arguments_str"] or "{}",
                                },
                            }
                        )
                    yield {"response": "", "thinking": "", "tool_calls": assembled}
                    chunk_count += 1
                # Terminal chunk: signal done + carry finish_reason so the
                # chat handler can distinguish "model finished naturally"
                # (stop) from "model called tools" (tool_calls) from
                # "model was truncated" (length).
                self._log_tool(
                    "chat",
                    self._chat_log_summary(payload, stream),
                    outputs={
                        "chunks": chunk_count,
                        "tool_calls": len(tc_acc),
                        "finish_reason": finish_reason,
                    },
                    duration_ms=(time.time() - t0) * 1000,
                )
                yield {"done": True, "finish_reason": finish_reason or "stop"}
                self._active_stream_response = None

            return chat_chunks()
        else:
            data = response.json()
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message", {}) or {}
            tool_calls = msg.get("tool_calls") or []
            # Normalize tool_calls to Ollama shape: each has
            # {"id":..., "function":{"name":..., "arguments":...}}.
            # OpenAI already uses this shape, but some entries may lack "id".
            normalized_tc = []
            for i, tc in enumerate(tool_calls):
                fn = tc.get("function", {}) or {}
                normalized_tc.append(
                    {
                        "id": tc.get("id") or f"call_{i}",
                        "function": {
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", "{}"),
                        },
                    }
                )
            finish_reason = choice.get("finish_reason") or "stop"
            result = {
                "response": msg.get("content") or "",
                "thinking": msg.get("reasoning") or "",
                "tool_calls": normalized_tc,
                "finish_reason": finish_reason,
            }
            self._log_tool(
                "chat",
                self._chat_log_summary(payload, stream),
                outputs={
                    "response_len": len(result["response"]),
                    "thinking_len": len(result["thinking"]),
                    "tool_calls": len(result["tool_calls"]),
                    "finish_reason": finish_reason,
                },
                duration_ms=(time.time() - t0) * 1000,
            )
            return result
