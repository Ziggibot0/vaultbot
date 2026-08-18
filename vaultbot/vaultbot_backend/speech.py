"""Speech backend — STT (transcription) + TTS (synthesis), any provider.

This is the speech equivalent of ``llm_client.py``: it resolves the stt/tts
role to a model entry in the provider pot, looks at the provider's type +
base_url + api_key, and hits the right endpoint:

  - ``openai`` providers (OpenAI, Groq, any OpenAI-compatible endpoint):
      STT → POST {base}/v1/audio/transcriptions  (multipart: audio file + model)
      TTS → POST {base}/v1/audio/speech          (JSON: model, input, voice)
  - ``edge-tts`` (free, no key): edge-tts websocket relay. Voice = model name.
  - ``browser``: not a server path — the frontend handles browser STT/TTS
    directly; this module returns a sentinel so the plugin knows to fall back.

The plugin's Call button uses the browser for STT (microphone access must
happen in the browser) but can use the server for TTS so the voice is a
real, configurable voice (Edge TTS / OpenAI) rather than the browser's
limited built-in voices. STT can also be server-side if the browser
uploads the recording (POST /stt with the audio body).

Why server-side at all (not pure browser): the user wants to pick ANY TTS/STT
model from ANY provider, like the LLMs — OpenAI voices, Groq Whisper, a
local Whisper server, Edge TTS. The browser can only do its own built-in
voices. So TTS is server-side by default; the browser path is a fallback.

No DLL blocks here: edge-tts is pure-Python (websocket); OpenAI-compatible
STT/TTS is plain HTTP via httpx/requests. The previous faster-whisper/PyAV
stack (blocked by WDAC) is NOT used.
"""

from __future__ import annotations

import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

# A sentinel returned to the plugin when the role points at the browser
# provider — tells the frontend to use the Web Speech API instead of a
# server round-trip. Kept as a module constant so the router + plugin agree.
BROWSER_SENTINEL = {"browser": True}


def _resolve(svc, role: str):
    """Return (ModelEntry, Provider) for a speech role, or (None, None)."""
    reg = getattr(svc, "registry", None)
    if reg is None:
        from providers import ProviderRegistry
        reg = ProviderRegistry.migrate_from_env()
        svc.registry = reg
    mid = reg.get_role(role)
    if not mid:
        return None, None
    entry = reg.get_model(mid)
    if entry is None:
        return None, None
    prov = reg.get_provider(entry.provider)
    return entry, prov


def is_browser_role(svc, role: str) -> bool:
    """True if the role points at the browser provider (frontend handles it)."""
    _, prov = _resolve(svc, role)
    return prov is not None and prov.type == "browser"


# ---------------------------------------------------------------------------
# STT — transcribe audio bytes to text
# ---------------------------------------------------------------------------
def transcribe(svc, audio_bytes: bytes, filename: str = "audio.webm") -> dict[str, Any]:
    """Transcribe ``audio_bytes`` using the stt role's configured provider.

    Returns {"text": str} on success, {"error": str, "browser": bool} on
    failure or when the role is browser-handled.
    """
    entry, prov = _resolve(svc, "stt")
    if entry is None or prov is None:
        return {"error": "No STT model configured. Pick one in Settings → Speech Models."}
    if prov.type == "browser":
        return {**BROWSER_SENTINEL, "error": "STT is browser-handled; use the in-browser recognizer."}
    if prov.type == "openai":
        return _transcribe_openai(prov, entry, audio_bytes, filename)
    return {"error": f"Provider type '{prov.type}' does not support STT."}


def _transcribe_openai(prov, entry, audio_bytes: bytes, filename: str) -> dict[str, Any]:
    import requests
    base = prov.base_url.rstrip("/")
    url = f"{base}/v1/audio/transcriptions"
    headers = {}
    if prov.api_key:
        headers["Authorization"] = f"Bearer {prov.api_key}"
    files = {"file": (filename, audio_bytes)}
    data = {"model": entry.model}
    try:
        r = requests.post(url, headers=headers, files=files, data=data, timeout=120)
        if r.status_code in (401, 403):
            return {"error": f"auth rejected (check the API key) — {r.status_code}"}
        if r.status_code == 404:
            return {"error": f"no /v1/audio/transcriptions at {base} (status 404). Does this endpoint support speech?"}
        r.raise_for_status()
        text = r.json().get("text", "").strip()
        return {"text": text}
    except Exception as e:  # noqa: BLE001
        logger.warning("STT openai failed: %s", e)
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# TTS — synthesize text to audio bytes (WAV/MP3)
# ---------------------------------------------------------------------------
async def synthesize(svc, text: str) -> dict[str, Any]:
    """Synthesize ``text`` using the tts role's configured provider.

    Returns {"audio": bytes, "content_type": str} on success,
    {"browser": True} if browser-handled, or {"error": str} on failure.
    """
    entry, prov = _resolve(svc, "tts")
    if entry is None or prov is None:
        return {"error": "No TTS model configured. Pick one in Settings → Speech Models."}
    if not text.strip():
        return {"error": "empty text"}
    if prov.type == "browser":
        return BROWSER_SENTINEL
    if prov.type == "edge-tts":
        return await _synthesize_edge_tts(entry, text)
    if prov.type == "openai":
        return await _synthesize_openai(prov, entry, text)
    return {"error": f"Provider type '{prov.type}' does not support TTS."}


