import serial
import requests
import time
import re

# --- AYARLAR ---
SERIAL_PORT = 'COM9 ' # Kendi ESP32 portunla değiştir!
BAUD_RATE = 115200
OLLAMA_URL = "http://localhost:11434/api/generate"

def analyze_with_ai(temp, gas, sound, motion):
    # LLM'e vereceğimiz Sistem Promptu (Adli Tıp Karakteri)
    prompt = f"""
    You are 'Neuro-Sentinel', an expert forensic AI responsible for the safety of an industrial electrical panel.
    Current live sensor data:
    - Temperature: {temp} C
    - Pyrolysis Gas (MQ-135): {gas} (Baseline ~400, Critical >1500)
    - Acoustic Arcing (I2S Mic): {sound} (Baseline ~200, Critical >3000)
    - Human Presence: {'Yes' if motion == 1 else 'No'}

    Based on this data, provide a concise, maximum 3-4 sentence professional forensic analysis. If there is a hazard (elevated gas, heat, or acoustic arcing), recommend an immediate emergency action plan. Use highly technical and formal engineering terminology.
    """

    payload = {
        "model": "llama3",
        "prompt": prompt,
        "stream": False
    }

    print("\n[AI DÜŞÜNÜYOR...] Ollama yerel sunucusunda analiz yapılıyor...\n")
    
    try:
        response = requests.post(OLLAMA_URL, json=payload)
        if response.status_code == 200:
            result = response.json()
            print("🚨 [NEURO-SENTINEL ADLİ RAPOR] 🚨")
            print(result['response'])
            print("--------------------------------------------------\n")
        else:
            print(f"Ollama API Hatası: {response.status_code}")
    except Exception as e:
        print(f"Ollama'ya bağlanılamadı. Ollama'nın arka planda çalıştığından emin olun. Hata: {e}")

def main():
    print(f"ESP32-S3 Bekleniyor ({SERIAL_PORT})...")
    
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        print("Bağlantı Kuruldu! Sensör verileri dinleniyor...\n")
    except Exception as e:
        print(f"Seri port açılamadı! ESP-IDF terminalinin kapalı olduğundan emin olun. Hata: {e}")
        return

    # Sadece ciddi bir değişiklik olduğunda AI'ı tetiklemek için sayaç
    ai_cooldown = 0 

    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8').strip()
                
                # Sadece içinde [VERİ] geçen satırları yakala
                if "[VERİ]" in line:
                    print(line)
                    
                    # Düzenli ifade (Regex) ile sayıları metnin içinden çek
                    match = re.search(r'ISI:\s*([\d.]+)\s*C\s*\|\s*GAZ:\s*(\d+)\s*\|\s*SES \(ARK\):\s*(\d+)\s*\|\s*HAREKET:\s*(\d+)', line)
                    
                    if match:
                        temp = float(match.group(1))
                        gas = int(match.group(2))
                        sound = int(match.group(3))
                        motion = int(match.group(4))
                        
                        # Basit bir tetikleyici (Trigger) mekanizması
                        # Eğer gaz 1500'ü, ısı 35'i veya ses 3000'i geçerse AI'ı uyar!
                        if (gas > 1500 or temp > 35 or sound > 3000) and ai_cooldown == 0:
                            print("\n⚠️ KRİTİK EŞİK AŞILDI! Veriler Yapay Zekaya Gönderiliyor...")
                            analyze_with_ai(temp, gas, sound, motion)
                            ai_cooldown = 10 # AI'ı 10 döngü boyunca tekrar rahatsız etme (Spam engelleme)
                            
                        if ai_cooldown > 0:
                            ai_cooldown -= 1
                            
        except KeyboardInterrupt:
            print("Program sonlandırıldı.")
            break
        except Exception as e:
            pass

if __name__ == "__main__":
    main()