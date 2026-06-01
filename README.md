# NEURO-SENTINEL
### AI-Powered Local Forensic Node for Critical Environment Analysis
**CEN322 – Internet of Things | Recep Tayyip Erdogan University**
**Owners: Durdane Naz BABAOĞLU, Şevval ASİ** 

---

## Overview

NEURO-SENTINEL is a two-node distributed monitoring system for critical indoor environments such as server rooms, electrical cabinets, and chemical storage areas.

- **Node 1 – ESP32-S3 (Sensor Node):** Reads four sensors in real time, drives a color LCD display, and plays local alarm tones.
- **Node 2 – Host PC (AI Node):** Receives sensor data over the local Wi-Fi network and runs the Gemma 3 4B language model via Ollama to generate a forensic analysis report. No internet connection or external API is required.

---

## Hardware Requirements

| Component | Model | Quantity |
|---|---|---|
| Microcontroller | ESP32-S3 DevKitC-1 | 1 |
| PIR Motion Sensor | HC-SR501 | 1 |
| Gas / Air Quality Sensor | MQ-135 | 1 |
| Temperature Sensor | DS18B20 | 1 |
| Digital Microphone | INMP441 (I2S) | 1 |
| TFT LCD Display | ILI9341 (2.8 inch, 320x240) | 1 |
| Audio Amplifier | MAX98357A | 1 |
| Speaker | 1W / 4 Ohm | 1 |
| Breadboard | 830-point | 1 |
| Pull-up Resistor | 4.7 kOhm | 1 |
| Jumper Wires | Male-Male, Male-Female | As needed |
| Host Machine | PC / Laptop with 16 GB RAM (for Ollama) | 1 |

---

## Wiring Guide

### Power Rails (Breadboard)

| Rail | ESP32-S3 Pin | Breadboard Strip | Supplies |
|---|---|---|---|
| GND | GND | Blue (−) rail | All component grounds |
| 5V | VBUS / Vin | Left red (+) rail | HC-SR501, MQ-135 |
| 3.3V | 3V3 | Right red (+) rail | INMP441, ILI9341, MAX98357A, DS18B20 |

### GPIO Pin Connections

| ESP32-S3 GPIO | Component | Signal | Notes |
|---|---|---|---|
| GPIO 1 | HC-SR501 \[OUT\] | PIR digital output | HIGH = motion detected |
| GPIO 2 | MQ-135 \[AO\] | Gas analog output | ADC input, 0–3.3V |
| GPIO 4 | DS18B20 \[DATA\] | 1-Wire data | 4.7k pull-up resistor to 3.3V required |
| GPIO 5 | INMP441 \[SD\] | I2S0 audio data | Microphone data line |
| GPIO 6 | INMP441 \[SCK\] | I2S0 bit clock | |
| GPIO 7 | INMP441 \[WS\] | I2S0 word select | |
| GND rail | INMP441 \[L/R\] | Channel select | Tie to GND for left channel mono |
| GPIO 8 | ILI9341 \[RST\] | LCD reset | Active LOW |
| GPIO 9 | ILI9341 \[DC\] | Data/Command select | |
| GPIO 10 | ILI9341 \[CS\] | SPI chip select | Active LOW |
| GPIO 11 | ILI9341 \[MOSI\] | SPI data to LCD | |
| GPIO 12 | ILI9341 \[CLK\] | SPI clock | |
| GPIO 15 | MAX98357A \[DIN\] | I2S1 audio data | Speaker output |
| GPIO 16 | MAX98357A \[BCLK\] | I2S1 bit clock | |
| GPIO 17 | MAX98357A \[LRC\] | I2S1 word select | |
| GPIO 0 | BOOT button | Voice record trigger | Built-in button on DevKitC-1 |
| SPK +/− | 1W Speaker | Analog output | Connect to MAX98357A speaker terminals |

---

## Software Requirements

