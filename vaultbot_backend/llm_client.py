"""
LLM client abstraction — let VaultBot's synthesis step talk to ANY backend.

Why
---
The whole point of VaultBot is that the LLM is only ever used for the *final
synthesis* — the dig, retrieval, and research loop are token-free. That makes
the synthesis LLM a small, swappable surface. A college kid on a weak laptop
cannot run a multi-GB local chat model, but they CAN afford a few cents of API
spend per session (the research loop already keeps the token count tiny). So
the synthesis client must be configurable: local Ollama (free, private, heavy)
OR any OpenAI-compatible API endpoint (OpenAI, OpenRouter→Anthropic, Gemini,
vLLM, LM Studio, anything that speaks /v1/chat/completions) using a key the
user brings.

Embeddings are a SEPARATE concern and stay on OllamaClient (nomic-embed-text,
~270MB, light enough for a weak laptop). This module only abstracts the
chat/synthesis client.

Contract
--------
Every LLMClient exposes exactly the surface main.py already uses on
OllamaClient, so the 12 `ollama_client.*` call sites work unchanged no matter
which backend is active:

  - llm_model: str                 (current model name; read + set)
  - base_url: str                  (kept for backwards-compat health pings)
  - set_model(name) -> None
  - list_models() -> List[str]     (live model list from the backend)
  - chat(messages, tools, temperature, stream) -> dict | generator
  - is_running() -> bool
  - health_check() -> bool

The streaming chat contract (what main.py's agentic loop consumes):
  - stream=True  -> generator yielding dicts:
        {"response": str, "thinking": str, "tool_calls": list}
    followed by a terminal {"done": True} chunk (no response, no tool_calls).
  - stream=False -> dict {"response", "thinking", "tool_calls"}.

Tool-call shape (what main.py's tool executor reads):
  {"id": str, "function": {"name": str, "arguments": <dict-or-json-str>}}
  Ollama returns arguments already parsed; OpenAI returns a JSON string —
  both are accepted by main.py's `json.loads`-with-fallback parser.

Selection
---------
A factory `get_llm_client()` reads .env and returns the right instance:

  LLM_BACKEND=ollama            -> OllamaClient (local, free)
  LLM_BACKEND=openai           -> OpenAICompatibleClient (API key)
  LLM_BASE_URL, LLM_API_KEY, LLM_MODEL configure the openai path.

If LLM_BACKEND is unset but OLLAMA_LLM_MODEL is set, we assume the legacy
Ollama-only setup so existing installs keep working with zero config change.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Generator
from typing import Any

import requests


def _test_image_base64() -> str:
    """Build a tiny red PNG, as base64.

    Used by vision_capable() to probe whether a model can actually see
    images. Uses PIL if available (reliable), else a hand-built PNG.
    """
    import base64
    try:
        from io import BytesIO

        from PIL import Image, ImageDraw
        img = Image.new("RGB", (32, 32), (255, 0, 0))
        d = ImageDraw.Draw(img)
        d.text((4, 10), "RED", fill=(255, 255, 255))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        # Fallback: a minimal valid 1x1 red PNG (known-good bytes).
        return "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKkMIQAAAABJRU5ErkJggg=="


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------
class LLMClient:
    """Abstract synthesis-LLM client.

    Concrete subclasses: OllamaClient (defined in ollama_client.py, already
    implements this surface) and OpenAICompatibleClient (below).
    """

    llm_model: str
    base_url: str

    def set_model(self, model: str) -> None:
        raise NotImplementedError

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def chat(self,
             messages: list[dict[str, Any]],
             tools: list[dict[str, Any]] | None = None,
             temperature: float = 0.7,
             stream: bool = False) -> Any:
        raise NotImplementedError

    def is_running(self) -> bool:
        raise NotImplementedError

    def health_check(self) -> bool:
        """Backend reachable + responding. Used by the /health endpoint."""
        return self.is_running()

    def vision_capable(self) -> bool:
        """Probe whether this model can see images.

        Renders a tiny test image (a red square with the word "RED" in it)
        and asks the model what color it sees. Returns True only if the
        model's reply mentions the color — proving it actually processed the
        image, not just accepted the request and hallucinated. This is the
        human-centered check: when a user hits Ingest, the GUI calls this so
        it can alert them RIGHT THEN if their chat model can't read textbook
        pages and they need to pick a vision model.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# OpenAI-compatible client (OpenAI, OpenRouter, Gemini via proxy, vLLM, etc.)
