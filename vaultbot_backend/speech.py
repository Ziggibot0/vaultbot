"""
Local speech (STT + TTS) for VaultBot — no cloud API keys, no per-call cost.

STT: Vosk (offline, Kaldi-based). The small English model (~40MB) is
auto-downloaded to stt_models/ on first use so "just works" out of the
box. Runs on CPU and transcribes short call-style utterances in well
under a second on this machine. A `speech_recognition` Google-endpoint
fallback covers any vosk import failure.
TTS: Kokoro v1.0 (82M params, ONNX) — natural expressive voices that beat
older neural TTS like Piper. Runs on CPU (RTF ~0.15 on this machine) so it
stays out of the way of the NPU/GPU. The "am_michael" voice is the deep,
calm default (JARVIS-like); switchable at runtime via the plugin settings.

Both run on the backend so the Obsidian plugin (Electron) doesn't have to
ship a speech engine — it just records audio (MediaRecorder) and plays a
WAV back. The browser's own `speechSynthesis` remains a fast client-side
fallback when streaming is preferred.

NPU note: this machine has an AMD Ryzen AI NPU exposed via DirectML.
DirectML crashes on Kokoro's ConvTranspose op, and CPU is already
sub-real-time (RTF 0.15), so TTS/STT run on CPU by design — the NPU is
left free for whatever else the laptop is doing.

STT history: this module previously used faster-whisper (Whisper on
CTranslate2). That path imports PyAV (`av`), and on this machine a
Windows Application Control (WDAC) policy blocks one of av's Cython
extensions (av/filter/link.pyd) by hash — so `import av` and therefore
`import faster_whisper` both fail with "DLL load failed... An Application
Control policy has blocked this file." Vosk has no such dependency and
works offline, so it is the STT engine here.
"""

import io
import os
import sys
import json
import wave
import zipfile
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Optional

# Model files live next to this module so they travel with the vault.
HERE = Path(__file__).parent
KOKORO_MODEL = HERE / "kokoro_models" / "kokoro-v1.0.onnx"
KOKORO_VOICES = HERE / "kokoro_models" / "voices-v1.0.bin"

# Where the vosk model lives. Kept inside the backend dir so it travels
# with the vault and survives venv rebuilds.
VOSK_MODEL_DIR = HERE / "stt_models"
# Use the 0.15 small model — the 0.22 "small" archive is no longer hosted
# at the canonical URL (404 as of July 2026). 0.15 is the documented
# lightweight US English model and is reachable.
VOSK_SMALL_MODEL_NAME = "vosk-model-small-en-us-0.15"
VOSK_SMALL_MODEL_URL = (
    "https://alphacephei.com/vosk/models/" + VOSK_SMALL_MODEL_NAME + ".zip"
)

# The default voice. "am_michael" is a deep, calm American male — the
# closest Kokoro voice to a JARVIS assistant. The plugin settings can
# override this (full list at /voices).
DEFAULT_VOICE = "am_michael"

# Lazy singletons — heavy objects load once and are reused across calls.
_kokoro = None
_kokoro_lock = threading.Lock()
_vosk_model = None
_vosk_model_lock = threading.Lock()

_session_logger = None


def set_logger(logger):
    global _session_logger
    _session_logger = logger


def _log(event: str, data: dict) -> None:
    if _session_logger is not None:
        try:
            _session_logger.log(event, data)
        except Exception:
            pass


# ─── TTS: Kokoro v1.0 ────────────────────────────────────────────────────────

