# 🤖 Rocky-S3 & Marvin: Bipedal AI Robot Assistant

Unified hardware, 3D-printable CAD, ESP32-S3 edge firmware, and local Mac AI voice/vision platform (`~/rocky-voice` / `voice/`). Supports both **Rocky-S3** exploration/vision and the **Marvin-inspired 10-DOF Bipedal Waddle Robot** with faux studio headphones, `2.0" ST7789` GLOOM-MO facial expressions, and 2-axis camera face tracking.

---

## 📋 Hardware Stack (Marvin Bipedal Build)
- **MCU + Camera:** Compact Dual-USB-C **ESP32-S3-WROOM CAM (N16R8)** (`16 MB` Flash, `8 MB` Octal PSRAM, OV2640/OV3660) mounted vertically inside `head_front`
- **Actuators:** **10× 9g 180° Micro Servos** (`9× MG90S` metal-gear + `1× CyberBrick PG001`) driven over I2C by a **PCA9685 16-Channel PWM Driver** (`GPIO 1 SDA`, `GPIO 2 SCL`)
- **Face Display:** **Waveshare 2.0" ST7789 IPS LCD** (`320×240` landscape SPI: `MOSI 47, SCLK 21, CS 14, DC 41, RST 42`)
- **Voice Audio:** **INMP441 I2S Mic** (`SD 3`) + **MAX98357A I2S 3W Amp** (`DIN 40`) sharing `BCLK 38` & `LRC 39` (`ENABLE_SDMMC 0`) → **28 mm 8Ω Chest Speaker**
- **Power:** **CyberBrick 2S `14500 7.4V 800mAh` Li-Ion** + **CyberBrick `XH2.54` Power Switch** → **`5V 5A` UBEC** + onboard **`2S 8.4V` USB-C Boost Charger**

See detailed docs in `cad/`:
- [cad/ELECTRONICS_BOM.md](cad/ELECTRONICS_BOM.md) — Full hardware BOM tailored to CyberBrick Ultimate Kit + ESP32-S3 CAM
- [cad/AMAZON_WISHLIST.md](cad/AMAZON_WISHLIST.md) — Direct Amazon shopping links for remaining parts
- [cad/WIRING_DIAGRAM.md](cad/WIRING_DIAGRAM.md) — Complete power/signal schematic, GPIO map, & safety checklist

---

## 🖨️ 1. 3D Printing & CAD (`cad/`)

Ready-to-slice files (20 watertight parts, `372.6 cm³` total solid volume):
- **Print Plate:** `cad/output/3mf/marvin_print_plate.3mf` — Open in Bambu Studio, choose **No** (separate objects), press **`A`** to auto-arrange across 2–3 beds, and slice at `0.16–0.20 mm` with `3` walls, `15–20%` gyroid infill, and **Tree (auto)** supports.
- **Individual Parts:** `cad/output/3mf/*.3mf` and `cad/output/stl/*.stl`
- **Full Assemblies:** `cad/output/3mf/marvin_assembled.3mf` and `cad/output/3mf/marvin_with_internals.3mf`

To regenerate STL/3MF files and the interactive X-Ray/Waddle/Face-Track 3D viewer (`cad/output/viewer.html`) after editing `cad/src/marvin_cad/params.py`:

```bash
cd cad
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python build123d trimesh numpy
PYTHONPATH=src .venv/bin/python scripts/build_all.py
```

---

## ⚡ 2. Build & Flash ESP32-S3 Firmware

1. Edit [include/robot_config.h](include/robot_config.h) to set your `WIFI_SSID`, `WIFI_PASSWORD`, and `ROCKY_BRAIN_HOST`.
   - Keep `#define ENABLE_SDMMC 0` when using `GPIO 38–40` for I2S audio (or set `1` if using SDMMC without I2S).
2. Compile and upload with PlatformIO:

```bash
# Set up PlatformIO once if needed: ./scripts/setup_pio.sh
./.venv/bin/pio run
./.venv/bin/pio run -t upload
./.venv/bin/pio device monitor
```