# ---------------------------------------------------------------------------
class OpenAICompatibleClient(LLMClient):
    """Talks to any /v1/chat/completions endpoint.

    Streaming tool-calls are the fiddly part: OpenAI streams a tool call's
    `arguments` JSON as MANY fragments across deltas (each delta carries a
    sliver of the argument string, indexed by `tool_calls[i].index`). We
    accumulate per-index and only emit a complete tool_call once the stream
    finishes (finish_reason == "tool_calls"). This matches Ollama's
    one-shot tool_calls list that main.py expects.

    Reasoning tokens: OpenRouter exposes reasoning models with a `reasoning`
    field in the delta; we map it to `thinking` so the existing thinking-block
    renderer works unchanged.
    """

    def __init__(self,
                 base_url: str,
                 api_key: str,
                 llm_model: str = "",
                 session_logger: Any = None,
                 timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.llm_model = llm_model
        self.session_logger = session_logger
        self.timeout = timeout

    # -- internals ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _log(self, method: str, **kw: Any) -> None:
        if self.session_logger is None:
            return
        try:
            self.session_logger.log_tool_call(
                tool="llm_openai", method=method,
                inputs=kw.get("inputs"),
                outputs=kw.get("outputs"),
                duration_ms=kw.get("duration_ms"),
                error=kw.get("error"),
            )
        except Exception:
            pass

    # -- LLMClient surface -------------------------------------------------
    def set_model(self, model: str) -> None:
        self.llm_model = model
        if self.session_logger is not None:
            try:
                self.session_logger.log("model_changed", {"model": model})
            except Exception:
                pass

    def list_models(self) -> list[str]:
        """List model IDs from /v1/models. Returns [] on any failure."""
        try:
            r = requests.get(f"{self.base_url}/v1/models",
                             headers=self._headers(), timeout=10)
            r.raise_for_status()
            data = r.json().get("data", [])
            return [m.get("id", "") for m in data if m.get("id")]
        except Exception as e:
            self._log("list_models", error=str(e))
            return []

    # Known context windows (in tokens) for common OpenAI-compatible model
    # families. Used by context_window() when the API doesn't expose the
    # value (most OpenAI-compatible endpoints don't). Matched by substring
    # against the model id, most specific first. Values from official docs.
    _KNOWN_CONTEXT_WINDOWS: list[tuple[str, int]] = [
        # OpenAI
        ("gpt-4.1", 1048576),
        ("gpt-4o-mini", 128000),
        ("gpt-4o", 128000),
        ("gpt-4-turbo", 128000),
        ("gpt-4-", 8192),
        ("gpt-3.5-turbo", 16385),
        ("o3-mini", 200000),
        ("o3", 200000),
        ("o4-mini", 200000),
        ("o1-mini", 128000),
        ("o1", 200000),
        # Anthropic (via OpenRouter / direct compat)
        ("claude-opus-4", 200000),
        ("claude-sonnet-4", 200000),
        ("claude-3-7-sonnet", 200000),
        ("claude-3-5-sonnet", 200000),
        ("claude-3-5-haiku", 200000),
        ("claude-3-opus", 200000),
        ("claude-3-haiku", 200000),
        # Gemini (via OpenRouter / Google compat)
        ("gemini-2.5-pro", 1048576),
        ("gemini-2.5-flash", 1048576),
        ("gemini-2.0-flash", 1048576),
        ("gemini-1.5-pro", 2000000),
        ("gemini-1.5-flash", 1000000),
        # DeepSeek
        ("deepseek-chat", 64000),
        ("deepseek-reasoner", 64000),
        # Qwen (DashScope / OpenRouter)
        ("qwen3-", 131072),
        ("qwen2.5-", 131072),
        ("qwen-max", 32768),
        # Mistral
        ("mistral-large", 128000),
        ("mistral-medium", 32000),
        ("mistral-small", 32000),
        ("mixtral", 32000),
        # Llama
        ("llama-3.3", 128000),
        ("llama-3.2", 128000),
        ("llama-3.1", 128000),
        ("llama-3", 8192),
        # Default fallback
    ]

    def context_window(self, model: str | None = None) -> int:
        """Return the model's context-window size in tokens.

        OpenAI-compatible endpoints generally don't expose the context
        window via /v1/models, so we match the model id against a known-
        models table. Falls back to 32768 for unknown models so the UI
        meter always has a sane ceiling.
        """
        model = (model or self.llm_model or "").lower()
        if not model:
            return 32768
        for needle, ctx in self._KNOWN_CONTEXT_WINDOWS:
            if needle in model:
                return ctx
        return 32768

    def is_running(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/v1/models",
                             headers=self._headers(), timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def health_check(self) -> bool:
        return self.is_running()

    def vision_capable(self) -> bool:
        """Probe whether this model can see images (OpenAI-compatible path).

        Sends a tiny red test image as a data URL and asks what color it is.
        Returns True only if the reply mentions red — proving the model
        actually processed the image, not just accepted the request.

        Reasoning-model note: OpenRouter/o1-style models stream reasoning into
        a separate `reasoning` field and may spend the whole token budget
        reasoning before the answer lands in `content`. We bump max_tokens
        and check BOTH `content` and `reasoning` for "red" so a vision model
        that genuinely saw the red square isn't falsely reported as blind.
        """
        img_b64 = _test_image_base64()
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "What color is the square in this image? Reply with one word."},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ],
        }]
        try:
            r = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self._headers(),
                json={"model": self.llm_model, "messages": messages,
                      "temperature": 0.0, "max_tokens": 128},
                timeout=60,
            )
            if r.status_code != 200:
                return False
            data = r.json()
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message", {}) or {}
            content = (msg.get("content", "") or "").lower()
            reasoning = (msg.get("reasoning", "") or "").lower()
            return "red" in content or "red" in reasoning
        except Exception:
            return False

    # -- chat --------------------------------------------------------------
    def chat(self,
             messages: list[dict[str, Any]],
             tools: list[dict[str, Any]] | None = None,
             temperature: float = 0.7,
             stream: bool = False) -> Any:
        payload: dict[str, Any] = {
            "model": self.llm_model,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
        }
        # Ollama and OpenAI share the tool schema shape
        # {"type":"function","function":{"name","description","parameters"}},
        # so we pass tools through unchanged.
        if tools:
            payload["tools"] = tools

        t0 = time.time()
        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self._headers(), json=payload,
                stream=stream, timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as e:
            self._log("chat", inputs={"model": self.llm_model, "stream": stream},
                      error=str(e), duration_ms=(time.time() - t0) * 1000)
            raise

        if stream:
            return self._stream_chat(response, payload, t0)
        return self._nonstream_chat(response, payload, t0)

    def _nonstream_chat(self, response, payload, t0) -> dict[str, Any]:
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        # OpenAI returns tool_calls as a list with string arguments.
        tool_calls = msg.get("tool_calls") or []
        result = {
            "response": msg.get("content") or "",
            "thinking": msg.get("reasoning") or "",
            "tool_calls": tool_calls,
        }
        self._log("chat", inputs={"model": self.llm_model, "stream": False},
                  outputs={"tool_calls": len(tool_calls)},
                  duration_ms=(time.time() - t0) * 1000)
        return result

    def _stream_chat(self, response, payload, t0) -> Generator[dict[str, Any], None, None]:
        """Yield Ollama-shaped chunks from an OpenAI SSE stream.

        Accumulates fragmented tool-call argument strings per index and
        emits the complete tool_calls list on the terminal chunk, so the
        caller sees the same one-shot tool_calls that Ollama produces.
        """
        # Per-index accumulator: index -> {"id","name","arguments_str"}
        tc_acc: dict[int, dict[str, str]] = {}
        chunk_count = 0
        try:
            for raw in response.iter_lines():
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
                if not line.startswith("data:"):
                    continue
                body = line[len("data:"):].strip()
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
                    slot = tc_acc.setdefault(idx, {"id": "", "name": "", "arguments_str": ""})
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
                finish = choice.get("finish_reason")
                if finish in ("tool_calls", "stop", "length"):
                    # Emit accumulated tool calls as one chunk (Ollama-style).
                    if tc_acc:
                        assembled = []
                        for idx in sorted(tc_acc):
                            slot = tc_acc[idx]
                            assembled.append({
                                "id": slot["id"] or f"call_{idx}",
                                "function": {
                                    "name": slot["name"],
                                    "arguments": slot["arguments_str"] or "{}",
                                },
                            })
                        yield {"response": "", "thinking": "", "tool_calls": assembled}
                        chunk_count += 1
                    break
        except Exception as e:
            self._log("chat", inputs={"model": self.llm_model, "stream": True},
                      error=str(e), duration_ms=(time.time() - t0) * 1000)
            raise
        finally:
            self._log("chat", inputs={"model": self.llm_model, "stream": True},
                      outputs={"chunks": chunk_count, "tool_calls": len(tc_acc)},
                      duration_ms=(time.time() - t0) * 1000)
        # Terminal done marker (matches Ollama's contract).
        yield {"done": True}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def get_llm_client(session_logger: Any = None) -> LLMClient:
    """Build the synthesis LLM client from .env.

    Priority:
      1. LLM_BACKEND explicitly set -> honor it ("ollama" | "openai").
      2. Legacy: OLLAMA_LLM_MODEL set + no LLM_BACKEND -> Ollama (back-compat).
      3. Default -> Ollama (free, zero-config, the original behavior).
    """
    backend = (os.getenv("LLM_BACKEND") or "").strip().lower()

    if backend == "openai":
        base_url = (os.getenv("LLM_BASE_URL") or "https://api.openai.com").strip()
        api_key = (os.getenv("LLM_API_KEY") or "").strip()
        model = (os.getenv("LLM_MODEL") or "").strip()
        if not api_key:
            raise RuntimeError(
                "LLM_BACKEND=openai but LLM_API_KEY is not set. "
                "Add LLM_API_KEY=sk-... to your .env (or pick LLM_BACKEND=ollama)."
            )
        if not model:
            raise RuntimeError(
                "LLM_BACKEND=openai but LLM_MODEL is not set. "
                "Add LLM_MODEL=gpt-4o-mini (or your endpoint's model id) to .env."
            )
        return OpenAICompatibleClient(
            base_url=base_url, api_key=api_key, llm_model=model,
            session_logger=session_logger,
        )

    # Default / "ollama" / legacy: use the existing OllamaClient, which
    # already implements the LLMClient surface (chat, list_models, set_model,
    # is_running). Imported lazily so an OpenAI-only install never loads the
    # Ollama requests surface unnecessarily.
    from ollama_client import OllamaClient
    return OllamaClient(
        base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        llm_model=os.getenv("OLLAMA_LLM_MODEL", ""),
        embed_model=os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
        session_logger=session_logger,
    )