def _get_kokoro():
    """Lazily load the Kokoro ONNX model. Returns None if unavailable."""
    global _kokoro
    if _kokoro is not None:
        return _kokoro
    with _kokoro_lock:
        if _kokoro is not None:
            return _kokoro
        if not KOKORO_MODEL.exists() or not KOKORO_VOICES.exists():
            _log("tts_kokoro_missing", {"model": str(KOKORO_MODEL),
                                         "voices": str(KOKORO_VOICES)})
            return None
        try:
            # Force CPUExecutionProvider. onnxruntime-directml is installed
            # (for the NPU), but Kokoro's ConvTranspose op crashes the DML
            # EP. CPU is sub-real-time (RTF 0.15) so there's no speed loss,
            # and this keeps the NPU free for other workloads.
            import onnxruntime as rt
            from kokoro_onnx import Kokoro
            sess = rt.InferenceSession(
                str(KOKORO_MODEL), providers=["CPUExecutionProvider"]
            )
            _kokoro = Kokoro.from_session(sess, str(KOKORO_VOICES))
            _log("tts_kokoro_loaded", {"model": KOKORO_MODEL.name, "ep": "cpu"})
            return _kokoro
        except Exception as e:
            _log("tts_kokoro_load_failed", {"error": str(e)})
            return None


def synthesize(text: str, voice: Optional[str] = None, rate: int = 190) -> bytes:
    """Synthesize text to WAV bytes (16-bit PCM, 24kHz).

    `voice` selects a Kokoro voice (e.g. "am_michael", "af_sarah").
    `rate` maps words/min → Kokoro speed (1.0 at 190 wpm).
    Returns b"" on failure.
    """
    if not text or not text.strip():
        return b""
    k = _get_kokoro()
    if k is None:
        _log("tts_kokoro_unavailable", {})
        return b""
    v = (voice or DEFAULT_VOICE).strip()
    # Map the wpm-style rate to a Kokoro speed multiplier. 190 wpm ≈ 1.0x.
    speed = max(0.5, min(2.0, float(rate) / 190.0)) if rate else 1.0
    _log("tts_request", {"chars": len(text), "voice": v, "speed": round(speed, 2)})
    try:
        import numpy as np
        import soundfile as sf
        samples, sr = k.create(text, voice=v, speed=speed, lang="en-us")
        # Convert float32 [-1,1] to int16 PCM WAV bytes for the browser.
        buf = io.BytesIO()
        sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
        data = buf.getvalue()
        _log("tts_ok", {"bytes": len(data), "duration_s": round(len(samples) / sr, 2)})
        return data
    except Exception as e:
        _log("tts_failed", {"error": str(e)})
        return b""


def list_voices() -> list:
    """Return Kokoro voice names + a JARVIS-recommendation flag."""
    k = _get_kokoro()
    if k is None:
        return []
    out = []
    # Kokoro voices encode gender/accent: am_* = American male, af_* female,
    # bm_* = British male, bf_* British female. Surface all, mark the
    # recommended JARVIS-like male voices.
    jarvis_voices = {"am_michael", "am_onyx", "am_echo", "bm_george", "bm_daniel"}
    for name in k.get_voices():
        out.append({
            "id": name,
            "name": name,
            "recommended": name in jarvis_voices,
        })
    return out


# ─── STT: Vosk (offline Kaldi) ───────────────────────────────────────────────

def _vosk_model_root() -> Path:
    """Return the path to the extracted vosk model directory (creates parent)."""
    VOSK_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    # The zip extracts to a folder named <VOSK_SMALL_MODEL_NAME>; look for it.
    for child in VOSK_MODEL_DIR.iterdir():
        if child.is_dir() and child.name.startswith("vosk-model-small-en-us"):
            return child
    return VOSK_MODEL_DIR / VOSK_SMALL_MODEL_NAME


