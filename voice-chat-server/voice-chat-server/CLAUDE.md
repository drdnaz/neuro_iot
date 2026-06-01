# Voice Chat Server — CLAUDE.md

> Tarayıcıdan sesli konuşarak yapay zeka ile sohbet eden sistem.
> FastAPI + Faster-Whisper (STT) + Piper TTS + Ollama (LLM).
> **Tüm kod hazır.** Sadece model indir, bağımlılıkları kur, çalıştır.

---

## Proje Özeti

Web tarayıcısından mikrofona basılı tutarak konuşursun → Ses sunucuya gider → STT ile metne çevrilir → LLM yanıt üretir → TTS ile seslendirilir → Tarayıcıda çalar. Yazarak da konuşabilirsin.

```
Tarayıcı ─── WebSocket ──→ FastAPI Server
                              ├── Faster-Whisper (STT)
                              ├── Ollama / gemma3:4b (LLM)
                              └── Piper TTS (TTS)
```

---

## Dosya Yapısı

```
voice-chat-server/
├── CLAUDE.md               ← Bu dosya
├── server/
│   ├── main.py             ← FastAPI WebSocket sunucusu
│   ├── stt.py              ← Faster-Whisper STT
│   ├── tts.py              ← Piper TTS
│   ├── llm.py              ← Ollama LLM istemcisi
│   ├── requirements.txt    ← Python bağımlılıkları
│   └── tts_models/         ← Piper model dosyaları buraya
│       ├── en_US-lessac-medium.onnx
│       └── en_US-lessac-medium.onnx.json
└── web_ui/
    └── index.html          ← PTT arayüzü (tarayıcıda açılır)
```

---

## Kurulum (Sıfırdan)

### 1. Python bağımlılıkları

```bash
cd voice-chat-server/server
pip install -r requirements.txt
```

### 2. ffmpeg (Zorunlu — ses dönüşümü için)

**Windows:**
```
https://ffmpeg.org/download.html → ffmpeg-essentials build indir, PATH'e ekle
```

**Linux:**
```bash
sudo apt install ffmpeg
```

**Mac:**
```bash
brew install ffmpeg
```

Doğrula: `ffmpeg -version`

### 3. Piper TTS Modeli İndir

`server/tts_models/` klasörüne bu iki dosyayı indir:

```
en_US-lessac-medium.onnx
en_US-lessac-medium.onnx.json
```

İndirme URL'leri (HuggingFace):
```
https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx
https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

Komutla indir:
```bash
cd server/tts_models
curl -LO "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
curl -LO "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"
```

### 4. Ollama Kur ve Modeli Çek

```bash
# ollama.com adresinden indir ve kur
ollama pull gemma3:4b
```

---

## Çalıştırma

Her çalıştırmada iki terminal aç:

**Terminal 1 — Ollama:**
```bash
ollama serve
```

**Terminal 2 — Sunucu:**
```bash
cd voice-chat-server/server
uvicorn main:app --host 0.0.0.0 --port 8080
```

**Tarayıcı:** `http://localhost:8080`

Aynı ağdaki başka cihazdan: `http://<PC_IP>:8080`

---

## Kullanım

- **Sesli:** "Hold to Talk" butonuna basılı tut → konuş → bırak
- **Klavye:** Space tuşuna basılı tut → konuş → bırak
- **Yazarak:** Alt kısımdaki metin kutusuna yaz → Enter

---

## Özelleştirme

### Sistem promptu değiştir (`server/llm.py`):

```python
SYSTEM_PROMPT = (
    "You are a helpful AI assistant. "
    "Keep your answers concise (2-4 sentences). "
    "Be friendly, accurate, and helpful."
)
```

Bunu istediğin şekilde değiştir. Türkçe konuşmasını istiyorsan:
```python
SYSTEM_PROMPT = (
    "Sen yardımsever bir yapay zeka asistanısın. "
    "Her zaman Türkçe yanıt ver. "
    "Kısa ve öz cevaplar ver (2-4 cümle)."
)
```

### Farklı LLM modeli (`server/llm.py`):