def get_vision_client(session_logger: Any = None) -> LLMClient | None:
    """Build the OPTIONAL vision LLM client from .env.

    The vision client is a SEPARATE concern from the synthesis (chat) client:
    it is used ONLY to read rendered textbook pages (textbook_read_page) when
    the chat model is text-only. A user keeps their fast/cheap chat model and
    delegates page-reading to a vision-capable model on its own backend.

    Settings (all optional; if any required piece is missing, returns None
    and the caller falls back to the synthesis client or the text layer):
      VISION_BACKEND  : "ollama" | "openai"  (defaults to the synthesis backend)
      VISION_BASE_URL  : OpenAI-compatible endpoint (openai path)
      VISION_API_KEY   : bearer token (openai path)
      VISION_MODEL     : model id (openai path) OR Ollama model name
      VISION_OLLAMA_HOST: Ollama host if the vision model lives on a different
                         Ollama daemon than the chat model (ollama path)

    Resolution rules:
      - If VISION_MODEL is unset -> return None (no dedicated vision model;
        callers fall back to the synthesis client's own vision_capable()).
      - If VISION_BACKEND == "openai" -> OpenAICompatibleClient using
        VISION_BASE_URL / VISION_API_KEY / VISION_MODEL. Missing api_key or
        base_url -> None (not a hard error; just no vision).
      - If VISION_BACKEND == "ollama" (or unset with VISION_MODEL set) ->
        OllamaClient pointed at VISION_OLLAMA_HOST (or the same OLLAMA_HOST as
        chat) with VISION_MODEL as the model.
    """
    model = (os.getenv("VISION_MODEL") or "").strip()
    if not model:
        return None

    backend = (os.getenv("VISION_BACKEND") or "").strip().lower()
    # If the vision backend isn't explicitly set, mirror the synthesis backend
    # so a user who only sets VISION_MODEL gets sensible behavior.
    if not backend:
        backend = (os.getenv("LLM_BACKEND") or "").strip().lower()

    if backend == "openai":
        base_url = (os.getenv("VISION_BASE_URL") or os.getenv("LLM_BASE_URL")
                    or "https://api.openai.com").strip()
        api_key = (os.getenv("VISION_API_KEY") or os.getenv("LLM_API_KEY")
                   or "").strip()
        if not api_key:
            return None
        return OpenAICompatibleClient(
            base_url=base_url, api_key=api_key, llm_model=model,
            session_logger=session_logger,
        )

    # Ollama path (default).
    from ollama_client import OllamaClient
    host = (os.getenv("VISION_OLLAMA_HOST") or os.getenv("OLLAMA_HOST")
            or "http://localhost:11434")
    return OllamaClient(
        base_url=host,
        llm_model=model,
        embed_model=os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
        session_logger=session_logger,
    )
