import serial
import requests
import time
import re
import pyttsx3
import threading
import queue
import asyncio
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

# --- SETTINGS / AYARLAR ---
SERIAL_PORT = 'COM9'  # Kendi ESP32 portunla değiştir!
BAUD_RATE = 115200
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "gemma3:4b"
WEB_PORT = 5000

app = FastAPI(title="Neuro-Sentinel Web Dashboard")

# Aktif WebSocket bağlantılarını takip etmek için
connected_clients = set()
loop = None  # Ana asyncio döngüsü referansı

# Thread'ler arası kuyruklar
speech_queue = queue.Queue()
running = True

# --- HTML / CSS / JS FRONTEND (Futuristic Glassmorphic Dark Dashboard) ---
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Neuro-Sentinel AI Dashboard</title>
    <!-- Google Fonts -->
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Share+Tech+Mono&family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #030712;
            --card-bg: rgba(17, 24, 39, 0.7);
            --border-color: rgba(75, 85, 99, 0.4);
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --color-green: #10b981;
            --color-orange: #f59e0b;
            --color-red: #ef4444;
            --glow-green: 0 0 15px rgba(16, 185, 129, 0.4);
            --glow-orange: 0 0 15px rgba(245, 158, 11, 0.4);
            --glow-red: 0 0 20px rgba(239, 68, 68, 0.6);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Inter', sans-serif;
            background-color: var(--bg-color);
            background-image: 
                radial-gradient(at 0% 0%, rgba(17, 24, 39, 0.8) 0, transparent 50%),
                radial-gradient(at 50% 0%, rgba(30, 41, 59, 0.5) 0, transparent 50%),
                radial-gradient(at 100% 0%, rgba(15, 23, 42, 0.8) 0, transparent 50%);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
        }

        /* Arka planda hareket eden hafif grid çizgileri */
        body::before {
            content: "";
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background-image: linear-gradient(rgba(255,255,255,0.01) 1px, transparent 1px),
                              linear-gradient(90deg, rgba(255,255,255,0.01) 1px, transparent 1px);
            background-size: 40px 40px;
            z-index: -1;
        }

        header {
            width: 100%;
            max-width: 1400px;
            margin: 0 auto;
            padding: 30px 40px 10px 40px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .header-left h1 {
            font-family: 'Orbitron', sans-serif;
            font-size: 1.8rem;
            font-weight: 900;
            letter-spacing: 2px;
            background: linear-gradient(90deg, #38bdf8, #3b82f6, #6366f1);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-transform: uppercase;
        }

        .header-left p {
            font-size: 0.85rem;
            color: var(--text-muted);
            margin-top: 4px;
        }

        .header-right {
            display: flex;
            align-items: center;
            gap: 15px;
        }

        .connection-badge {
            background: rgba(16, 185, 129, 0.1);
            border: 1px solid var(--color-green);
            color: var(--color-green);
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 700;
            letter-spacing: 1px;
            display: flex;
            align-items: center;
            gap: 8px;
            text-transform: uppercase;
            box-shadow: 0 0 10px rgba(16, 185, 129, 0.1);
        }

        .connection-badge.offline {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid var(--color-red);
            color: var(--color-red);
            box-shadow: 0 0 10px rgba(239, 68, 68, 0.1);
        }

        .connection-badge::before {
            content: "";
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: currentColor;
            animation: pulse 1.5s infinite ease-in-out;
        }

        @keyframes pulse {
            0%, 100% { opacity: 0.5; transform: scale(1); }
            50% { opacity: 1; transform: scale(1.2); }
        }

        main {
            width: 100%;
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px 40px;
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 30px;
        }

        /* 4'lü Sensör Grid Sistemi */
        .sensor-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
            width: 100%;
        }

        .sensor-card {
            background: var(--card-bg);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 24px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
            box-shadow: 0 4px 30px rgba(0, 0, 0, 0.3);
        }

        /* Kart Üstüne Hafif Işık Yansıması */
        .sensor-card::after {
            content: "";
            position: absolute;
            top: 0; left: 0; width: 100%; height: 100%;
            background: linear-gradient(135deg, rgba(255,255,255,0.05) 0%, transparent 100%);
            pointer-events: none;
        }

        .sensor-card:hover {
            transform: translateY(-5px);
            border-color: rgba(255, 255, 255, 0.2);
        }

        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
        }

        .card-title {
            font-size: 0.75rem;
            font-weight: 700;
            letter-spacing: 1.5px;
            color: var(--text-muted);
            text-transform: uppercase;
        }

        .card-title-tr {
            font-size: 0.65rem;
            color: #6b7280;
            margin-top: 2px;
        }

        .card-icon {
            font-size: 1.5rem;
            opacity: 0.7;
        }

        .card-value {
            font-family: 'Orbitron', sans-serif;
            font-size: 2.2rem;
            font-weight: 700;
            margin: 25px 0 10px 0;
            color: var(--text-main);
            text-shadow: 0 2px 10px rgba(0,0,0,0.5);
            display: flex;
            align-items: baseline;
        }

        .card-value .unit {
            font-size: 1rem;
            font-weight: 400;
            color: var(--text-muted);
            margin-left: 5px;
        }

        /* DİNAMİK DURUM RENKLERİ VE PULSING ANİMASYONLARI */
        .sensor-card.state-green {
            border-color: var(--color-green);
            box-shadow: var(--glow-green), inset 0 0 10px rgba(16, 185, 129, 0.05);
        }
        .sensor-card.state-green .card-icon { color: var(--color-green); }

        .sensor-card.state-orange {
            border-color: var(--color-orange);
            box-shadow: var(--glow-orange), inset 0 0 10px rgba(245, 158, 11, 0.05);
            animation: card-alert-orange 2s infinite ease-in-out;
        }
        .sensor-card.state-orange .card-icon { color: var(--color-orange); }

        .sensor-card.state-red {
            border-color: var(--color-red);
            box-shadow: var(--glow-red), inset 0 0 15px rgba(239, 68, 68, 0.1);
            animation: card-alert-red 1s infinite cubic-bezier(0.4, 0, 0.6, 1);
        }
        .sensor-card.state-red .card-icon { color: var(--color-red); }

        @keyframes card-alert-orange {
            0%, 100% { box-shadow: var(--glow-orange); }
            50% { box-shadow: 0 0 25px rgba(245, 158, 11, 0.6); }
        }

        @keyframes card-alert-red {
            0%, 100% { box-shadow: var(--glow-red); background: var(--card-bg); }
            50% { box-shadow: 0 0 35px rgba(239, 68, 68, 0.8); background: rgba(239, 68, 68, 0.05); }
        }

        /* Alt Rapor Bölümü */
        .report-section {
            background: var(--card-bg);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 30px;
            display: flex;
            flex-direction: column;
            gap: 20px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
            flex: 1;
            min-height: 350px;
        }

        .report-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            padding-bottom: 15px;
        }

        .report-title-container {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .report-title {
            font-family: 'Orbitron', sans-serif;
            font-size: 1.1rem;
            font-weight: 700;
            letter-spacing: 1px;
            color: #ef4444;
            text-transform: uppercase;
        }

        .pulse-red-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background-color: #ef4444;
            box-shadow: 0 0 8px #ef4444;
            animation: pulse 1s infinite;
        }

        .cooldown-badge {
            font-size: 0.85rem;
            font-weight: 700;
            background: rgba(16, 185, 129, 0.1);
            color: var(--color-green);
            padding: 6px 14px;
            border-radius: 30px;
            border: 1px solid rgba(16, 185, 129, 0.2);
            transition: all 0.3s;
        }

        .cooldown-badge.active {
            background: rgba(245, 158, 11, 0.15);
            color: var(--color-orange);
            border: 1px solid var(--color-orange);
            animation: pulse 2s infinite;
        }

        .report-body {
            flex: 1;
            background: #020617;
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            padding: 24px;
            font-family: 'Share Tech Mono', monospace;
            font-size: 1.05rem;
            line-height: 1.6;
            color: #e2e8f0;
            overflow-y: auto;
            white-space: pre-wrap;
            position: relative;
        }

        /* Terminal efekti */
        .report-body::after {
            content: "_";
            animation: blink 1s infinite step-start;
            color: var(--color-green);
            font-weight: 900;
        }

        @keyframes blink {
            50% { opacity: 0; }
        }

        footer {
            width: 100%;
            max-width: 1400px;
            margin: 0 auto;
            padding: 10px 40px 30px 40px;
            display: flex;
            justify-content: space-between;
            font-size: 0.75rem;
            color: var(--text-muted);
        }

        @media (max-width: 1024px) {
            .sensor-grid {
                grid-template-columns: repeat(2, 1fr);
            }
        }

        @media (max-width: 640px) {
            .sensor-grid {
                grid-template-columns: 1fr;
            }
            header {
                flex-direction: column;
                gap: 15px;
                align-items: flex-start;
            }
        }
    </style>
