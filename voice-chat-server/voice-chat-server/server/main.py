"""
NEURO-SENTINEL Voice Server
STT (faster-whisper tiny) + LLM (Ollama gemma3:4b) + TTS (Piper)

Run:  uvicorn main:app --host 0.0.0.0 --port 8080
UI:   http://localhost:8080

Endpoints:
  GET  /                  — Web UI
  POST /chat              — Text in → text + audio out (browser)
  POST /voice             — Audio file in → text + audio out (browser)
  POST /api/voice         — Raw PCM in (ESP32) → WAV out
  POST /api/sensor-data   — ESP32 sensor update
  GET  /api/sensor-data   — Latest sensor readings
  WS   /ws/chat           — WebSocket real-time voice (browser)
  GET  /health            — Health check
"""

import asyncio
import base64
import io
import logging
import os
import threading
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("neuro-sentinel")

# ─── Config ───────────────────────────────────────────────────────────────────
OLLAMA_URL  = "http://localhost:11434/api/chat"
MODEL_NAME  = "gemma3:4b"
STT_MODEL   = "tiny"          # tiny=40MB, base=150MB — tiny yeterli
TTS_MODEL   = str(Path(__file__).parent / "tts_models" / "en_US-lessac-medium.onnx")
TTS_VOICE   = "en-US-GuyNeural"   # edge-tts ses profili (erkek: GuyNeural, kadin: JennyNeural)
SAMPLE_RATE = 22050
SENSOR_STALE_SEC = 30.0