*(If Wi-Fi times out, the ESP32-S3 starts fallback AP **`Rocky-Robot-AP`** at `192.168.4.1`.)*

---

## 🌐 3. ESP32-S3 HTTP & Streaming Endpoints

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `http://<robot-ip>/` | `GET` | Web Console with live MJPEG stream, **Waddle / Nod / Wave / Sigh** & mood buttons |
| `http://<robot-ip>:81/stream` | `GET` | Live MJPEG video stream |
| `http://<robot-ip>/capture` | `GET` | Single JPEG frame snapshot |
| `http://<robot-ip>/status` | `GET` | JSON telemetry (heap, PSRAM, SD status, Wi-Fi RSSI) |
| `http://<robot-ip>/gesture` & `/action` | `POST` | Execute gait/gesture (`step_forward`, `step_backward`, `turn_left`, `turn_right`, `nod`, `shake`, `sigh`, `wave`, `bow`, `home`) |
| `http://<robot-ip>/servo` | `POST` | Direct servo angle target (`{"pan": 95, "tilt": 105}` or `{"ch": 0, "angle": 90}`) |
| `http://<robot-ip>/face` | `POST` | Set ST7789 GLOOM-MO expression (`{"mood": "bored"\|"sad"\|"happy"\|"surprised", "gx": 0.0, "gy": 0.0, "speaking": false}`) |
| `http://<robot-ip>/save` | `POST` | Save snapshot to MicroSD (when `ENABLE_SDMMC 1`) |

---

## 🧠 4. Start AI Brain, Marvin Voice & Face Tracking (`voice/`)

1. Start the local Ollama + FastAPI voice/vision server (with Marvin Alan Rickman persona `voice/characters/marvin.json`):

```bash
cd voice
./start_rocky.sh
```

2. Open `http://127.0.0.1:7860` and select **Marvin**, or toggle **2-axis camera face tracking** via API:

```bash
# Start real-time YuNet face tracking (steers CH0/CH1 servos & ST7789 pupils)
curl -X POST http://127.0.0.1:7860/api/robot/tracking \
  -H 'Content-Type: application/json' \
  -d '{"enabled": true, "robot_ip": "<robot-ip>"}'

# Stop face tracking
curl -X POST http://127.0.0.1:7860/api/robot/tracking \
  -H 'Content-Type: application/json' \
  -d '{"enabled": false}'
```

---

## 📁 Project Structure

```
rocky-robot/
├── cad/                        # Parametric build123d CAD, STL/3MF parts, BOM & Wiring
│   ├── src/marvin_cad/         # Modular CAD (head, torso, pelvis, legs, arms, backpack, headphones)
│   ├── scripts/build_all.py    # Rebuild & verify watertight STL/3MF + 3D viewer
│   ├── output/                 # Exported .stl & .3mf files
│   ├── ELECTRONICS_BOM.md
│   ├── AMAZON_WISHLIST.md
│   └── WIRING_DIAGRAM.md
├── include/
│   ├── camera_pins.h           # OV2640/OV3660 & SDMMC GPIO definitions
│   ├── marvin_pins.h           # PCA9685, ST7789, I2S & CH0-CH9 servo mapping
│   └── robot_config.h          # Wi-Fi credentials & subsystem toggles
├── src/
│   ├── main.cpp                # Dual HTTP/Stream server + gesture/servo/face endpoints
│   ├── marvin_motion.h         # PCA9685 10-servo driver, smooth easing & waddle gait
│   └── marvin_face.h           # Native SPI ST7789 320x240 GLOOM-MO face renderer
└── voice/                      # Local AI Brain, RVC Marvin Voice & Vision
    ├── server.py               # FastAPI backend (port 7860)
    ├── face_tracker.py         # YuNet face tracking -> ESP32 /servo & /face
    ├── tars_gestures.py        # Gesture catalog & spoken intent dispatch
    ├── robot_vision.py         # YuNet, SFace & facial expression recognition
    └── characters/marvin.json  # Marvin persona & RVC voice config
```
