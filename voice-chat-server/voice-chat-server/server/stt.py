"""
stt.py — Faster-Whisper speech-to-text
Accepts raw audio bytes (WebM/Ogg/MP3/WAV) — ffmpeg decodes them.
Model is lazy-loaded on first call and stays in memory.
"""

import os
import tempfile
import logging

log = logging.getLogger("stt")

_model = None
# tiny=fastest, base=balanced, small=more accurate, medium=best quality
_MODEL_NAME = "base"

try:
    import torch
    _DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
except ImportError:
    _DEVICE = "cpu"

_COMPUTE_TYPE = "float16" if _DEVICE == "cuda" else "int8"


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        log.info(f"Loading Whisper '{_MODEL_NAME}' on {_DEVICE}...")
        _model = WhisperModel(_MODEL_NAME, device=_DEVICE, compute_type=_COMPUTE_TYPE)
        log.info("Whisper ready.")
    return _model


def transcribe_bytes(audio_bytes: bytes, language: str = "en") -> str:
    """
    Transcribe audio bytes to text.
    language=None → auto-detect language.
    language="tr" → force Turkish.
    language="en" → force English.
    """
    model = _get_model()

    suffix = _detect_suffix(audio_bytes)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        segments, info = model.transcribe(
            tmp_path,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        text = " ".join(seg.text.strip() for seg in segments)
        log.info(f"STT ({info.language}, {info.language_probability:.2f}): '{text}'")
        return text.strip()
    finally:
        os.unlink(tmp_path)


def _detect_suffix(data: bytes) -> str:
    """Detect audio container format from magic bytes."""
    if data[:4] == b'RIFF':
        return '.wav'
    if data[:4] == b'OggS':
        return '.ogg'
    if data[:3] == b'ID3' or (len(data) >= 2 and data[:2] == b'\xff\xfb'):
        return '.mp3'
    if data[:4] == b'fLaC':
        return '.flac'
    return '.webm'  # default: browser MediaRecorder output
