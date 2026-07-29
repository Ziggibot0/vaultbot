import json
import subprocess
import time
from collections.abc import Generator
from typing import Any

import requests

try:
    # The synthesis LLM abstraction (llm_client.py) treats OllamaClient as
    # one of two interchangeable backends (the other is OpenAICompatibleClient).
    # Importing the base is optional so ollama_client stays usable standalone.
    from llm_client import LLMClient
    _BASE = LLMClient
except Exception:  # pragma: no cover - circular-import safety
    _BASE = object

class OllamaClient(_BASE):
    def __init__(self, base_url: str = "http://localhost:11434", llm_model: str = "qwen3.6:latest", embed_model: str = "nomic-embed-text", session_logger=None):
        self.base_url = base_url
        self.llm_model = llm_model
        self.embed_model = embed_model
        self.session_logger = session_logger
        # Reuse a single requests.Session across all calls so HTTP keep-alive
        # can pool the TCP connection to the local Ollama daemon.  Embedding
        # batches (8 concurrent) and the streaming chat loop no longer pay a
        # fresh connection handshake per request.
        self._session = requests.Session()

    def set_model(self, model: str) -> None:
        """Switch the active LLM model at runtime."""
        self.llm_model = model
        if self.session_logger is not None:
            self.session_logger.log("model_changed", {"model": model})

    def list_local_models(self) -> list[str]:
        """Return model names installed in the local Ollama daemon via `ollama list`."""
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
            if result.returncode != 0:
                # Fallback to the API if the CLI is unavailable
                resp = self._session.get(f"{self.base_url}/api/tags", timeout=5)
                resp.raise_for_status()
                return [m["name"] for m in resp.json().get("models", [])]
            models = []
            for line in result.stdout.strip().splitlines()[1:]:
                name = line.split()[0] if line.strip() else ""
                if name:
                    models.append(name)
            return models
        except Exception as e:
            self._log_tool("list_local_models", {}, error=str(e))
            return []

    # Backend-agnostic alias used by llm_client.LLMClient and the /models
    # endpoint. Same as list_local_models; the alias lets /models call
    # .list_models() uniformly across Ollama and OpenAI-compatible backends.
    def list_models(self) -> list[str]:
        return self.list_local_models()

    def context_window(self, model: str | None = None) -> int:
        """Return the model's native context-window size in tokens.

        Queries Ollama's /api/show for the model and extracts the
        architecture-prefixed ``*.context_length`` field from ``model_info``
        (e.g. ``qwen35moe.context_length``, ``glm5.2.context_length``).
        Works for both local and cloud (``:cloud``) Ollama models — both
        return the same show metadata shape. Falls back to 32768 on any
        failure so the UI always has a sane meter ceiling.
        """
        model = model or self.llm_model
        if not model:
            return 32768
        try:
            resp = self._session.post(
                f"{self.base_url}/api/show",
                json={"model": model}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            info = data.get("model_info") or {}
            # The key is "<arch>.context_length" — find it generically.
            for key, val in info.items():
                if key.endswith(".context_length") and isinstance(val, (int, float)):
                    return int(val)
            # Some older Ollama builds expose it under parameters as a
            # "num_ctx" string like "262144".
            params = data.get("parameters") or ""
            if isinstance(params, str):
                for tok in params.split():
                    if tok.startswith("num_ctx"):
                        # "num_ctx" or "num_ctx:262144"
                        if ":" in tok:
                            return int(tok.split(":")[-1])
            return 32768
        except Exception as e:
            self._log_tool("context_window", {"model": model}, error=str(e))
            return 32768

    def _log_tool(self, method: str, inputs: dict[str, Any], outputs: Any = None, duration_ms: float | None = None, error: str | None = None):
        if self.session_logger is None:
            return
        self.session_logger.log_tool_call(tool="ollama", method=method, inputs=inputs, outputs=outputs, duration_ms=duration_ms, error=error)

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
            {"role": m.get("role", "?"),
             "chars": len(str(m.get("content", "") or "")),
             "thinking_chars": len(str(m.get("thinking", "") or "")),
             "has_tool_calls": bool(m.get("tool_calls"))}
            for m in msgs if isinstance(m, dict)
        ]
        return {
            "model": payload.get("model", ""),
            "stream": stream,
            "message_count": len(msgs),
            "total_content_chars": sum(m["chars"] for m in msg_summary),
            "messages": msg_summary,
            "has_tools": bool(payload.get("tools")),
        }

    def generate(self, prompt: str, system: str | None = None, temperature: float = 0.7, max_tokens: int | None = None, stream: bool = False) -> dict | Generator:
        """
        Generate text from the LLM.
        If stream=True, returns a generator that yields chunks.
        Each chunk is a dict with keys: 'response' (the text chunk) and optionally 'thinking' (the reasoning chunk).
        """
        payload = {
            "model": self.llm_model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": temperature,
            }
        }
        if system:
            payload["system"] = system
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens

        t0 = time.time()
        try:
            response = self._session.post(f"{self.base_url}/api/generate", json=payload, stream=stream)
            response.raise_for_status()
        except Exception as e:
            self._log_tool("generate", {"payload": payload, "stream": stream}, error=str(e), duration_ms=(time.time() - t0) * 1000)
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
                                "thinking": data.get("thinking", "")
                            }
                            yield chunk
                            chunk_count += 1
                            if data.get("done", False):
                                break
                except Exception as e:
                    self._log_tool("generate", {"payload": payload, "stream": stream}, error=str(e), duration_ms=(time.time() - t0) * 1000)
                    raise
                finally:
                    self._log_tool("generate", {"payload": payload, "stream": stream}, outputs={"chunks": chunk_count}, duration_ms=(time.time() - t0) * 1000)
            return generate_chunks()
        else:
            data = response.json()
            result = {
                "response": data.get("response", ""),
                "thinking": data.get("thinking", "")
            }
            self._log_tool("generate", {"payload": payload, "stream": stream}, outputs=result, duration_ms=(time.time() - t0) * 1000)
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
        payload = {
            "model": self.embed_model,
            "prompt": text
        }
        t0 = time.time()
        try:
            response = self._session.post(f"{self.base_url}/api/embeddings", json=payload)
            response.raise_for_status()
            data = response.json()
            embedding = data["embedding"]
            self._log_tool("embeddings", {"model": self.embed_model, "truncated": truncated, "text_length": len(payload["prompt"])}, outputs={"embedding_length": len(embedding)}, duration_ms=(time.time() - t0) * 1000)
            return embedding
        except Exception as e:
            self._log_tool("embeddings", {"model": self.embed_model, "truncated": truncated, "text_length": len(payload["prompt"])}, error=str(e), duration_ms=(time.time() - t0) * 1000)
            raise

    def batch_embeddings(self, texts: list[str], max_workers: int = 8) -> list[list[float] | None]:
        """Get embeddings for multiple texts in parallel via ThreadPoolExecutor.

        Ollama's embedding endpoint is stateless and thread-safe — concurrent
        requests are handled by the Ollama server's internal queue.  This cuts
        a 282-note weave from ~282 sequential round-trips to ~36 batches of 8,
        roughly an 8x speedup.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: list[list[float] | None] = [None] * len(texts)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self.embeddings, t): i for i, t in enumerate(texts)}
            for future in as_completed(futures):
                i = futures[future]
                try:
                    results[i] = future.result()
                except Exception:
                    results[i] = None
        return results

    def is_running(self) -> bool:
        """Check if the Ollama server is running."""
        try:
            response = self._session.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except:
            return False

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
            "messages": [{
                "role": "user",
                "content": "What color is the square in this image? Reply with one word.",
                "images": [img_b64],
            }],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "num_predict": 64},
        }
        try:
            r = self._session.post(f"{self.base_url}/api/chat",
                              json=payload, timeout=60)
            if r.status_code != 200:
                return False
            msg = r.json().get("message", {}) or {}
            content = (msg.get("content", "") or "").lower()
            thinking = (msg.get("thinking", "") or "").lower()
            return "red" in content or "red" in thinking
        except Exception:
            return False

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             temperature: float = 0.7, stream: bool = False) -> dict | Generator:
        """
        Multi-turn chat completion via /api/chat, with optional tool-calling.

        Supports the Ollama tool-calling protocol: when `tools` is provided,
        the model may emit `tool_calls` in its response instead of (or alongside)
        content. Each tool call has a name + arguments the caller must execute
        and feed back as a `tool`-role message.

        If stream=True, returns a generator yielding chunks with keys:
          'response' (text chunk), 'thinking' (reasoning chunk),
          'tool_calls' (list of tool call dicts, or []).
        If stream=False, returns a dict with 'response', 'thinking', 'tool_calls'.
        """
        payload = {
            "model": self.llm_model,
            "messages": messages,
            "stream": stream,
            "options": {"temperature": temperature},
        }
        if tools:
            payload["tools"] = tools

        t0 = time.time()
        try:
            # Streaming chat: a read timeout so a stalled cloud model
            # (glm-5.2:cloud is a remote ollama.com model) raises
            # ReadTimeout instead of blocking the chat loop forever. The
            # connect timeout is short (the server is local); the read
            # timeout is generous (120s) so a slow-but-alive stream isn't
            # killed. The non-stream path already uses timeout=60.
            if stream:
                response = self._session.post(
                    f"{self.base_url}/api/chat", json=payload,
                    stream=True, timeout=(5, 120))
            else:
                response = self._session.post(
                    f"{self.base_url}/api/chat", json=payload,
                    stream=False, timeout=60)
            response.raise_for_status()
        except Exception as e:
            self._log_tool("chat", self._chat_log_summary(payload, stream),
                            error=str(e), duration_ms=(time.time() - t0) * 1000)
            raise

        if stream:
            def chat_chunks():
                chunk_count = 0
                accumulated_tool_calls = []
                try:
                    for line in response.iter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        msg = data.get("message", {})
                        chunk = {
                            "response": msg.get("content", "") or "",
                            "thinking": msg.get("thinking", "") or "",
                            "tool_calls": msg.get("tool_calls", []) or [],
                        }
                        if chunk["tool_calls"]:
                            accumulated_tool_calls.extend(chunk["tool_calls"])
                        yield chunk
                        chunk_count += 1
                        if data.get("done", False):
                            break
                except Exception as e:
                    self._log_tool("chat", self._chat_log_summary(payload, stream),
                                    error=str(e), duration_ms=(time.time() - t0) * 1000)
                    raise
                finally:
                    self._log_tool("chat", self._chat_log_summary(payload, stream),
                                    outputs={"chunks": chunk_count,
                                             "tool_calls": len(accumulated_tool_calls)},
                                    duration_ms=(time.time() - t0) * 1000)
            return chat_chunks()
        else:
            data = response.json()
            msg = data.get("message", {})
            result = {
                "response": msg.get("content", "") or "",
                "thinking": msg.get("thinking", "") or "",
                "tool_calls": msg.get("tool_calls", []) or [],
            }
            self._log_tool("chat", self._chat_log_summary(payload, stream),
                            outputs={"response_len": len(result["response"]),
                                     "thinking_len": len(result["thinking"]),
                                     "tool_calls": len(result["tool_calls"])},
                            duration_ms=(time.time() - t0) * 1000)
            return result
