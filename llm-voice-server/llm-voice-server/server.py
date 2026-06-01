"""
LLM Voice Server
Konuş → STT → LLM → TTS → Ses yanıtı

Başlatma:
  uvicorn server:app --host 0.0.0.0 --port 8080

Endpoint'ler:
  GET  /           — Web arayüzü (tarayıcıdan test)
  POST /chat       — Metin gönder, metin al
  POST /voice      — Ses dosyası gönder, ses al (WAV)
  WS   /ws         — WebSocket gerçek zamanlı sesli sohbet
"""

import asyncio
import io
import logging
import wave
import base64
import time
from contextlib import asynccontextmanager
from pathlib import Path
import json

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import crypto
from session import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("voice-server")

# ─── Yapılandırma ─────────────────────────────────────────────────────────────
OLLAMA_URL    = "http://localhost:11434/api/chat"
MODEL_NAME    = "gemma3:4b"           # ollama pull gemma3:4b
SAMPLE_RATE   = 22050
TTS_VOICE     = "en-US-GuyNeural"     # edge-tts ses profili
STT_MODEL     = "Google STT"
SENSOR_STALE_SEC = 30.0

SYSTEM_PROMPT = (
    "You are the AI assistant for NEURO-SENTINEL, an ESP32-based IoT security and monitoring system. "
    "The system has these sensors: DS18B20 (temperature), MQ-135 (gas/air quality), "
    "INMP441 (acoustic/sound level), PIR (motion). "
    "Alert thresholds — Temperature: >31.5°C CRITICAL, 29-31.5°C WARM; "
    "Gas: >1200 CRITICAL, 800-1200 HIGH; Sound: >2500 ALARM, 1000-2500 WARNING; "
    "Motion: PIR=1 detected. "
    "When sensor data is provided, evaluate current status. "
    "Keep answers concise (2-3 sentences). Always respond in English."
)

# ─── Global State ─────────────────────────────────────────────────────────────
_sensor_state: dict = {}
_sensor_ts: float   = 0.0


# ─── STT (SpeechRecognition) ──────────────────────────────────────────────────
def transcribe_bytes(audio_bytes: bytes, is_wav: bool = True) -> str:
    """Transcribe audio bytes using SpeechRecognition (Google STT) with ffmpeg normalization."""
    import speech_recognition as sr
    import tempfile
    import os
    import subprocess

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
            tmp_out = tmp_in

        r = sr.Recognizer()
        with sr.AudioFile(tmp_out) as source:
            audio_data = r.record(source)
        
        try:
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
                pass


# ─── LLM ─────────────────────────────────────────────────────────────────────
def _sensor_context() -> str:
    import time
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
        messages.extend(history)
    messages.append({"role": "user", "content": text})

    payload = {
        "model":   MODEL_NAME,
        "stream":  False,
        "options": {"temperature": 0.7, "num_predict": 80},
        "messages": messages,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(OLLAMA_URL, json=payload)
            r.raise_for_status()
            answer = r.json()["message"]["content"].strip()
            log.info(f"LLM: '{answer[:80]}'")
            return answer
    except Exception as e:
        log.error(f"LLM hata: {e}")
        return "Sorry, I could not generate a response. Is Ollama running?"


# ─── TTS (edge-tts) ──────────────────────────────────────────────────────────
async def synthesize_async(text: str) -> bytes:
    """Text -> WAV bytes via edge-tts (22050Hz, 16-bit, mono)."""
    if not text.strip():
        return b""
    try:
        import edge_tts
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
             "-map_metadata", "-1", "-fflags", "+bitexact",
             "-ar", str(SAMPLE_RATE), "-ac", "1", "-acodec", "pcm_s16le", "-filter:a", "volume=5.0", "-f", "wav", "pipe:1"],
            input=mp3_data,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            log.error(f"ffmpeg TTS conversion error: {proc.stderr.decode('utf-8', 'ignore')[:200]}")
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


def pcm_to_wav(pcm: bytes, rate: int = 16000) -> bytes:
    """Converts raw 16-bit PCM mono audio to standard WAV bytes."""
    import io
    import wave
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
    log.info(">>> STT (Google Speech Recognition) hazır.")
    log.info(">>> TTS (edge-tts) hazır.")
    log.info("Sunucu hazır. http://localhost:8080/")
    yield


app = FastAPI(title="LLM Voice Server", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ─── Web UI ───────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LLM Voice Assistant</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0d0d0d;color:#f0f0f0;
     display:flex;flex-direction:column;align-items:center;min-height:100vh;padding:24px}