SYSTEM_PROMPT = (
    "You are the AI assistant for NEURO-SENTINEL, an ESP32-based IoT security and monitoring system. "
    "The system has these sensors: DS18B20 (temperature), MQ-135 (gas/air quality), "
    "INMP441 (acoustic/sound level), PIR (motion). "
    "Alert thresholds — Temperature: >31.5°C CRITICAL, 29-31.5°C WARM; "
    "Gas: >1200 CRITICAL, 800-1200 HIGH; Sound: >2500 ALARM, 1000-2500 WARNING; "# ─── Global state ─────────────────────────────────────────────────────────────
_stt_model  = None
_stt_ready  = True           # Google STT is instantly ready
_sensor_state: dict = {}
_sensor_ts: float   = 0.0

# ─── Model loaders ────────────────────────────────────────────────────────────
def get_stt():
    """Dummy loader for backwards compatibility."""
    global _stt_ready
    _stt_ready = True
    return None


# ─── STT (SpeechRecognition) ──────────────────────────────────────────────────
def transcribe_bytes(audio_bytes: bytes) -> str:
    """Transcribe audio bytes using SpeechRecognition (Google STT) with ffmpeg format normalization."""
    import speech_recognition as sr
    import tempfile
    import os
    import subprocess

    # Save audio_bytes to a temp file
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
        f.write(audio_bytes)
        tmp_in = f.name

    tmp_out = tmp_in + ".wav"
    try:
        # Convert to 16kHz mono WAV for maximum speech recognition compatibility
        cmd = ["ffmpeg", "-y", "-i", tmp_in, "-ar", "16000", "-ac", "1", tmp_out]
        proc = subprocess.run(cmd, capture_output=True, timeout=15)
        if proc.returncode != 0:
            log.error(f"ffmpeg conversion failed in STT: {proc.stderr.decode('utf-8', 'ignore')[:200]}")
            # Fallback to tmp_in if it was already WAV
            tmp_out = tmp_in

        r = sr.Recognizer()
        with sr.AudioFile(tmp_out) as source:
            audio_data = r.record(source)
        
        try:
            # We can force English as specified in SYSTEM_PROMPT
            text = r.recognize_google(audio_data, language="en-US")
            log.info(f"STT (Google): '{text}'")
            return text.strip()
        except sr.UnknownValueError:
            log.warning("Google STT could not understand audio")
            return ""
        except sr.RequestError as e:
            log.error(f"Google STT request error: {e}")
            return ""
    except Exception as e:
        log.error(f"STT processing error: {e}")
        return ""
    finally:
        for p in [tmp_in, tmp_out]:
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass��───────────────────────────────────────────
def transcribe_bytes(audio_bytes: bytes) -> str:
    model  = get_stt()
    audio  = io.BytesIO(audio_bytes)
    segs, info = model.transcribe(
        audio,
        language="en",
        beam_size=1,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
    )
    text = " ".join(s.text.strip() for s in segs).strip()
    log.info(f"STT ({info.language}, {info.language_probability:.2f}): '{text}'")
    return text


# ─── LLM ──────────────────────────────────────────────────────────────────────
def _sensor_context() -> str:
    if not _sensor_state or (time.time() - _sensor_ts) > SENSOR_STALE_SEC:
        return ""
    parts = []
    d = _sensor_state
    if "temperature" in d:
        t  = d["temperature"]
        st = "CRITICAL" if t > 31.5 else ("WARM" if t >= 29 else "NORMAL")
        parts.append(f"Temperature: {t:.1f}°C ({st})")
    if "gas_raw" in d:
        g  = d["gas_raw"]
        st = "CRITICAL" if g > 1200 else ("HIGH" if g >= 800 else "CLEAN")
        parts.append(f"Gas: {g} ({st})")
    if "audio_level" in d:
        a  = d["audio_level"]
        st = "ALARM" if a > 2500 else ("WARNING" if a >= 1000 else "QUIET")
        parts.append(f"Sound: {a} ({st})")
    if "motion_detected" in d:
        parts.append(f"Motion: {'DETECTED' if d['motion_detected'] else 'NONE'}")
    return "\n".join(parts)


async def llm_chat(text: str, history: list | None = None) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    ctx = _sensor_context()
    if ctx:
        messages.append({"role": "system", "content": f"Current sensor readings:\n{ctx}"})
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": text})

    payload = {
        "model":    MODEL_NAME,
        "stream":   False,
        "options":  {"temperature": 0.7, "num_predict": 100},
        "messages": messages,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(OLLAMA_URL, json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
    except Exception as e:
        log.error(f"LLM error: {e}")
        return "Sorry, AI backend is unavailable. Is Ollama running?"


# ─── TTS (edge-tts) ──────────────────────────────────────────────────────────
async def synthesize_async(text: str) -> bytes:
    """Text -> WAV bytes via edge-tts (22050Hz, 16-bit, mono)."""
    if not text.strip():
        return b""
    try:
        import edge_tts
        import struct

        communicate = edge_tts.Communicate(text, TTS_VOICE)
        mp3_chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3_chunks.append(chunk["data"])

        if not mp3_chunks:
            return b""

        mp3_data = b"".join(mp3_chunks)

        # MP3 -> WAV donusumu (ffmpeg kullanarak)
        import subprocess
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", "pipe:0",
             "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "wav", "pipe:1"],
            input=mp3_data,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            log.error(f"ffmpeg TTS conversion error: {proc.stderr[:200]}")
            return b""
        return proc.stdout
    except Exception as e:
        log.error(f"TTS error: {e}")
        return b""


def synthesize(text: str) -> bytes:
    """Sync wrapper for synthesize_async (for run_in_executor compatibility)."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're in a running loop, create a new one in thread
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(synthesize_async(text))
            finally:
                new_loop.close()
        else:
            return loop.run_until_complete(synthesize_async(text))
    except Exception:
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(synthesize_async(text))
        finally:
            new_loop.close()


def pcm_to_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


# ─── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_event_loop()

    async def _load():
        try:
            await loop.run_in_executor(None, get_stt)
            log.info(">>> STT (Whisper tiny) ready.")
        except Exception as e:
            log.error(f">>> STT load failed: {e}")
        log.info(">>> NEURO-SENTINEL server ready. Press ESP32 BOOT button to speak.")

    asyncio.create_task(_load())
    log.info(">>> edge-tts will be used for speech synthesis (no model preload needed).")
    yield


# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="NEURO-SENTINEL Voice Server", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ─── Sensor endpoints ─────────────────────────────────────────────────────────
@app.post("/api/sensor-data")
async def post_sensor(request: Request):
    global _sensor_state, _sensor_ts
    try:
        _sensor_state = await request.json()
        _sensor_ts    = time.time()
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.get("/api/sensor-data")
async def get_sensor():
    age = round(time.time() - _sensor_ts, 1) if _sensor_ts else None
    return {
        "data":        _sensor_state,
        "age_seconds": age,
        "stale":       age is None or age > SENSOR_STALE_SEC,
    }


# ─── ESP32 voice endpoint ─────────────────────────────────────────────────────
@app.post("/api/voice")
async def esp32_voice(request: Request):
    """ESP32 sends raw 16-bit PCM (16000Hz mono) → WAV response (22050Hz)."""
    if not _stt_ready:
        log.warning("ESP32 ses isteği geldi ama STT henüz hazır değil.")
        return JSONResponse({"error": "STT model loading, please wait"}, status_code=503)

    pcm = await request.body()
    if len(pcm) < 3200:
        return JSONResponse({"error": "audio too short"}, status_code=400)

    wav_in = pcm_to_wav(pcm, rate=16000)

    loop = asyncio.get_event_loop()
    try:
        transcript = await loop.run_in_executor(None, transcribe_bytes, wav_in)
    except Exception as e:
        log.error(f"ESP32 STT error: {e}")
        return JSONResponse({"error": "stt failed"}, status_code=500)

    if not transcript.strip():
        return Response(status_code=204)

    log.info(f"── ESP32 USER ────────────────────────")
    log.info(f"  {transcript}")

    answer = await llm_chat(transcript)

    log.info(f"── ESP32 ASSISTANT ───────────────────")
    log.info(f"  {answer}")
    log.info(f"──────────────────────────────────────")

    wav_out = await loop.run_in_executor(None, synthesize, answer)
    return Response(content=wav_out, media_type="audio/wav")


# ─── Browser REST: text ───────────────────────────────────────────────────────
@app.post("/chat")
async def chat_endpoint(body: dict):
    text    = body.get("text", "").strip()
    history = body.get("history", [])
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)

    log.info(f"── USER ──────────────────────────────")
    log.info(f"  {text}")

    answer = await llm_chat(text, history)

    log.info(f"── ASSISTANT ─────────────────────────")
    log.info(f"  {answer}")
    log.info(f"──────────────────────────────────────")

    loop     = asyncio.get_event_loop()
    wav      = await loop.run_in_executor(None, synthesize, answer)
    audio_b64 = base64.b64encode(wav).decode() if wav else ""
    return {"text": answer, "audio": audio_b64}


# ─── Browser REST: voice file ─────────────────────────────────────────────────
@app.post("/voice")
async def voice_endpoint(file: UploadFile = File(...)):
    audio_bytes = await file.read()
    loop = asyncio.get_event_loop()

    try:
        transcript = await loop.run_in_executor(None, transcribe_bytes, audio_bytes)
    except Exception as e:
        return JSONResponse({"error": f"stt failed: {e}"}, status_code=422)

    if not transcript:
        return JSONResponse({"error": "could not transcribe"}, status_code=422)

    log.info(f"── USER ──────────────────────────────")
    log.info(f"  {transcript}")

    answer = await llm_chat(transcript)

    log.info(f"── ASSISTANT ─────────────────────────")
    log.info(f"  {answer}")
    log.info(f"──────────────────────────────────────")

    wav       = await loop.run_in_executor(None, synthesize, answer)
    audio_b64 = base64.b64encode(wav).decode() if wav else ""
    return {"transcript": transcript, "text": answer, "audio": audio_b64}


# ─── Browser WebSocket ────────────────────────────────────────────────────────
@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    history: list[dict] = []
    log.info("Client connected")
    loop = asyncio.get_event_loop()

    try:
        while True:
            msg = await ws.receive()

            if "bytes" in msg:
                audio_bytes = msg["bytes"]
                if not audio_bytes:
                    continue

                await ws.send_json({"type": "status", "state": "transcribing"})
                try:
                    transcript = await loop.run_in_executor(None, transcribe_bytes, audio_bytes)
                except Exception as e:
                    await ws.send_json({"type": "error", "message": f"STT failed: {e}"})
                    await ws.send_json({"type": "status", "state": "ready"})
                    continue

                if not transcript.strip():
                    await ws.send_json({"type": "status", "state": "ready"})
                    continue

                await ws.send_json({"type": "transcript", "text": transcript})
                await _ws_respond(ws, transcript, history, loop)

            elif "text" in msg:
                try:
                    import json
                    data = json.loads(msg["text"])
                    if data.get("type") == "text":
                        text = data.get("content", "").strip()
                        if text:
                            await _ws_respond(ws, text, history, loop)
                except Exception:
                    pass

    except WebSocketDisconnect:
        pass
    finally:
        log.info("Client disconnected")


async def _ws_respond(ws: WebSocket, text: str, history: list, loop):
    log.info(f"── USER ──────────────────────────────")
    log.info(f"  {text}")

    await ws.send_json({"type": "status", "state": "thinking"})
    answer = await llm_chat(text, history)

    log.info(f"── ASSISTANT ─────────────────────────")
    log.info(f"  {answer}")
    log.info(f"──────────────────────────────────────")

    history.append({"role": "user",      "content": text})
    history.append({"role": "assistant", "content": answer})
    if len(history) > 20:
        del history[:-20]

    await ws.send_json({"type": "answer", "text": answer})
    await ws.send_json({"type": "status", "state": "speaking"})

    wav = await loop.run_in_executor(None, synthesize, answer)
    if wav:
        await ws.send_bytes(wav)

    await ws.send_json({"type": "status", "state": "ready"})


# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    age = round(time.time() - _sensor_ts, 1) if _sensor_ts else None
    return {"status": "ok", "model": MODEL_NAME, "stt": STT_MODEL, "sensor_age": age}


# ─── Static web UI (must be last) ─────────────────────────────────────────────
_WEB_UI = Path(__file__).parent.parent / "web_ui"
if _WEB_UI.exists():
    app.mount("/", StaticFiles(directory=str(_WEB_UI), html=True), name="web_ui")