</head>
<body>

    <header>
        <div class="header-left">
            <h1>Neuro-Sentinel AI</h1>
            <p>Industrial Panel Emergency Action Plan Hub</p>
        </div>
        <div class="header-right">
            <div id="conStatus" class="connection-badge offline">Awaiting Signal</div>
        </div>
    </header>

    <main>
        <!-- 4'lü Sensör Kartı Gridi -->
        <section class="sensor-grid">
            <!-- 1. Sıcaklık -->
            <div id="cardTemp" class="sensor-card">
                <div class="card-header">
                    <div>
                        <div class="card-title">Temperature</div>
                        <div class="card-title-tr">Sıcaklık Kontrolü</div>
                    </div>
                    <div class="card-icon">🌡️</div>
                </div>
                <div class="card-value"><span id="valTemp">--</span><span class="unit">°C</span></div>
            </div>

            <!-- 2. Akustik ARC -->
            <div id="cardSound" class="sensor-card">
                <div class="card-header">
                    <div>
                        <div class="card-title">Acoustic ARC</div>
                        <div class="card-title-tr">Akustik Sıçrama</div>
                    </div>
                    <div class="card-icon">🎙️</div>
                </div>
                <div class="card-value"><span id="valSound">--</span><span class="unit">Val</span></div>
            </div>

            <!-- 3. PIR Hareket -->
            <div id="cardMotion" class="sensor-card">
                <div class="card-header">
                    <div>
                        <div class="card-title">PIR Motion</div>
                        <div class="card-title-tr">Panel İçi Hareket</div>
                    </div>
                    <div class="card-icon">👤</div>
                </div>
                <div class="card-value"><span id="valMotion">--</span></div>
            </div>

            <!-- 4. Gaz (MQ-135) -->
            <div id="cardGas" class="sensor-card">
                <div class="card-header">
                    <div>
                        <div class="card-title">Pyrolysis Gas</div>
                        <div class="card-title-tr">MQ-135 Gaz Seviyesi</div>
                    </div>
                    <div class="card-icon">⚠️</div>
                </div>
                <div class="card-value"><span id="valGas">--</span><span class="unit">ppm</span></div>
            </div>
        </section>

        <!-- Yapay Zeka Adli Eylem Planı Rapor Kutusu -->
        <section class="report-section">
            <div class="report-header">
                <div class="report-title-container">
                    <div class="pulse-red-dot"></div>
                    <div class="report-title">🚨 Forensic Action Plan / Adli Rapor</div>
                </div>
                <div id="cooldownBadge" class="cooldown-badge">Cooldown: Ready</div>
            </div>
            <div id="reportBox" class="report-body">System initialized. Awaiting sensor threshold triggers...</div>
        </section>
    </main>

    <footer>
        <div>© 2026 Neuro-Sentinel AI. Live Sensor Analytics Interface.</div>
        <div>Model: gemma3:4b (Local Ollama Engine)</div>
    </footer>

    <script>
        // WebSocket Bağlantısı
        let ws;
        function connect() {
            const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
            const wsUrl = `${proto}//${window.location.host}/ws`;
            ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                const conStatus = document.getElementById("conStatus");
                conStatus.textContent = "Telemetry Link Active";
                conStatus.classList.remove("offline");
            };

            ws.onmessage = (event) => {
                const msg = JSON.parse(event.data);
                
                if (msg.type === "sensors") {
                    updateSensors(msg);
                } else if (msg.type === "report_start") {
                    document.getElementById("reportBox").textContent = msg.content;
                } else if (msg.type === "report_chunk") {
                    document.getElementById("reportBox").textContent += msg.content;
                    const rBox = document.getElementById("reportBox");
                    rBox.scrollTop = rBox.scrollHeight;
                }
            };

            ws.onclose = () => {
                const conStatus = document.getElementById("conStatus");
                conStatus.textContent = "Connection Lost";
                conStatus.classList.add("offline");
                // 3 saniye sonra tekrar bağlanmayı dene
                setTimeout(connect, 3000);
            };
        }

        function updateSensors(data) {
            // Değerleri yazdır
            document.getElementById("valTemp").textContent = data.temp.toFixed(1);
            
            const memText = data.sound_memory > 0 ? ` (M:${data.sound_memory})` : "";
            document.getElementById("valSound").textContent = data.sound + memText;
            
            document.getElementById("valMotion").textContent = data.motion === 1 ? "PRESENCE" : "VACANT";
            document.getElementById("valGas").textContent = data.gas;

            // 1. SICAKLIK KART RENGİ
            const cardTemp = document.getElementById("cardTemp");
            cardTemp.className = "sensor-card";
            if (data.temp <= 24) cardTemp.classList.add("state-green");
            else if (data.temp <= 27) cardTemp.classList.add("state-orange");
            else cardTemp.classList.add("state-red");

            // 2. SES KART RENGİ
            const cardSound = document.getElementById("cardSound");
            cardSound.className = "sensor-card";
            if (data.sound <= 300 && data.sound_memory === 0) cardSound.classList.add("state-green");
            else if (data.sound <= 1000) cardSound.classList.add("state-orange");
            else cardSound.classList.add("state-red");

            // 3. HAREKET KART RENGİ
            const cardMotion = document.getElementById("cardMotion");
            cardMotion.className = "sensor-card";
            if (data.motion === 0) cardMotion.classList.add("state-green");
            else cardMotion.classList.add("state-red");

            // 4. GAZ KART RENGİ
            const cardGas = document.getElementById("cardGas");
            cardGas.className = "sensor-card";
            if (data.gas <= 350) cardGas.classList.add("state-green");
            else if (data.gas <= 700) cardGas.classList.add("state-orange");
            else cardGas.classList.add("state-red");

            // COOLDOWN GÜNCELLEMESİ
            const cooldownBadge = document.getElementById("cooldownBadge");
            if (data.cooldown > 0) {
                cooldownBadge.textContent = `Cooldown: ${data.cooldown}s`;
                cooldownBadge.classList.add("active");
            } else {
                cooldownBadge.textContent = "Cooldown: Ready";
                cooldownBadge.classList.remove("active");
            }
        }

        // Sayfa açıldığında bağlan
        connect();
    </script>
