"""
llm.py — Ollama LLM client with conversation history
Model: gemma3:4b (default) — change MODEL_NAME to use another
Endpoint: http://localhost:11434/api/chat
"""

import logging
import httpx

log = logging.getLogger("llm")

OLLAMA_URL  = "http://localhost:11434/api/chat"
MODEL_NAME  = "gemma3:4b"
TIMEOUT_SEC = 60.0

SYSTEM_PROMPT = (
    "You are the AI assistant for NEURO-SENTINEL, an ESP32-based IoT security and monitoring system. "
    "The system monitors these sensors: DS18B20 (temperature), MQ-135 (gas/air quality), "
    "INMP441 (acoustic/sound), and PIR (motion). "
    "Alert thresholds — Temperature: >31.5°C CRITICAL, 29-31.5°C WARM warning; "
    "Gas: >1200 CRITICAL, 800-1200 HIGH; "
    "Sound: >2500 ALARM, 1000-2500 WARNING; "
    "Motion: PIR=1 means motion detected. "
    "When sensor data is provided, evaluate the current status and answer accordingly. "
    "Keep your answers concise (2-4 sentences). Be accurate and helpful."
)


def _build_sensor_context(data: dict) -> str:
    parts = []
    if "temperature" in data:
        t = data["temperature"]
        status = "CRITICAL" if t > 31.5 else ("WARM" if t >= 29.0 else "NORMAL")
        parts.append(f"Temperature: {t:.1f}°C ({status})")
    if "gas_raw" in data:
        g = data["gas_raw"]
        status = "CRITICAL" if g > 1200 else ("HIGH" if g >= 800 else "CLEAN")
        parts.append(f"Gas: {g} raw ({status})")
    if "audio_level" in data:
        a = data["audio_level"]
        status = "ALARM" if a > 2500 else ("WARNING" if a >= 1000 else "QUIET")
        parts.append(f"Sound level: {a} ({status})")
    if "motion_detected" in data:
        parts.append(f"Motion: {'DETECTED' if data['motion_detected'] else 'NONE'}")
    return "\n".join(parts)


async def generate(text: str, history: list[dict] | None = None, sensor_data: dict | None = None) -> str:
    """
    Generate a response.
    history: list of {"role": "user"|"assistant", "content": "..."}
    sensor_data: latest sensor readings from ESP32 (optional)
    Returns AI response text.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if sensor_data:
        ctx = _build_sensor_context(sensor_data)
        if ctx:
            messages.append({"role": "system", "content": f"Current sensor readings:\n{ctx}"})

    if history:
        messages.extend(history[-10:])  # last 5 turns

    messages.append({"role": "user", "content": text})

    payload = {
        "model":    MODEL_NAME,
        "stream":   False,
        "options":  {"temperature": 0.7, "num_predict": 150},
        "messages": messages,
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            answer = resp.json()["message"]["content"].strip()
            return answer
    except httpx.ConnectError:
        log.warning("Ollama not running — fallback active")
        return _fallback(text)
    except Exception as e:
        log.error(f"LLM error: {e}", exc_info=True)
        return _fallback(text)


def _fallback(text: str) -> str:
    low = text.lower()
    if any(w in low for w in ["hello", "hi", "hey"]):
        return "Hello! I'm the NEURO-SENTINEL assistant. How can I help you?"
    if "sensor" in low or "temperature" in low or "gas" in low or "motion" in low:
        return "I need Ollama running to read sensor data. Please run 'ollama serve' in a terminal."
    return (
        "Sorry, the AI backend is not available. "
        "Please start Ollama: run 'ollama serve' in a terminal."
    )
