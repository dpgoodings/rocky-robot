# 🤖 Rocky-S3: Bipedal AI Desk Robot & Studio

Unified 3D-printable 10-DOF bipedal hardware, ESP32-S3 edge firmware (`ST7789` animated visor, OV2640/OV3660 camera, PCA9685 servo motion, Wi-Fi OTA), and local Mac AI voice/vision studio (**Rocky Studio**).

👉 **For the full step-by-step hardware, wiring, firmware, calibration, and software guide, see [BUILD.md](BUILD.md).**

---

## ✨ Key Features

- **Expressive Rocky v2 Face (`320×240` ST7789 + Browser Mirror):** 14 moods (`neutral`, `happy`, `excited`, `curious`, `thinking`, `surprised`, `worried`, `sad`, `angry`, `skeptical`, `love`, `confused`, `sleepy`, `asleep`), blink physics, pupils/glints, syllable lip-sync mouth, and non-blocking SPI rendering.
- **10-DOF Motion & Auto-Relax (`PCA9685`):** Smooth eased head pan/tilt, 4 dance routines (`groove`, `robot`, `sprinkler`, `disco`), emotional speaking body language, **Assembly Mode** (`90°` calibration lock), and automatic idle servo relaxation.
- **Predictive Vision & Face Memory:** Latency-compensated `SlewModel` 2-axis face tracking, hand-wave greetings, Motion Guard sentry, YuNet/SFace biometric face enrollment, and 1-hop VLM scene/OCR reading mode.
- **Alexa-Style Local Voice Pipeline:** Faster-Whisper STT + Piper & RVC ONNX voice synthesis + adaptive turn endpointing, follow-up listening window, barge-in support, and deterministic live clock/calendar answers.
- **Teach & Brain Studio (`http://127.0.0.1:7860`):**
  - Teach Rocky rules out loud (*"From now on, always..."*, *"Remember to..."*, *"Never say..."*) saved to SQLite and applied immediately in chat & vision.
  - 1-click **`👎 Fix`** on any Rocky chat bubble to teach a rule, flag a turn, or dispatch a guarded code fix via Antigravity CLI (`agy`).
  - Live-editable personality prompt (`voice/characters/rocky.json`) with instant hot-reload.
  - Optional **Smart Brain Provider** toggle: free offline **Local Ollama** (`qwen2.5:1.5b` + `qwen3-vl:2b-instruct`) by default, or **Gemini / OpenRouter / OpenAI** with automatic silent fallback to Ollama.

---

## 🚀 Quick Start

### 1. Flash ESP32-S3 Firmware (USB or Wireless OTA)
```bash
# Build firmware
./.venv/bin/pio run

# First-time USB flash
./.venv/bin/pio run -t upload

# Subsequent wireless OTA updates
curl -f -F "firmware=@.pio/build/esp32s3_cam/firmware.bin" http://192.168.4.40/ota
```

### 2. Start Rocky Studio (Mac Voice & Vision Server)
```bash
cd voice
./start_rocky.sh
```
Open **`http://127.0.0.1:7860`** (or **`http://127.0.0.1:7860/emulator`** for the 320×240 hardware LCD bench).

### 3. Run Offline Unit Tests
```bash
./voice/.venv/bin/python -m unittest discover -s voice/tests -v
```

---

## 🌐 ESP32-S3 HTTP Endpoints

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `http://<robot-ip>/` | `GET` | On-device Web Console with live MJPEG stream & controls |
| `http://<robot-ip>:81/stream` | `GET` | Live MJPEG video stream |
| `http://<robot-ip>/capture` | `GET` | Single JPEG frame snapshot |
| `http://<robot-ip>/status` | `GET` | JSON telemetry (`robot`, `uptime_sec`, `free_heap`, `free_psram`, `wifi_rssi`, `reset_reason`, `servos_relaxed`, `assembly_mode`) |
| `http://<robot-ip>/face` | `POST` | Set ST7789 Rocky v2 face (`mood`, `anim`, `gx`, `gy`, `speaking`) |
| `http://<robot-ip>/servo` | `POST` | Move head/body servos (`{"pan": 90, "tilt": 90, "speed": 180}` or `{"ch": 0, "angle": 90}`) |
| `http://<robot-ip>/action` | `POST` | Execute gestures & dances (`dance`, `dance_groove`, `dance_robot`, `dance_sprinkler`, `dance_disco`, `nod`, `shake`, `wave`, `home`, etc.) |
| `http://<robot-ip>/assembly` | `POST` | Toggle 90° servo calibration lock (`{"enabled": true\|false}`) |
| `http://<robot-ip>/ota` | `POST` | Multipart firmware binary upload for wireless OTA updates |

---

## 📚 Documentation & Project Structure

- **[BUILD.md](BUILD.md)** — Complete build, print, wiring, flashing, calibration & studio guide
- **[cad/ELECTRONICS_BOM.md](cad/ELECTRONICS_BOM.md)** — Full hardware BOM
- **[cad/AMAZON_WISHLIST.md](cad/AMAZON_WISHLIST.md)** — Component shopping links
- **[cad/WIRING_DIAGRAM.md](cad/WIRING_DIAGRAM.md)** — Power/signal wiring schematic & GPIO map
- **[wiring_diagram.html](wiring_diagram.html)** — Interactive browser wiring diagram

```text
rocky-robot/
├── BUILD.md                        # Complete step-by-step build & setup guide
├── cad/                            # Parametric build123d CAD, STL/3MF plates, BOM & wiring
├── include/
│   ├── camera_pins.h               # OV2640/OV3660 camera GPIO map
│   ├── marvin_pins.h               # PCA9685 (1/14), ST7789 (47/21/41/42/2/45), I2S & CH0–CH9
│   └── robot_config.h              # Wi-Fi, static IP & hardware toggles
├── src/
│   ├── main.cpp                    # HTTP/MJPEG/OTA server + /face, /servo, /action, /assembly
│   ├── marvin_motion.h             # PCA9685 10-DOF motion, dances, easing & auto-relax
│   └── marvin_face.h               # Non-blocking SPI ST7789 320x240 RockyFaceEngine (14 moods)
└── voice/                          # Rocky Studio AI Brain, Voice, Vision & Web Dashboard
    ├── server.py                   # FastAPI backend (port 7860)
    ├── brain.py                    # Local Ollama + optional Cloud Brain, intents, OCR & vision
    ├── memory.py                   # SQLite facts, visual memories, spoken rules & feedback flags
    ├── face_tracker.py             # Latency-compensated predictive SlewModel face tracking
    ├── body_language.py            # Subtle emotional head gestures while thinking/speaking
    ├── turn_detector.py            # Adaptive acoustic prosody turn-completion detector
    ├── robot_vision.py             # YuNet/SFace face recognition, expressions, wave & Motion Guard
    ├── rocky_filter.py             # Eridian speech cadence & wake-word parser
    ├── voice_engine.py             # Faster-Whisper STT + Piper/RVC ONNX voice synthesis
    ├── characters/rocky.json       # Live-editable Rocky persona config
    ├── static/index.html           # 5-tab Rocky Studio dashboard (Live, Teach & Brain, Vision, Settings, Log)
    ├── static/emulator.html        # 320x240 ST7789 hardware LCD mirror & bench
    └── tests/test_rocky_suite.py   # Offline unit test suite (22 tests)
```