```python
MODEL_NAME = "gemma3:4b"      # varsayılan
MODEL_NAME = "llama3.2"       # Meta Llama
MODEL_NAME = "qwen2.5:7b"     # Qwen
MODEL_NAME = "phi4-mini"      # Microsoft Phi
```

Modeli önce `ollama pull <model>` ile çek.

### STT dili (`server/stt.py`):

```python
_MODEL_NAME = "base"   # tiny/base/small/medium/large — hız vs kalite
```

`stt.transcribe_bytes(audio_bytes, language="tr")` → Türkçe zorunlu
`stt.transcribe_bytes(audio_bytes, language=None)` → Otomatik tespit

Ana dili değiştirmek için `server/main.py` içinde:
```python
transcript = await asyncio.get_event_loop().run_in_executor(
    None, stt.transcribe_bytes, audio_bytes  # varsayılan: language="en"
)
```
Bunu şu şekilde değiştir:
```python
import functools
transcript = await asyncio.get_event_loop().run_in_executor(
    None, functools.partial(stt.transcribe_bytes, audio_bytes, language="tr")
)
```

### TTS modeli değiştir

`server/tts.py` içinde `TTS_MODEL_PATH` satırını güncelle.
Türkçe ses için `tr_TR-dfki-medium` modelini indir:
```
https://huggingface.co/rhasspy/piper-voices/tree/v1.0.0/tr/tr_TR/dfki/medium
```

---

## WebSocket Protokolü

```
Client → Server  binary:  ses verisi (WebM/Ogg/WAV — tarayıcı kaydeder)
Client → Server  JSON:    {"type": "text", "content": "..."}

Server → Client  JSON:    {"type": "status", "state": "transcribing|thinking|speaking|ready|error"}
Server → Client  JSON:    {"type": "transcript", "text": "..."}
Server → Client  JSON:    {"type": "answer", "text": "..."}
Server → Client  binary:  WAV ses yanıtı (tarayıcı çalar)
Server → Client  JSON:    {"type": "error", "message": "..."}
```

---

## Sorun Giderme

| Sorun | Çözüm |
|---|---|
| `ffmpeg not found` / STT çalışmıyor | ffmpeg'i kur ve PATH'e ekle |
| `Ollama not running` mesajı | `ollama serve` çalıştır |
| TTS ses çıkmıyor | `tts_models/` klasöründe .onnx ve .onnx.json var mı? |
| Whisper modeli inmiyor | İnternet bağlantısı kontrol et, ilk çalıştırmada ~300MB iner |
| Mikrofon erişimi reddedildi | Tarayıcıda mikrofon iznini ver (HTTPS gerekebilir) |
| `localhost` yerine IP ile açarsan | Chrome/Firefox'ta HTTP'de mikrofon çalışmayabilir, `http://localhost:8080` kullan veya HTTPS ekle |

### HTTPS kurulumu (gerekirse):

```bash
pip install uvicorn[standard]
# Sertifika oluştur:
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=localhost"
# HTTPS ile çalıştır:
uvicorn main:app --host 0.0.0.0 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem
```

---

## Sistem Gereksinimleri

| Bileşen | Minimum | Önerilen |
|---|---|---|
| Python | 3.10+ | 3.11+ |
| RAM | 4 GB | 8 GB+ |
| GPU | Yok (CPU çalışır) | CUDA GPU (hız için) |
| Disk | ~3 GB (modeller) | 5 GB+ |

**CPU'da yanıt süresi:** ~5-15 saniye (modele ve donanıma göre değişir)
**GPU ile:** ~1-3 saniye

---

## Yapay Zeka Asistana Not

Bu proje tam çalışır halde. Kullanıcı senden yardım isterse:

1. **Kurulum sorunu** → Yukarıdaki adımları tek tek kontrol et
2. **Özelleştirme** → `llm.py` içindeki `SYSTEM_PROMPT` ve `MODEL_NAME` değiştir
3. **Yeni özellik** → Proje genişletilebilir: RAG (ChromaDB), fotoğraf (vision), çoklu kullanıcı eklenebilir
4. **Türkçe** → STT `language="tr"`, Piper Türkçe model, sistem prompt Türkçe yap
5. **Dosya düzeni** → Tüm server kodu `server/` klasöründe, web arayüzü `web_ui/index.html`