### ESP32-S3 Firmware
- [ESP-IDF v5.x](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/get-started/index.html)
- [VS Code](https://code.visualstudio.com/) with the [ESP-IDF Extension](https://marketplace.visualstudio.com/items?itemName=espressif.esp-idf-vscode-extension)

### Host PC Application
- Python 3.10 or later
- [Ollama](https://ollama.com/) (local LLM runtime)
- Python packages: `pip install requests pyserial pyttsx3 websockets`

---

## Step 1 – Configure the Firmware

Open `main/neuro_sentinel_main.c` and update the following lines at the top of the file:

```c
#define WIFI_SSID    "your_wifi_name"      // Your Wi-Fi network name
#define WIFI_PASS    "your_wifi_password"  // Your Wi-Fi password
#define SERVER_HOST  "192.168.x.x"         // IP address of your host PC
#define SERVER_PORT  8080
```

To find your host PC's IP address:
- **Windows:** open Command Prompt and run `ipconfig`
- **macOS / Linux:** open Terminal and run `ifconfig` or `ip addr`

---

## Step 2 – Build and Flash the Firmware

Connect the ESP32-S3 to your computer via USB-C cable.

**Using VS Code ESP-IDF Extension:**
1. Open the project folder in VS Code.
2. Press `Ctrl+Shift+P` → `ESP-IDF: Set Espressif Device Target` → select `esp32s3`.
3. Press `Ctrl+Shift+P` → `ESP-IDF: Build, Flash and Monitor`.

**Using the terminal:**
```bash
cd neuro_iot-main
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor   # replace with your port (COMx on Windows)
```

If the flash is successful, the LCD will display "NEURO-SENTINEL" in the header bar and the four sensor boxes will appear.

---

## Step 3 – Set Up Ollama and Gemma 3 4B

1. Download and install Ollama from [https://ollama.com](https://ollama.com).
2. Open a terminal on the host PC and pull the model:

```bash
ollama pull gemma3:4b
```

3. Verify that Ollama is running:

```bash
ollama list
```

You should see `gemma3:4b` in the list. Ollama runs as a background daemon on `http://localhost:11434`. No API key or internet connection is needed after the model is downloaded.

---

## Step 4 – Run the Host PC Application

Open `ai_dashboard.py` and update the serial port at the top if you are using serial monitoring:

```python
SERIAL_PORT = 'COM9'   # Windows example — change to your port
                        # macOS/Linux: '/dev/ttyUSB0' or '/dev/tty.usbserial-xxxx'
```

Then run the application:

```bash
python ai_dashboard.py
```

The dashboard window will open and begin listening for sensor data from the ESP32-S3.

---

## Step 5 – Testing the System

### Normal State
With all sensors in safe conditions (room temperature, clean air, no loud sounds), all four sensor boxes on the LCD should display in **green**. The dashboard shows no alert.

### Warning State
Breathe near the MQ-135 or bring a warm object close to the DS18B20. One or two sensor boxes will turn **orange**. No alarm tone plays yet.

### Critical Alert
Trigger at least three sensors simultaneously (e.g., elevated gas + temperature + acoustic sound). The ESP32-S3 will:
- Play a **three-pulse 880 Hz alarm tone** through the speaker.
- Turn the affected sensor boxes **red** on the LCD.

On the host PC, if an individual trigger threshold is crossed (`gas_raw > 1500`, `temp > 35°C`, or `audio_level > 3000`), the AI pipeline activates:
- Gemma 3 4B generates a forensic analysis report (takes approximately 8–14 seconds).
- The report is displayed on the dashboard and **read aloud** by the TTS engine.

### Voice Command (optional)
1. Hold the **BOOT button** on the ESP32-S3 DevKitC-1.
2. Speak your query (up to 4 seconds).
3. Release the button — the recording is sent to the host PC via WebSocket.
4. The spoken response plays through the speaker.

---

## Project File Structure

```
neuro_iot-main/
├── CMakeLists.txt              Top-level CMake build file
├── ai_dashboard.py             Host PC application (AI + TTS + GUI)
├── main/
│   ├── CMakeLists.txt          Component registration
│   ├── neuro_sentinel_main.c   Main ESP32-S3 firmware
│   ├── ds18b20_test.c          Standalone DS18B20 test
│   └── ili9341_test.c          Standalone ILI9341 SPI test
└── README.md                   This file
```

---

## Alarm Threshold Reference

| Sensor | Warning (Yellow) | Critical (Red) |
|---|---|---|
| Temperature (DS18B20) | >= 29.0 °C | > 31.5 °C |
| Gas raw ADC (MQ-135) | >= 800 | > 1200 |
| Acoustic level (INMP441) | >= 1000 | > 2500 |
| Motion (HC-SR501) | Detected | — |

**AI trigger thresholds (host PC):** `gas_raw > 1500` OR `temperature > 35°C` OR `audio_level > 3000`

Alarm fires when: 3+ yellow sensors active, OR 2+ red sensors with 1+ yellow, OR 3+ red sensors.

---

## Troubleshooting

**LCD shows nothing after flash:**
Check that RST (GPIO 8) and DC (GPIO 9) wires are connected correctly. Verify 3.3V supply on the right breadboard rail.

**Temperature always reads -99.0:**
The DS18B20 data line (GPIO 4) requires a 4.7 kOhm pull-up resistor to 3.3V. Without it the 1-Wire communication will fail.

**No sound from speaker:**
Confirm MAX98357A BCLK=GPIO16, LRC=GPIO17, DIN=GPIO15. Check that the speaker wires are connected to the amplifier output terminals, not the I2S input pins.

**Dashboard does not receive data:**
Make sure the ESP32-S3 and host PC are on the same Wi-Fi network. Verify `SERVER_HOST` in the firmware matches the host PC's current IP address.

**Ollama model not found:**
Run `ollama pull gemma3:4b` again and wait for the download to complete before starting `ai_dashboard.py`.

---

## License

This project was developed for academic purposes as part of the CEN322 Internet of Things course at Recep Tayyip Erdogan University.

---

*The complete source code is publicly available at: https://github.com/drdnaz/neuro_iot*
