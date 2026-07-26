"""Voice endpoints: local STT + TTS.

Migrated from main.py. These are stateless leaf-function calls via
run_in_executor; no svc fields are needed (but taken via Depends for
consistency + future use).
"""
from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from app_state import get_services
from services import Services
from speech import transcribe as stt_transcribe
from speech import synthesize as tts_synthesize
from speech import list_voices as tts_voices

router = APIRouter()


@router.post("/stt")
async def stt_endpoint(request: Request,
                       svc: Annotated[Services, Depends(get_services)]) -> dict[str, str]:
    """Transcribe a raw audio upload (webm/wav/ogg from MediaRecorder).

    Body = raw audio bytes. Content-Type is honored to pick the decoder.
    Returns {text: "..."}. On any failure returns {text: "", error: "..."}
    so the caller can degrade gracefully.
    """
    mime = request.headers.get("content-type", "audio/webm")
    body = await request.body()
    if not body:
        return {"text": "", "error": "empty body"}
    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(None, stt_transcribe, body, mime)
    return {"text": text}


@router.post("/tts")
async def tts_endpoint(request: Request,
                       svc: Annotated[Services, Depends(get_services)]):
    """Synthesize text to a WAV. Body = JSON {text, voice?, rate?}.

    Returns audio/wav bytes (or 204 if text is empty). Used as a server-side
    fallback when the browser's speechSynthesis isn't available.
    """
    try:
        payload = await request.json()
    except Exception:
        return {"error": "invalid json"}, 400
    text = (payload.get("text") or "").strip()
    if not text:
        return b"", 204
    voice = payload.get("voice")
    rate = int(payload.get("rate", 190))
    loop = asyncio.get_event_loop()
    wav = await loop.run_in_executor(None, tts_synthesize, text, voice, rate)
    if not wav:
        return {"error": "synthesis failed"}, 500
    return Response(content=wav, media_type="audio/wav")


@router.get("/voices")
async def voices_endpoint(svc: Annotated[Services, Depends(get_services)]) -> dict[str, Any]:
    """List available local TTS voices (SAPI on Windows)."""
    loop = asyncio.get_event_loop()
    voices = await loop.run_in_executor(None, tts_voices)
    return {"voices": voices}