</body>
</html>
"""

# --- TEXT-TO-SPEECH (TTS) WORKER THREAD ---
def speech_worker():
    """pyttsx3 ses motorunu arka planda donmasız çalıştırır."""
    try:
        engine = pyttsx3.init()
        voices = engine.getProperty('voices')
        en_voice = None
        for voice in voices:
            if "EN" in voice.id.upper() or "ENGLISH" in voice.name.upper() or "ZIRA" in voice.name.upper() or "DAVID" in voice.name.upper():
                en_voice = voice
                break
        if en_voice:
            engine.setProperty('voice', en_voice.id)
        else:
            if len(voices) > 0:
                engine.setProperty('voice', voices[0].id)
        engine.setProperty('rate', 160)
    except Exception as e:
        print(f"[!] TTS Başlatılamadı: {e}")
        engine = None

    if engine:
        try:
            engine.say("System check. Neuro-Sentinel online.")
            engine.runAndWait()
        except Exception:
            pass

    while running:
        try:
            text = speech_queue.get(timeout=0.5)
            if engine and text:
                try:
                    engine.say(text)
                    engine.runAndWait()
                except Exception as ex:
                    print(f"[!] Seslendirme hatası: {ex}")
            speech_queue.task_done()
        except queue.Empty:
            continue

def speak(text):
    """Konuşma isteğini arka plan kuyruğuna ekler."""
    print(f"\n📢 [SESLİ UYARI]: \"{text}\"")
    speech_queue.put(text)

# --- SERIAL PORT SENSOR READER & MAIN TRIGGER LOGIC ---
def serial_worker():
    """Seri porttan gelen verileri okur, koşulları test eder ve WebSocket üzerinden dağıtır."""
    global loop
    print(f"[*] ESP32 Bağlantısı Bekleniyor ({SERIAL_PORT})...")
    
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        print("[+] Seri port başarıyla açıldı! Sensörler dinleniyor...\n")
    except Exception as e:
        print(f"[!] Seri port açılamadı! Portun başka program tarafından meşgul edilmediğinden emin olun. Hata: {e}")
        return

    ai_cooldown = 0
    sound_memory = 0

    while running:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    # Gelen her satırı konsola yazdır
                    print(line)
                    
                    temp, gas, sound, motion = None, None, None, None
                    
                    # Format 1: Yeni JSON formatı (Örn: {"temperature":25.75,"gas_raw":357,"audio_level":764,"motion_detected":1})
                    if "{" in line and ("temperature" in line or "gas_raw" in line):
                        try:
                            start_idx = line.find("{")
                            end_idx = line.rfind("}") + 1
                            json_data = json.loads(line[start_idx:end_idx])
                            temp = float(json_data.get("temperature", 0))
                            gas = int(json_data.get("gas_raw", 0))
                            sound = int(json_data.get("audio_level", 0))
                            
                            # motion_detected bool veya int gelebilir, güvenli okuma yapıyoruz
                            m_val = json_data.get("motion_detected", 0)
                            motion = 1 if (m_val is True or m_val == 1 or str(m_val).lower() == "true") else 0
                        except Exception as je:
                            print(f"[!] JSON ayrıştırma hatası: {je}")
                    
                    # Format 2: Klasik "[VERİ]" etiketli format
                    elif "[VERİ]" in line:
                        match = re.search(r'ISI:\s*([\d.]+)\s*C\s*\|\s*GAZ:\s*(\d+)\s*\|\s*SES:\s*(\d+)\s*\|\s*PIR:\s*(\d+)', line)
                        if match:
                            temp = float(match.group(1))
                            gas = int(match.group(2))
                            sound = int(match.group(3))
                            motion = int(match.group(4))
                    
                    # Değerler başarıyla yakalandıysa işlemleri gerçekleştir
                    if temp is not None:
                        # Ses hafızası time-window latch
                        if sound > 1000:
                            sound_memory = 20

                        # Web arayüzüne anlık veri paketi yayınla (Broadcast)
                        sensor_payload = {
                            "type": "sensors",
                            "temp": temp,
                            "sound": sound,
                            "motion": motion,
                            "gas": gas,
                            "sound_memory": sound_memory,
                            "cooldown": ai_cooldown
                        }
                        broadcast_to_web(sensor_payload)

                        # --- 3'LÜ TETİKLEME ŞARTI (Isı > 27, Ses Hafızası > 0, Hareket == 1) ---
                        if temp > 27 and sound_memory > 0 and motion == 1 and ai_cooldown == 0:
                            print("\n🚨 3'LÜ TETİKLEME GERÇEKLEŞTİ! ACİL DURUM SEQUENCE BAŞLADI... 🚨")
                            
                            # 1. İlk sesli uyarıyı hoparlörden çal
                            speak("Warning. Multi-sensor anomaly detected. Initiating local AI forensic analysis.")
                            
                            # 2. Arka planda Ollama LLM'i çalıştır ve arayüze harf harf akıt
                            threading.Thread(target=run_ollama_and_stream, args=(temp, gas, sound, motion), daemon=True).start()
                            
                            ai_cooldown = 20

                        if sound_memory > 0:
                            sound_memory -= 1
                        if ai_cooldown > 0:
                            ai_cooldown -= 1

            time.sleep(0.05)
        except Exception as e:
            print(f"[!] Worker Hatası: {e}")
            time.sleep(1)

def run_ollama_and_stream(temp, gas, sound, motion):
    """Ollama API'sine sorup yanıtı WebSocket üzerinden harf harf akıtır."""
    broadcast_to_web({"type": "report_start", "content": "🔄 Neuro-Sentinel AI generating forensic report...\n"})
    
    prompt = f"""
    You are 'Neuro-Sentinel', an expert forensic AI responsible for the safety of an industrial electrical panel.
    Current live sensor data:
    - Temperature: {temp} C (Threshold > 27.0 C)
    - Pyrolysis Gas (MQ-135): {gas} (Reference Value)
    - Acoustic Arcing (I2S Mic): {sound} (Threshold > 1000)
    - Human Presence (PIR): {'Yes' if motion == 1 else 'No'} (Threshold == 1)

    Analyze these ambient values immediately. You MUST provide clear, highly technical, and formal engineering recommendations (Forensic recommendations / Emergency Action Plan) specifically tailored for a maintenance engineer. Keep your response concise (maximum 3-4 sentences) and entirely in English.
    """

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=20)
        if response.status_code == 200:
            ai_response = response.json().get('response', '')
            
            # Harf harf web'e akıtarak premium bir arayüz deneyimi sunuyoruz
            broadcast_to_web({"type": "report_start", "content": ""}) # İçeriği temizle
            for char in ai_response:
                broadcast_to_web({"type": "report_chunk", "content": char})
                time.sleep(0.015) # Harf akış hızı (15ms)
            
            # Rapor tamamlanınca ikinci sesli bildirim
            speak("Forensic analysis complete. Recommendations are ready on the web dashboard.")
        else:
            broadcast_to_web({"type": "report_chunk", "content": f"Ollama API Error: {response.status_code}"})
    except Exception as e:
        broadcast_to_web({"type": "report_chunk", "content": f"Ollama Connection Failed! Ensure local service is running.\\nHata: {e}"})

