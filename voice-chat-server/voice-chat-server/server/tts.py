"""
tts.py — Piper TTS (in-memory, fast)
Model: en_US-lessac-medium.onnx (place in tts_models/ folder)
Returns raw PCM (22050Hz, 16-bit, mono) or WAV bytes.

Download model:
  tts_models/en_US-lessac-medium.onnx
  tts_models/en_US-lessac-medium.onnx.json
From: https://huggingface.co/rhasspy/piper-voices/tree/v1.0.0/en/en_US/lessac/medium
"""

import io
import os
import wave
import logging

log = logging.getLogger("tts")

_HERE = os.path.dirname(os.path.abspath(__file__))
TTS_MODEL_PATH = os.path.join(_HERE, "tts_models", "en_US-lessac-medium.onnx")
TTS_SAMPLE_RATE = 22050

_voice = None


def _get_voice():
    global _voice
    if _voice is None:
        from piper.voice import PiperVoice
        log.info(f"Loading Piper model: {TTS_MODEL_PATH}")
        _voice = PiperVoice.load(TTS_MODEL_PATH)
        log.info("Piper ready.")
    return _voice


def synthesize(text: str) -> bytes:
    """Text → raw PCM (22050Hz, 16-bit, mono)."""
    if not text.strip():
        return b""
    try:
        voice = _get_voice()
        if hasattr(voice, "synthesize_wav"):
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                voice.synthesize_wav(text, wf)
            return buf.getvalue()[44:]  # strip 44-byte WAV header → raw PCM
        elif hasattr(voice, "synthesize_stream_raw"):
            return b"".join(voice.synthesize_stream_raw(text))
        else:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                voice.synthesize(text, wf)
            return buf.getvalue()[44:]
    except Exception as e:
        log.error(f"TTS error: {e}", exc_info=True)
        return b""


def pcm_to_wav(pcm: bytes, sample_rate: int = TTS_SAMPLE_RATE) -> bytes:
    """Raw PCM → WAV bytes (browser plays this directly)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()
