# 🛠️ Rocky-S3 Complete Build Guide

End-to-end guide to 3D printing, wiring, flashing, calibrating, and running **Rocky-S3** — a 10-DOF bipedal ESP32-S3 desk robot with a `2.0" ST7789` animated face, onboard camera, predictive face tracking, and local Mac AI voice & vision studio.

---

## 📋 Overview & Bill of Materials

Before starting, review the hardware docs in [`cad/`](cad/):

- **[cad/ELECTRONICS_BOM.md](cad/ELECTRONICS_BOM.md)** — Complete parts list (ESP32-S3 CAM, Waveshare 2.0" ST7789 LCD, PCA9685, 10× 9g servos, I2S mic/amp, power & fasteners)
- **[cad/AMAZON_WISHLIST.md](cad/AMAZON_WISHLIST.md)** — Shopping links for components
- **[cad/WIRING_DIAGRAM.md](cad/WIRING_DIAGRAM.md)** — Detailed schematic, wire routing through the neck, and safety checklist
- **[wiring_diagram.html](wiring_diagram.html)** — Interactive browser wiring diagram

### Core Hardware Stack
| Subsystem | Component | Notes |
| :--- | :--- | :--- |
| **MCU + Camera** | ESP32-S3-WROOM CAM (`N16R8`) | `16 MB` QIO Flash, `8 MB` Octal PSRAM, OV2640/OV3660 |
| **Face Display** | Waveshare `2.0"` ST7789 IPS LCD | `320×240` RGB565 SPI visor (`RockyFaceEngine`, 14 moods) |
| **Servos (10-DOF)** | `9× MG90S` + `1× CyberBrick PG001` | Driven over I2C by **PCA9685** (`0x40`) with auto-relax |
| **Onboard Audio** | `INMP441` I2S Mic + `MAX98357A` 3W Amp | Shared full-duplex `I2S0` → `28 mm 8Ω` chest speaker |
| **Power** | `2S 7.4V 14500` Li-Ion + `5V 5A` UBEC | With `XH2.54` power switch, `2S 8.4V` USB-C charger & `1000 µF` cap |
| **AI Host** | Apple Silicon Mac (macOS) | Runs local Ollama (`qwen2.5:1.5b` + `qwen3-vl:2b-instruct`), Whisper, Piper+RVC, and FastAPI Studio |

---

## 🖨️ Step 1 — 3D Printing (`cad/`)

Pre-sliced multi-part `.3mf` plates live in `cad/output/3mf/`:

### Recommended Print Plates
- **Color-grouped plates (fastest workflow):**
  - `cad/output/3mf/plate_1_white.3mf` — Main white body shell, head, torso, arms, legs
  - `cad/output/3mf/plate_2_black.3mf` — Visor bezel, dark accents, joints
  - `cad/output/3mf/plate_3_blue.3mf` — Accent parts / headphones
- **All-in-one plate:** `cad/output/3mf/marvin_print_plate.3mf` (open in Bambu Studio → choose **No** to keep separate objects → press **`A`** to auto-arrange).
- **Reference assemblies (non-print):** `marvin_assembled.3mf` and `marvin_with_internals.3mf`.

### Slicer Settings
- **Layer height:** `0.16–0.20 mm`
- **Walls / Perimeters:** `3`
- **Infill:** `15–20%` Gyroid
- **Supports:** Tree (auto) on overhangs
- **Material:** PLA or PETG

### Optional: Regenerate CAD from Source
If you edit parameters in `cad/src/marvin_cad/params.py`:

```bash
cd cad
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python build123d trimesh numpy
PYTHONPATH=src .venv/bin/python scripts/build_all.py
```

---

## 🔌 Step 2 — Electronics Wiring

All pin definitions are verified against [`include/marvin_pins.h`](include/marvin_pins.h).

> **Reserved ESP32-S3-WROOM CAM pins (DO NOT wire anything here):**  
> Camera: `GPIO 4–13, 15–18` • Octal PSRAM: `GPIO 35–37` • Onboard WS2812: `GPIO 48` • USB-UART / USB-OTG: `GPIO 43–44, 19–20`

### Waveshare 2.0" ST7789 IPS LCD (`SPI`)
| LCD Pin | ESP32-S3 GPIO |
| :--- | :--- |
| `VCC` | `3V3` |
| `GND` | `GND` |
| `DIN (MOSI)` | `GPIO 47` |
| `CLK (SCLK)` | `GPIO 21` |
| `CS` | `GPIO 41` |
| `DC` | `GPIO 42` |
| `RST` | `GPIO 2` |
| `BL` | `GPIO 45` |

### PCA9685 16-Channel Servo Driver (`I2C @ 0x40`)
| PCA9685 Pin | Connection | Notes |
| :--- | :--- | :--- |
| `VCC` | ESP32-S3 `3V3` | **Must be 3.3V** for safe I2C logic |
| `GND` | Common `GND` | Shared across ESP32, PCA9685, and UBEC |
| `SDA` | `GPIO 1` | Through neck wire arch |
| `SCL` | `GPIO 14` | Through neck wire arch |
| `V+ / GND` (Terminal) | `5V 5A` UBEC | Add **`1000 µF 16V`** electrolytic cap across `V+` / `GND` |
| `OE` | *Leave floating* | Pulled low onboard (outputs enabled) |

### Shared Full-Duplex `I2S0` Audio (`ENABLE_SDMMC 0`)
| Device | Pin | ESP32-S3 GPIO / Connection |
| :--- | :--- | :--- |
| **INMP441 Mic** | `VDD` / `GND` / `L/R` | `3V3` / `GND` / bridge `L/R` to `GND` |
| **INMP441 Mic** | `SD` / `SCK` / `WS` | `GPIO 3` / `GPIO 38` / `GPIO 39` |
| **MAX98357A Amp** | `VIN` / `GND` | `5V` (UBEC) / Common `GND` |
| **MAX98357A Amp** | `BCLK` / `LRC` / `DIN` | `GPIO 38` / `GPIO 39` / `GPIO 40` |

### Servo Channel Map (`PCA9685 CH0–CH9`)
| Ch | Joint | Limits / Center |
| :--- | :--- | :--- |
| `CH 0` | Head Pan (Yaw) | `15°–165°` (`90°` center) |
| `CH 1` | Head Tilt (Pitch) | `58°–132°` (`90°` level, `>90°` up) |
| `CH 2` / `CH 3` | Left Shoulder Pitch / Roll | `90°` neutral |
| `CH 4` / `CH 5` | Right Shoulder Pitch / Roll | `90°` neutral |
| `CH 6` / `CH 7` | Left Hip Pitch / Ankle Roll | `90°` neutral |
| `CH 8` / `CH 9` | Right Hip Pitch / Ankle Roll | `90°` neutral |

### Pre-Power Safety Checks
1. **Leave all servo horns detached** until Step 4 calibration.
2. Verify with a multimeter: `PCA9685 VCC = 3.3V`, `PCA9685 V+ = 5.0V`, no shorts between `5V` and `3V3`.
3. Never draw servo current through the ESP32-S3 USB-C port — turn the battery switch OFF or disconnect `5V` from the ESP32 when flashing over USB.
4. Keep the robot power switch **OFF** while charging the `2S` pack over USB-C.

---

## ⚡ Step 3 — Build & Flash ESP32-S3 Firmware

### 1. Configure Network & Subsystems
Edit [`include/robot_config.h`](include/robot_config.h):
- Set `WIFI_SSID` and `WIFI_PASSWORD` to your local 2.4 GHz Wi-Fi network.
- Set `STATIC_IP` (default `192.168.4.40`), `STATIC_GATEWAY`, and `STATIC_SUBNET`.
- Keep `#define ENABLE_SDMMC 0`, `#define ENABLE_PCA9685 1`, `#define ENABLE_ST7789 1`.

### 2. First-Time USB-C Flash (PlatformIO)
```bash
# One-time PlatformIO environment setup
./scripts/setup_pio.sh

# Build firmware
./.venv/bin/pio run

# Upload over USB-C (update upload_port in platformio.ini if your tty differs)
./.venv/bin/pio run -t upload

# Optional: inspect boot logs
./.venv/bin/pio device monitor
```

### 3. Wireless Over-the-Air (OTA) Updates
Once Rocky-S3 is on Wi-Fi, you no longer need a USB cable to update firmware:

```bash
./.venv/bin/pio run
curl -f -F "firmware=@.pio/build/esp32s3_cam/firmware.bin" http://192.168.4.40/ota
```

### 4. Verify Hardware Online
```bash
curl -s http://192.168.4.40/status
```
Expected fields include `"robot": "Rocky-S3"`, `"servos_relaxed": true`, `"wifi_rssi"`, `"free_heap"`, `"free_psram"`, and `"reset_reason"`.

*(If Wi-Fi connection fails, the ESP32-S3 falls back to AP **`Rocky-Robot-AP`** at `http://192.168.4.1`.)*

---

## 🔧 Step 4 — Servo Calibration & Mechanical Assembly

Rocky-S3 includes an **Assembly Mode** that holds all 10 servos rigidly at `90°` center without auto-relaxing, making horn alignment effortless:

1. Power on Rocky-S3 with servo horns removed.
2. Enable Assembly Mode via either:
   - **Rocky Studio UI:** `Settings → Robot & Display → Servo Assembly Mode` (ON), or
   - **HTTP API:**
     ```bash
     curl -X POST http://192.168.4.40/assembly \
       -H 'Content-Type: application/json' \
       -d '{"enabled": true}'
     ```
3. Wait for all servos to settle at `90°`, then press and screw each horn onto its joint facing straight/neutral.
4. **Turn Assembly Mode OFF** once assembly is finished so PCA9685 auto-relaxation re-enables (keeps servos silent, cool, and extends battery life).

---

## 🧠 Step 5 — Set Up Rocky Studio AI Voice & Vision Server (`voice/`)

Rocky's brain runs locally on your Mac (`http://127.0.0.1:7860`) using Python 3.12, local Ollama, Faster-Whisper STT, and Piper + RVC ONNX voice synthesis.

### 1. Python Environment & Local Models
```bash
cd voice
python3.12 -m venv .venv
./.venv/bin/pip install fastapi uvicorn httpx pydantic numpy opencv-python faster-whisper onnxruntime soundfile scipy
```

Ensure local Ollama has Rocky's text and vision models pulled:
```bash
./bin/ollama pull qwen2.5:1.5b
./bin/ollama pull qwen3-vl:2b-instruct
```
*(Or use system `ollama` if installed globally.)*

### 2. Launch Rocky Studio
```bash
cd voice
./start_rocky.sh
```
This starts local Ollama (`127.0.0.1:11434`), boots the FastAPI voice/vision server on port `7860`, and opens **`http://127.0.0.1:7860`**.

### 3. Rocky Studio Dashboard Tour
| Tab | What You Can Do |
| :--- | :--- |
| **Live** | Interactive 2x-supersampled Rocky visor (mirrored to ST7789 LCD), 14-mood strip, hands-free Alexa-style wake/follow-up mic, live camera feed, pan/tilt sliders, Dance, Face Tracking, and chat transcript with 1-click **`👎 Fix`** on any reply |
| **Teach & Brain** | Manage spoken/saved behavior rules (*"From now on, always..."*), live-edit & hot-reload `voice/characters/rocky.json`, switch between free offline **Local Ollama** and optional Cloud Brains (**Gemini / OpenRouter / OpenAI**) with automatic Ollama fallback, and trigger guarded **`agy`** code Auto-Fixes on flagged turns |
| **Vision & Memory** | Switch between **ESP32-CAM** and **MacBook Pro** camera, arm **Motion Guard**, enroll/delete biometric face profiles, snap photos to SD |
| **Settings** | Voice pitch/speed, Eridian filter intensity, VAD mic threshold meter, wake mode (`Wake` / `Continuous` / `Push-to-Talk`), voice barge-in, robot IP, emotional body language, **Servo Assembly Mode**, and `/emulator` 320×240 LCD bench |
| **Log** | Live ESP32-S3 hardware vitals (uptime, heap, PSRAM, Wi-Fi RSSI, reset reason, servo state) + live color-coded server event log |

---

## ✅ Step 6 — Verification & Automated Tests

Run the 100% offline test suite (no hardware, camera, or network required):

```bash
/Users/dad/rocky-robot/voice/.venv/bin/python -m unittest discover -s /Users/dad/rocky-robot/voice/tests -v
```

Quick live smoke checks once the server and robot are running:
```bash
# Brain & Studio health
curl -s http://127.0.0.1:7860/api/health

# Mirror an expression to the physical ST7789 LCD
curl -s -X POST http://127.0.0.1:7860/api/robot/face \
  -H 'Content-Type: application/json' \
  -d '{"mood": "excited"}'

# Trigger a head dance routine
curl -s -X POST http://127.0.0.1:7860/api/robot/dance \
  -H 'Content-Type: application/json' \
  -d '{"routine": "disco"}'
```

---

## 🩺 Troubleshooting

| Symptom | Likely Cause & Fix |
| :--- | :--- |
| **ESP32 reboots when servos move** | Power brownout. Confirm `5V 5A` UBEC is wired to PCA9685 `V+` with a `1000 µF` capacitor and battery is charged (`>7.2V`). Check `reset_reason` in `/status`. |
| **Servos hum/buzz while idle** | Verify **Assembly Mode** is OFF in `Settings` so PCA9685 auto-relaxation releases PWM after moves finish. |
| **LCD blank or garbled** | Double-check ST7789 pins against `include/marvin_pins.h` (`MOSI 47, SCLK 21, CS 41, DC 42, RST 2, BL 45`) and verify `3V3` supply. |
| **Camera stream shows offline** | Verify `robot_ip` in `Settings` matches `/status`, try toggling `Route: Proxy` vs `Direct` in **Vision & Memory**, or switch source to **MacBook Pro** to test vision locally. |
| **Rocky hears his own voice** | Lower `Mic VAD Sensitivity` only slightly, keep `Barge-In` off when using Mac speakers without headphones, or switch output mode to **Browser Audio** with headphones. |
| **Cloud Brain not activating** | Paste your API key in **Teach & Brain → Smart Brain Provider** and click **Apply Brain Settings**. Keys are saved locally in `voice/settings.json` (git-ignored) and masked in all API responses. |