h1{color:#00e5ff;margin-bottom:24px;font-size:24px}
#chat{width:100%;max-width:640px;background:#1a1a2e;border-radius:16px;
      padding:16px;min-height:320px;max-height:480px;overflow-y:auto;margin-bottom:16px}
.msg{padding:10px 14px;border-radius:12px;margin-bottom:10px;max-width:85%;font-size:14px;line-height:1.5}
.user{background:#0288d1;margin-left:auto}
.assistant{background:#2a2a3e;border:1px solid #333}
.controls{display:flex;gap:10px;width:100%;max-width:640px}
#txt{flex:1;background:#1a1a2e;border:1px solid #333;color:#f0f0f0;
     border-radius:10px;padding:12px;font-size:14px;resize:none;height:56px}
button{padding:12px 20px;border:none;border-radius:10px;cursor:pointer;
       font-size:14px;font-weight:600;transition:opacity .15s}
button:active{opacity:.7}
#send{background:#0288d1;color:#fff}
#record{background:#ff1744;color:#fff}
#record.recording{background:#ff6d00;animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
#status{margin-top:12px;font-size:12px;color:#7a8899}
</style>
</head>
<body>
<h1>🎙 LLM Voice Assistant</h1>
<div id="chat"></div>
<div class="controls">
  <textarea id="txt" placeholder="Type a message..."></textarea>
  <button id="send" onclick="sendText()">Send</button>
  <button id="record" onclick="toggleRecord()">🎙 Hold</button>
</div>
<div id="status">Ready</div>

<script>
const chat = document.getElementById('chat');
const status = document.getElementById('status');
const recBtn = document.getElementById('record');

let recorder, chunks=[], recording=false;
const history = [];

function addMsg(role, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.textContent = text;
  chat.appendChild(d);
  chat.scrollTop = chat.scrollHeight;
}

async function sendText() {
  const txt = document.getElementById('txt').value.trim();
  if (!txt) return;
  document.getElementById('txt').value = '';
  addMsg('user', txt);
  status.textContent = 'Thinking...';
  try {
    const r = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({text: txt, history})
    });
    const d = await r.json();
    addMsg('assistant', d.text);
    history.push({role:'user',content:txt},{role:'assistant',content:d.text});
    if(d.audio) {
      const ab = Uint8Array.from(atob(d.audio), c=>c.charCodeAt(0));
      const ctx = new AudioContext();
      ctx.decodeAudioData(ab.buffer, buf => {
        const src = ctx.createBufferSource();
        src.buffer = buf; src.connect(ctx.destination); src.start();
      });
    }
  } catch(e) { addMsg('assistant','Error: '+e); }
  status.textContent = 'Ready';
}

async function toggleRecord() {
  if (!recording) {
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    recorder = new MediaRecorder(stream);
    chunks = [];
    recorder.ondataavailable = e => chunks.push(e.data);
    recorder.onstop = async () => {
      const blob = new Blob(chunks, {type:'audio/webm'});
      const fd = new FormData();
      fd.append('file', blob, 'audio.webm');
      status.textContent = 'Processing voice...';
      addMsg('user', '🎙 Voice message');
      try {
        const r = await fetch('/voice', {method:'POST', body:fd});
        const d = await r.json();
        // update last user msg
        const msgs = chat.querySelectorAll('.msg.user');
        msgs[msgs.length-1].textContent = d.transcript || '🎙 Voice message';
        addMsg('assistant', d.text);
        history.push({role:'user',content:d.transcript||''},{role:'assistant',content:d.text});
        if(d.audio) {
          const ab = Uint8Array.from(atob(d.audio), c=>c.charCodeAt(0));
          const ctx = new AudioContext();
          ctx.decodeAudioData(ab.buffer, buf => {
            const src = ctx.createBufferSource();
            src.buffer = buf; src.connect(ctx.destination); src.start();
          });
        }
      } catch(e) { addMsg('assistant','Error: '+e); }
      status.textContent = 'Ready';
    };
    recorder.start();
    recording = true;
    recBtn.textContent = '⏹ Stop';
    recBtn.classList.add('recording');
    status.textContent = 'Recording...';
  } else {
    recorder.stop();
    recorder.stream.getTracks().forEach(t=>t.stop());
    recording = false;
    recBtn.textContent = '🎙 Hold';
    recBtn.classList.remove('recording');
  }
}