async def _synthesize_edge_tts(entry, text: str) -> dict[str, Any]:
    """Free Microsoft Edge TTS via the edge-tts websocket relay."""
    try:
        import edge_tts
        voice = entry.model  # e.g. "en-US-GuyNeural"
        communicate = edge_tts.Communicate(text, voice)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                buf.write(chunk["data"])
        audio = buf.getvalue()
        if not audio:
            return {"error": "edge-tts produced no audio (check the voice name)"}
        return {"audio": audio, "content_type": "audio/mpeg"}
    except Exception as e:  # noqa: BLE001
        logger.warning("TTS edge-tts failed: %s", e)
        return {"error": f"{type(e).__name__}: {e}"}


async def _synthesize_openai(prov, entry, text: str) -> dict[str, Any]:
    """OpenAI-compatible /v1/audio/speech."""
    import httpx
    base = prov.base_url.rstrip("/")
    url = f"{base}/v1/audio/speech"
    headers = {"Content-Type": "application/json"}
    if prov.api_key:
        headers["Authorization"] = f"Bearer {prov.api_key}"
    # The model field is the provider's voice/model id. For OpenAI TTS the
    # "voice" is separate from "model" — we use entry.model as the model and
    # entry.label (or a default) as the voice if the entry looks like a
    # voice name. Heuristic: if the model id contains a known voice token
    # (alloy/echo/fable/onyx/nova/shimmer), treat it as both model=tts-1 + voice.
    model = entry.model
    payload: dict[str, Any] = {"model": model, "input": text}
    # If the model id looks like a voice name, set model=tts-1 + voice=model.
    _TTS_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer", "coral", "sage", "ash", "ballad")
    if model.lower() in _TTS_VOICES:
        payload["model"] = "tts-1"
        payload["voice"] = model
    elif entry.label and entry.label.lower() in _TTS_VOICES:
        payload["voice"] = entry.label.lower()
    payload["response_format"] = "mp3"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(url, headers=headers, json=payload)
        if r.status_code in (401, 403):
            return {"error": f"auth rejected (check the API key) — {r.status_code}"}
        if r.status_code == 404:
            return {"error": f"no /v1/audio/speech at {base} (status 404). Does this endpoint support TTS?"}
        r.raise_for_status()
        ct = r.headers.get("content-type", "audio/mpeg")
        return {"audio": r.content, "content_type": ct}
    except Exception as e:  # noqa: BLE001
        logger.warning("TTS openai failed: %s", e)
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Voice listing — for the dropdown
# ---------------------------------------------------------------------------
async def list_tts_voices(svc) -> dict[str, Any]:
    """Return available TTS voices for the configured tts provider.

    For edge-tts this is the full edge-tts voice list (so the user can pick
    any voice). For openai-compatible, return the known OpenAI voice set.
    For browser, return an empty list (the frontend enumerates browser voices).
    """
    entry, prov = _resolve(svc, "tts")
    if prov is None:
        return {"voices": [], "error": "No TTS provider configured."}
    if prov.type == "edge-tts":
        try:
            import edge_tts
            voices = await edge_tts.list_voices()
            return {"voices": [{"id": v["ShortName"], "label": v["FriendlyName"], "lang": v.get("Locale", "")} for v in voices], "provider_type": "edge-tts"}
        except Exception as e:  # noqa: BLE001
            return {"voices": [], "error": str(e)}
    if prov.type == "browser":
        return {"voices": [], "provider_type": "browser"}
    if prov.type == "openai":
        # OpenAI's /v1/models doesn't list voices; return the known set so the
        # user can pick one (it becomes the model id for that role).
        _V = ["alloy", "echo", "fable", "onyx", "nova", "shimmer", "coral", "sage", "ash", "ballad"]
        return {"voices": [{"id": v, "label": v, "lang": ""} for v in _V], "provider_type": "openai"}
    return {"voices": []}