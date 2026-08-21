"""Speech endpoints: POST /stt, POST /tts, GET /tts/voices.

These let the Obsidian plugin do real-time voice conversations using ANY
STT/TTS provider the user configured (OpenAI, Groq Whisper, Edge TTS, a
local Whisper server, the browser). The plugin's Call button records audio
in-browser (microphone access must be in the browser) and POSTs it here for
STT; TTS audio is streamed back to the plugin and played.

The plugin streams TTS in chunks: it calls /tts per sentence so the voice
starts before the whole turn is done (the JARVIS feel). Each /tts call
returns audio bytes the plugin plays immediately.
"""

from __future__ import annotations

import logging
from typing import Annotated

import speech as speech_mod
from app_state import get_services
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from services import Services

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/stt")
async def stt_endpoint(
    request: Request,
    svc: Annotated[Services, Depends(get_services)],
):
    """Transcribe audio bytes → text.

    The raw request body is the audio (audio/webm from MediaRecorder, or any
    format the configured STT provider accepts). Returns {"text": "..."} on
    success, {"browser": true} if STT is browser-handled, or {"error": ...}.
    """
    audio = await request.body()
    if not audio:
        return {"error": "no audio body"}
    content_type = request.headers.get("content-type", "audio/webm")
    # Pick a filename extension from the content type for the multipart upload.
    ext = "webm"
    if "ogg" in content_type or "opus" in content_type:
        ext = "ogg"
    elif "wav" in content_type:
        ext = "wav"
    elif "mp3" in content_type:
        ext = "mp3"
    filename = f"audio.{ext}"
    result = speech_mod.transcribe(svc, audio, filename=filename)
    return result


@router.post("/tts")
async def tts_endpoint(
    request: Request,
    svc: Annotated[Services, Depends(get_services)],
):
    """Synthesize text → audio bytes.

    Body: {"text": "...", "voice": "optional override"}.
    Returns the raw audio bytes with the right Content-Type, or JSON
    {"browser": true} if TTS is browser-handled (plugin uses speechSynthesis),
    or {"error": ...}.
    """
    try:
        payload = await request.json()
    except Exception:
        # Tolerate a raw text body too.
        body = await request.body()
        payload = {"text": body.decode("utf-8", "replace")}
    text = (payload.get("text") or "").strip()
    result = await speech_mod.synthesize(svc, text)
    if result.get("browser"):
        return result
    if "error" in result:
        return result
    audio = result.get("audio", b"")
    ct = result.get("content_type", "audio/mpeg")
    return Response(content=audio, media_type=ct)


@router.get("/tts/voices")
async def tts_voices_endpoint(
    svc: Annotated[Services, Depends(get_services)],
):
    """Return available TTS voices for the configured tts provider.

    For edge-tts this is the full voice list; for openai-compatible the known
    OpenAI voices; for browser an empty list (the plugin enumerates browser
    voices). The settings dropdown uses this to populate the picker.
    """
    return await speech_mod.list_tts_voices(svc)