document.getElementById('txt').addEventListener('keydown', e => {
  if(e.key==='Enter' && !e.shiftKey){e.preventDefault();sendText();}
});
</script>
</body>
</html>""")


# ─── REST: metin sohbet ───────────────────────────────────────────────────────
@app.post("/chat")
async def chat_endpoint(body: dict):
    text    = body.get("text", "")
    history = body.get("history", [])
    if not text.strip():
        return JSONResponse({"error": "empty text"}, status_code=400)

    answer    = await llm_chat(text, history)
    wav_bytes = await asyncio.get_event_loop().run_in_executor(None, synthesize, answer)
    audio_b64 = base64.b64encode(wav_bytes).decode() if wav_bytes else ""

    return {"text": answer, "audio": audio_b64}


# ─── REST: sesli sohbet ───────────────────────────────────────────────────────
@app.post("/voice")
async def voice_endpoint(file: UploadFile = File(...)):
    audio_bytes = await file.read()

    transcript = await asyncio.get_event_loop().run_in_executor(
        None, transcribe_bytes, audio_bytes, True
    )
    if not transcript:
        return JSONResponse({"error": "could not transcribe audio"}, status_code=422)

    answer    = await llm_chat(transcript)
    wav_bytes = await asyncio.get_event_loop().run_in_executor(None, synthesize, answer)
    audio_b64 = base64.b64encode(wav_bytes).decode() if wav_bytes else ""

    return {"transcript": transcript, "text": answer, "audio": audio_b64}


# ─── WebSocket: gerçek zamanlı sesli sohbet ───────────────────────────────────
@app.websocket("/ws")
@app.websocket("/ws/chat")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    log.info("WS bağlandı (şifresiz PCM modu)")

    # Bağlantı hazır bildirimini gönder
    await ws.send_text(json.dumps({
        "type": "connected",
        "status": "ready"
    }, separators=(',', ':')))

    history = []
    try:
        while True:
            msg = await ws.receive()

            if "bytes" in msg:
                pcm_bytes = msg["bytes"]
                if len(pcm_bytes) < 320:  # Çok kısa paketleri yoksay
                    continue

                # Konuşma uzunluğunu logla
                log.info(f"Alınan Ses: {len(pcm_bytes)//2/16000:.2f}s")

                # STT ve debug WAV kaydı için standard format (WAV) dönüştür
                wav_in = pcm_to_wav(pcm_bytes, rate=16000)

                # PC'ye debug WAV dosyasını kaydet
                try:
                    debug_path = r"c:\Users\drdna\iot_project2\esp32_debug.wav"
                    with open(debug_path, "wb") as f:
                        f.write(wav_in)
                    log.info(f"Saved debug audio to {debug_path}")
                except Exception as ex:
                    log.error(f"Failed to save debug audio: {ex}")

                # Transkripsiyon yap
                transcript = await asyncio.get_event_loop().run_in_executor(
                    None, transcribe_bytes, wav_in, True
                )
                
                # İstemciye transkripti metin olarak geri bildir (TFT veya log için)
                await ws.send_text(json.dumps({
                    "type": "transcript",
                    "text": transcript,
                }, separators=(',', ':')))

                if not transcript.strip():
                    continue

                # LLM'den yanıt üret
                sensors = _sensor_context()
                answer = await llm_chat(transcript, history)

                # Konsolda şık log banner'ı bas
                log.info(
                    "\n" + "=" * 66 + "\n" +
                    "🎙️  [ESP32 RAW WS] Söylenen Söz (Transkript):\n" +
                    f"   \"{transcript}\"\n" +
                    ("-" * 66) + "\n" +
                    (f"📡  [SENSÖRLER] Aktif Telemetri:\n{sensors}\n" + ("-" * 66) + "\n" if sensors else "") +
                    "🤖  [GEMMA3 YANITI] Hoparlöre Gönderilen Cevap:\n" +
                    f"   \"{answer}\"\n" +
                    "=" * 66 + "\n"
                )

                # edge-tts ile WAV formatında ses sentezle
                wav_bytes = await asyncio.get_event_loop().run_in_executor(
                    None, synthesize, answer
                )

                if wav_bytes and len(wav_bytes) > 44:
                    # WAV header (ilk 44 byte) kırpılarak saf PCM verisi gönderilir
                    raw_pcm = wav_bytes[44:]
                    
                    # Ortalama genliği hesaplayıp logla (sesin dolu mu boş mu olduğunu test etmek için)
                    import struct
                    samples = struct.unpack(f"<{len(raw_pcm)//2}h", raw_pcm)
                    avg_amp = sum(abs(s) for s in samples) / len(samples) if samples else 0
                    log.info(f"Sentezlenen Ses: {len(raw_pcm)} byte PCM, Ortalama Genlik: {avg_amp:.1f}")
                    
                    await ws.send_bytes(raw_pcm)

                history.append({"role": "user",      "content": transcript})
                history.append({"role": "assistant",  "content": answer})
                if len(history) > 20:
                    history = history[-20:]

            elif "text" in msg:
                # Metin mesajı (Web UI'dan vb.)
                text = msg["text"].strip()
                answer = await llm_chat(text, history)
                await ws.send_text(json.dumps({
                    "type": "answer",
                    "text": answer,
                }, separators=(',', ':')))
                
                wav_bytes = await asyncio.get_event_loop().run_in_executor(
                    None, synthesize, answer
                )
                if wav_bytes and len(wav_bytes) > 44:
                    raw_pcm = wav_bytes[44:]
                    import struct
                    samples = struct.unpack(f"<{len(raw_pcm)//2}h", raw_pcm)
                    avg_amp = sum(abs(s) for s in samples) / len(samples) if samples else 0
                    log.info(f"Sentezlenen Ses (Text): {len(raw_pcm)} byte PCM, Ortalama Genlik: {avg_amp:.1f}")
                    await ws.send_bytes(raw_pcm)
                
                history.append({"role": "user",      "content": text})
                history.append({"role": "assistant",  "content": answer})

    except WebSocketDisconnect:
        log.info("WS bağlantısı kesildi")
    finally:
        pass


# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    import time
    age = round(time.time() - _sensor_ts, 1) if _sensor_ts else None
    return {"status": "ok", "model": MODEL_NAME, "stt": STT_MODEL, "sensor_age": age}


# ─── ESP32 API Endpoints ──────────────────────────────────────────────────────
from fastapi import Request

@app.post("/api/sensor-data")
async def post_sensor(request: Request):
    global _sensor_state, _sensor_ts
    try:
        body = await request.body()
        if not body:
            log.warning("Empty body in sensor POST")
            return JSONResponse({"ok": False, "error": "empty body"}, status_code=400)
        
        import json
        _sensor_state = json.loads(body.decode("utf-8"))
        _sensor_ts    = time.time()
        return {"ok": True}
    except Exception as e:
        log.error(f"Sensor POST error: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.get("/api/sensor-data")
async def get_sensor():
    import time
    age = round(time.time() - _sensor_ts, 1) if _sensor_ts else None
    return {
        "data":        _sensor_state,
        "age_seconds": age,
        "stale":       age is None or age > SENSOR_STALE_SEC,
    }


@app.post("/api/voice")
async def esp32_voice(request: Request):
    """ESP32 sends raw 16-bit PCM (16000Hz mono) -> WAV response (22050Hz)."""
    pcm = await request.body()
    if len(pcm) < 3200:
        return JSONResponse({"error": "audio too short"}, status_code=400)

    # Convert PCM to standard WAV
    wav_in = pcm_to_wav(pcm, rate=16000)

    # Save debug WAV file to PC
    try:
        debug_path = r"c:\Users\drdna\iot_project2\esp32_debug.wav"
        with open(debug_path, "wb") as f:
            f.write(wav_in)
        log.info(f"Saved debug audio to {debug_path}")
    except Exception as ex:
        log.error(f"Failed to save debug audio: {ex}")

    loop = asyncio.get_event_loop()
    try:
        transcript = await loop.run_in_executor(None, transcribe_bytes, wav_in, True)
    except Exception as e:
        log.error(f"ESP32 STT error: {e}")
        return JSONResponse({"error": "stt failed"}, status_code=500)

    if not transcript.strip():
        return Response(status_code=204)

    # Get active sensor context
    sensors = _sensor_context()

    # Get answer from LLM
    answer = await llm_chat(transcript)

    # Beautiful console log banner for easy tracking
    log.info(
        "\n" + "=" * 66 + "\n" +
        "🎙️  [ESP32 MİKROFONU] Söylenen Söz (Transkript):\n" +
        f"   \"{transcript}\"\n" +
        ("-" * 66) + "\n" +
        (f"📡  [SENSÖRLER] Aktif Telemetri:\n{sensors}\n" + ("-" * 66) + "\n" if sensors else "") +
        "🤖  [GEMMA3 YANITI] Hoparlöre Gönderilen Cevap:\n" +
        f"   \"{answer}\"\n" +
        "=" * 66 + "\n"
    )

    # Synthesize to WAV
    wav_out = await loop.run_in_executor(None, synthesize, answer)
    return Response(content=wav_out, media_type="audio/wav")