def broadcast_to_web(data):
    """FastAPI WebSocket listesine thread-safe veri gönderir."""
    global loop
    if loop and connected_clients:
        coro = broadcast_async(data)
        asyncio.run_coroutine_threadsafe(coro, loop)

async def broadcast_async(data):
    """WebSocket istemcilerine asenkron yayın yapar."""
    msg = json.dumps(data)
    disconnected = set()
    for client in connected_clients:
        try:
            await client.send_text(msg)
        except Exception:
            disconnected.add(client)
    
    for client in disconnected:
        connected_clients.remove(client)

# --- FASTAPI ENDPOINTS ---
@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    return HTMLResponse(content=HTML_TEMPLATE)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    print(f"[+] Web arayüzü bağlandı! Toplam bağlantı: {len(connected_clients)}")
    try:
        while True:
            # WebSocket bağlantısını canlı tutmak ve kapatmaları yakalamak için
            await websocket.receive_text()
    except WebSocketDisconnect:
        connected_clients.remove(websocket)
        print(f"[-] Web arayüzü ayrıldı. Kalan bağlantı: {len(connected_clients)}")

# --- SERVER LIFECYCLE & START ---
if __name__ == "__main__":
    # Ana asyncio döngü referansını al
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Arka plan iş parçacıklarını başlat
    serial_thread = threading.Thread(target=serial_worker, daemon=True)
    speech_thread = threading.Thread(target=speech_worker, daemon=True)
    
    serial_thread.start()
    speech_thread.start()

    print(f"\n" + "="*60)
    print(f" 🚀 NEURO-SENTINEL WEB DASHBOARD BAŞLATILIYOR!")
    print(f" 👉 Web Arayüzü: http://localhost:{WEB_PORT}")
    print(f" 👉 Model: {OLLAMA_MODEL} | Port: {SERIAL_PORT}")
    print("="*60 + "\n")

    # FastAPI'yi uvicorn ile başlatıyoruz
    config = uvicorn.Config(app=app, host="0.0.0.0", port=WEB_PORT, log_level="warning")
    server = uvicorn.Server(config)
    
    # Event loop'u sunucuyla çalıştır
    loop.run_until_complete(server.serve())