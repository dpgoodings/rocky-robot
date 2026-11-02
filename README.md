# 🤖 Rocky-S3: Autonomous AI Agent Robot

A high-performance physical AI robot system modeled after **TARS** and **Rocky**, powered by an **ESP32-S3 WROOM CAM** (with onboard MicroSD card) and connected to your local AI brain in `~/rocky-voice`.

---

## 📋 Detected Hardware Specifications
- **MCU:** ESP32-S3 (revision v0.2), Dual-core Xtensa LX7 @ 240 MHz
- **Flash:** 16 MB Quad SPI Flash
- **PSRAM:** 8 MB Octal PSRAM (embedded `AP_3v3`)
- **Sensors:** OV2640 Camera (ESP32S3-EYE pin layout)
- **Storage:** MicroSD Card slot via hardware 1-bit `SD_MMC` (GPIO 38 CMD, 39 CLK, 40 D0)
- **USB Interface:** QinHeng CH343 High-Speed Serial (`/dev/cu.usbmodem5D160824921`)

---

## 🚀 Quick Start

### 1. Configure Wi-Fi & Brain IP
Open [include/robot_config.h](file:///Users/dad/rocky-robot/include/robot_config.h) and set:
- `WIFI_SSID` & `WIFI_PASSWORD`
- `ROCKY_BRAIN_HOST` (IP of your Mac running `rocky-voice`, e.g. `http://192.168.1.100:7860`)

*(Note: If Wi-Fi is not configured or cannot connect, the robot automatically hosts its own Access Point: **Rocky-Robot-AP** at `192.168.4.1`)*

### 2. Set Up Build Environment
Run the setup script to initialize PlatformIO in a local `.venv`:
```bash
cd ~/rocky-robot
chmod +x scripts/*.sh scripts/*.py
./scripts/setup_pio.sh
```

### 3. Build & Flash
With the ESP32-S3 connected via USB:
```bash
# Compile firmware
./.venv/bin/pio run

# Upload to the ESP32-S3
./.venv/bin/pio run -t upload

# Open the live serial monitor (115200 baud)
./.venv/bin/pio device monitor
```

---

## 🌐 Web Endpoints & Streaming

Once booted and connected to Wi-Fi, the robot serves:

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `http://<robot-ip>/` | `GET` | **Interactive Robot Web Console** with live video & controls |
| `http://<robot-ip>/stream` | `GET` | **MJPEG Live Video Stream** (browser & OpenCV compatible) |
| `http://<robot-ip>/capture` | `GET` | Single JPEG frame snapshot (ideal for Ollama / LLM vision) |
| `http://<robot-ip>/save` | `POST` | Snaps a photo and writes `/sdcard/captures/photo_XXXX.jpg` |
| `http://<robot-ip>/status` | `GET` | JSON diagnostics (heap, PSRAM, SD card capacity, RSSI) |
| `http://<robot-ip>/sd/list` | `GET` | JSON catalog of all photos stored on the MicroSD card |
| `http://<robot-ip>/action` | `POST` | Hardware movement and reflex command endpoint |

---

## 🧠 Connecting to the `rocky-voice` AI Brain

Your Mac already has the AI brain ready in `~/rocky-voice`!
Run the bridge script to query the robot's vision using Rocky or TARS personas:

```bash
# Query the robot's camera and speak the observation
python3 scripts/robot_bridge.py --robot 192.168.1.xxx --character tars --prompt "What do you see?"

# Trigger a photo save to the robot's onboard SD card
python3 scripts/robot_bridge.py --robot 192.168.1.xxx --snap-sd

# Inspect live telemetry (PSRAM, SD card, Heap)
python3 scripts/robot_bridge.py --robot 192.168.1.xxx --status
```

---

## 📁 Project Structure

```
~/rocky-robot/
├── platformio.ini              # ESP32-S3 N16R8 PlatformIO configuration
├── include/
│   ├── camera_pins.h           # OV2640 & SDMMC GPIO definitions
│   └── robot_config.h          # Wi-Fi credentials & robot identity
├── src/
│   └── main.cpp                # Core firmware: Dual HTTP servers (port 80 command, port 81 stream)
├── scripts/
│   ├── setup_pio.sh            # PlatformIO CLI setup script
│   └── robot_bridge.py         # Mac bridge to Ollama & voice assistant
├── voice/                      # AI Brain, Voice Assistant & Web UI
│   ├── server.py               # FastAPI / Uvicorn backend (port 7860)
│   ├── brain.py                # Conversational LLM & Moondream VLM vision
│   ├── voice_engine.py         # Faster-Whisper, ONNX voice synthesis & Piper
│   ├── static/index.html       # Web UI Cyber-HUD with live robot stream
│   └── characters/             # Character persona definitions (Rocky, TARS, Marvin, BMO)
├── micropython/
│   └── README.md               # MicroPython setup guide & flash instructions
└── README.md                   # This documentation
```
