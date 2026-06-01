# -*- coding: utf-8 -*-
"""
NEURO-SENTINEL Voice Server -- Baslatici
========================================

Kullanim:
    python server.py

Bu script, voice-chat-server sunucusunu dogrudan baslatir.
Sunucu baslamadan once gerekli bilesenleri (ffmpeg, TTS modeli, Ollama) kontrol eder.

Pipeline:
    ESP32 BOOT butonu -> Mikrofon kaydi (16kHz PCM)
    -> HTTP POST /api/voice -> server
        -> faster-whisper (STT) -> metin
        -> Ollama gemma3:4b (LLM) -> yanit
        -> Piper TTS -> WAV ses (22050Hz)
    <- WAV yanit -> MAX98357A amfi -> Hoparlor
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path


def check_prerequisites():
    """Sunucu baslamadan once gerekli bilesenleri kontrol et."""
    errors = []
    warnings = []

    # 1. ffmpeg kontrolu
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        # Yerel ffmpeg kurulumunu da kontrol et
        local_ffmpeg = Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg"
        found = False
        if local_ffmpeg.exists():
            for d in local_ffmpeg.iterdir():
                bin_dir = d / "bin"
                if bin_dir.exists():
                    ffmpeg_exe = bin_dir / "ffmpeg.exe"
                    if ffmpeg_exe.exists():
                        # PATH'e ekle
                        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
                        print(f"  [OK] ffmpeg bulundu: {ffmpeg_exe}")
                        found = True
                        break
        if not found:
            errors.append(
                "[HATA] ffmpeg bulunamadi! STT (Whisper) icin ffmpeg zorunludur.\n"
                "   Kurulum: https://ffmpeg.org/download.html adresinden indirip PATH'e ekleyin.\n"
                "   Veya:  pip install imageio-ffmpeg"
            )
    else:
        print(f"  [OK] ffmpeg: {ffmpeg_path}")

    # 2. edge-tts kontrolu
    try:
        import edge_tts
        print("  [OK] edge-tts yuklu")
    except ImportError:
        errors.append(
            "[HATA] edge-tts yuklu degil!\n"
            "   Kur: pip install edge-tts"
        )

    # 3. Ollama erisimi
    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=5.0)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            if any("gemma3" in m for m in models):
                print(f"  [OK] Ollama: gemma3:4b modeli mevcut")
            else:
                warnings.append(f"[UYARI] Ollama calisiyor ama gemma3:4b bulunamadi. Mevcut: {models}")
        else:
            warnings.append("[UYARI] Ollama yanit verdi ama model listesi alinamadi.")
    except Exception:
        warnings.append(
            "[UYARI] Ollama'ya baglanilamadi (http://localhost:11434).\n"
            "   Ollama'nin arka planda calistigindan emin olun: ollama serve"
        )

    # 4. Python bagimliliklari
    missing_pkgs = []
    for pkg in ["fastapi", "uvicorn", "httpx", "speech_recognition", "edge_tts"]:
        try:
            __import__(pkg)
        except ImportError:
            missing_pkgs.append(pkg)

    if missing_pkgs:
        errors.append(
            f"[HATA] Eksik Python paketleri: {', '.join(missing_pkgs)}\n"
            f"   Kur: pip install fastapi uvicorn httpx SpeechRecognition edge-tts"
        )
    else:
        print("  [OK] Python bagimliliklari tamam")

    # Sonuc
    for w in warnings:
        print(f"\n{w}")

    if errors:
        print("\n" + "=" * 50)
        print("HATALAR:")
        print("=" * 50)
        for e in errors:
            print(f"\n{e}")
        print()
        return False

    return True


def get_local_ip():
    """Bilgisayarin yerel ag IP adresini bul."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    print()
    print("=" * 56)
    print("  NEURO-SENTINEL Voice Server")
    print("  Gemma3:4b + Whisper STT + Piper TTS")
    print("=" * 56)
    print()
    print("On kontroller yapiliyor...")

    if not check_prerequisites():
        print("\n[!] Yukaridaki hatalari cozup tekrar deneyin.")
        sys.exit(1)

    # IP bilgisi
    local_ip = get_local_ip()
    print(f"\n  [NET] Yerel IP: {local_ip}")
    print(f"  [NET] ESP32 firmware'da SERVER_HOST = \"{local_ip}\" olmali")

    print()
    print("-" * 56)
    print(f"  [WEB] Web UI:     http://localhost:8080")
    print(f"  [WEB] Agdan:      http://{local_ip}:8080")
    print(f"  [ESP] ESP32:      BOOT butonuna basili tutarak konusun")
    print(f"  [API] Saglik:     http://localhost:8080/health")
    print("-" * 56)
    print()

    # Sunucu dizinine gec ve baslat
    server_dir = Path(__file__).parent / "llm-voice-server" / "llm-voice-server"

    # sys.path'e ekle
    sys.path.insert(0, str(server_dir))

    # uvicorn ile baslat
    import uvicorn
    os.chdir(str(server_dir))
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8080,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