def _download_vosk_model() -> Optional[Path]:
    """Download + extract the small vosk model if not already present.

    Returns the model dir path on success, None on failure. Safe to call
    concurrently — the lock serializes downloaders.
    """
    root = _vosk_model_root()
    # A model dir is "ready" if it contains the expected files.
    if (root / "am" / "final.mdl").exists() or (root / "conf").exists():
        return root
    with _vosk_model_lock:
        # Re-check inside the lock — another thread may have just installed it.
        root = _vosk_model_root()
        if (root / "am" / "final.mdl").exists() or (root / "conf").exists():
            return root
        _log("stt_model_download_start", {"url": VOSK_SMALL_MODEL_URL})
        try:
            tmp_zip = VOSK_MODEL_DIR / (VOSK_SMALL_MODEL_NAME + ".zip")
            # Stream to disk so a 40MB download doesn't sit in RAM.
            req = urllib.request.Request(
                VOSK_SMALL_MODEL_URL, headers={"User-Agent": "VaultBot/1.0"}
            )
            with urllib.request.urlopen(req, timeout=120) as resp, \
                    open(tmp_zip, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            with zipfile.ZipFile(tmp_zip) as zf:
                zf.extractall(VOSK_MODEL_DIR)
            tmp_zip.unlink(missing_ok=True)
            root = _vosk_model_root()
            _log("stt_model_download_done", {"path": str(root)})
            return root if root.exists() else None
        except Exception as e:
            _log("stt_model_download_failed", {"error": str(e)})
            return None


def _get_vosk_model():
    """Lazily load the vosk Model. Returns None if vosk/model unavailable."""
    global _vosk_model
    if _vosk_model is not None:
        return _vosk_model
    with _vosk_model_lock:
        if _vosk_model is not None:
            return _vosk_model
        try:
            from vosk import Model, SetLogLevel
        except Exception as e:
            _log("stt_vosk_import_failed", {"error": str(e)})
            return None
        root = _vosk_model_root()
        if not root.exists() or not (root / "conf").exists():
            root = _download_vosk_model()
        if root is None or not root.exists():
            return None
        try:
            SetLogLevel(-1)  # silence Kaldi's chatty stderr
            _vosk_model = Model(str(root))
            _log("stt_vosk_model_loaded", {"path": str(root)})
            return _vosk_model
        except Exception as e:
            _log("stt_vosk_model_load_failed", {"error": str(e)})
            return None


def _bytes_to_audio_data(audio_bytes: bytes, mime: str):
    """Decode arbitrary audio bytes to a speech_recognition.AudioData object.

    Returns None on failure. Decoding priority:
      1. soundfile (libsndfile) — pure-Python wheel, no external binary,
         reads WAV/OGG/Opus/FLAC/MP3. This is the primary path because it
         has no DLL dependency that a Windows Application Control policy
         can block (unlike PyAV, whose av/filter/link.pyd is blocked here).
      2. PyAV — only if importable; on this machine it is NOT (WDAC
         blocks link.pyd), so this branch is effectively dead but kept as
         a fallback for machines where av works.
      3. pydub + system ffmpeg — last resort (no ffmpeg on this machine).

    For Vosk we need 16kHz mono 16-bit PCM; we resample with soundfile and
    hand speech_recognition a WAV it can read directly.
    """
    import speech_recognition as sr
    # Map common MediaRecorder mimes to file extensions for the temp file.
    ext = ".wav"
    if "webm" in mime:
        ext = ".webm"
    elif "ogg" in mime:
        ext = ".ogg"
    elif "mp4" in mime:
        ext = ".mp4"
    elif "mpeg" in mime:
        ext = ".mp3"
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    out_path = None
    try:
        tmp.write(audio_bytes)
        tmp.flush()
        tmp.close()
        src_path = tmp.name

        # If the input is already WAV, speech_recognition reads it directly
        # — but we still want 16kHz mono for Vosk, so route every input
        # through the soundfile resample path below for a consistent
        # 16kHz/mono/16-bit output. (The direct read is kept only as a
        # fallback if soundfile can't parse the WAV.)
        out_path = src_path + ".16k.wav"
        try:
            import soundfile as sf
            data, sr_in = sf.read(src_path, dtype="int16", always_2d=False)
            # soundfile returns mono as 1-D; stereo as 2-D (n, channels).
            if data.ndim > 1:
                data = data.mean(axis=1)
            # Convert to float for resample, then back to int16.
            import numpy as np
            if sr_in != 16000 and len(data) > 0:
                # Linear resample — fine for speech (no need for soxr).
                n_out = int(round(len(data) * 16000 / sr_in))
                idx = np.linspace(0, len(data) - 1, n_out)
                data = np.interp(idx, np.arange(len(data)), data.astype("float32")).astype("int16")
            sf.write(out_path, data, 16000, format="WAV", subtype="PCM_16")
            with sr.AudioFile(out_path) as source:
                return sr.Recognizer().record(source)
        except Exception as e:
            _log("stt_soundfile_decode_failed", {"error": str(e)})

        # Fallback 1: PyAV (blocked on this machine by WDAC, but works
        # elsewhere).
        try:
            import av  # noqa: F401
            if _decode_av(src_path, out_path):
                with sr.AudioFile(out_path) as source:
                    return sr.Recognizer().record(source)
        except Exception:
            pass

        # Fallback 2: pydub + system ffmpeg.
        try:
            from pydub import AudioSegment
            seg = AudioSegment.from_file(src_path)
            seg = seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
            seg.export(out_path, format="wav")
            with sr.AudioFile(out_path) as source:
                return sr.Recognizer().record(source)
        except Exception:
            pass

        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        if out_path:
            try:
                os.unlink(out_path)
            except OSError:
                pass


def _decode_av(src_path: str, out_path: str) -> bool:
    """Decode any audio file to 16kHz mono 16-bit WAV using PyAV.

    Returns True on success. Used as the preferred decoder for webm/opus
    from MediaRecorder; pydub/ffmpeg is the fallback.
    """
    try:
        import av
        inp = av.open(src_path)
        out = av.open(out_path, "w", format="wav")
        out_stream = out.add_stream("pcm_s16le", rate=16000, layout="mono")
        for frame in inp.decode(audio=0):
            frame.pts = None  # resampler assigns new pts
            for packet in out_stream.encode(frame):
                out.mux(packet)
        for packet in out_stream.encode(None):
            out.mux(packet)
        inp.close()
        out.close()
        return True
    except Exception as e:
        _log("stt_av_decode_failed", {"error": str(e)})
        return False


def _transcribe_vosk(model, audio_bytes: bytes, mime: str) -> str:
    """Run vosk on the audio. _bytes_to_audio_data already produced
    16kHz mono 16-bit PCM, so we just hand Vosk the raw WAV bytes."""
    from vosk import KaldiRecognizer
    audio_data = _bytes_to_audio_data(audio_bytes, mime)
    if audio_data is None:
        return ""
    # get_wav_data() with no args returns the bytes as-is (already 16k mono
    # 16-bit from our soundfile resample path). Passing convert_channels=
    # would raise on speech_recognition 3.17, which doesn't support it.
    wav_bytes = audio_data.get_wav_data()
    rec = KaldiRecognizer(model, 16000)
    rec.SetWords(False)
    # Feed in chunks; vosk accepts raw PCM.
    rec.AcceptWaveform(wav_bytes)
    result = json.loads(rec.FinalResult())
    return (result.get("text") or "").strip()


def _transcribe_google(audio_bytes: bytes, mime: str) -> str:
    import speech_recognition as sr
    audio_data = _bytes_to_audio_data(audio_bytes, mime)
    if audio_data is None:
        return ""
    recognizer = sr.Recognizer()
    # Uses the free Google web endpoint (no API key). May fail offline.
    return recognizer.recognize_google(audio_data)


def transcribe(audio_bytes: bytes, mime: str = "audio/webm") -> str:
    """Transcribe raw audio bytes to text.

    Accepts webm/opus (what MediaRecorder produces) or wav. Vosk needs
    16kHz mono WAV, so we decode via `speech_recognition`'s AudioFile
    helper (which uses the system ffmpeg/avbin) into the right format.
    Falls back to the Google free endpoint if vosk isn't ready.
    """
    if not audio_bytes:
        return ""
    _log("stt_request", {"bytes": len(audio_bytes), "mime": mime})

    # Try vosk (offline) first.
    model = _get_vosk_model()
    if model is not None:
        try:
            text = _transcribe_vosk(model, audio_bytes, mime)
            if text:
                _log("stt_vosk_ok", {"text": text[:120]})
                return text
        except Exception as e:
            _log("stt_vosk_failed", {"error": str(e)})

    # Fallback: speech_recognition + Google free endpoint (online, no key).
    try:
        text = _transcribe_google(audio_bytes, mime)
        _log("stt_google_ok", {"text": (text or "")[:120]})
        return text or ""
    except Exception as e:
        _log("stt_google_failed", {"error": str(e)})
        return ""