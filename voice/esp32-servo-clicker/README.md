# ESP32 Servo Autoclicker 🤖

A physical button-pressing autoclicker powered by an ESP32 and a micro servo motor (SG90 or MG90S). Features a built-in mobile-friendly Web Dashboard, mDNS discovery, non-blocking timing, anti-buzz auto-detaching, randomized jitter, and a REST API for home automation / remote triggers.

---

## ⚡ Features

- **📱 Sleek Mobile Web Dashboard**: Accessible via browser (`http://autoclicker.local` or IP) with dark theme and live click countdown.
- **🔄 Single Click & Continuous Loop**: Trigger one-off presses or run repeated clicks on an interval.
- **🎲 Anti-Bot Jitter**: Optional random time variance ($\pm X$ seconds) to emulate human button pressing.
- **🔇 Auto-Detach (No Jitter / Hum)**: Automatically disables PWM pulses while idle so the servo doesn't hum, overheat, or buzz.
- **📐 Interactive Calibration**: Live angle sliders (Rest & Press angles) with test buttons to align the servo horn perfectly with your target button.
- **💾 Flash Memory Storage**: Preferences (angles, interval, Wi-Fi credentials) survive reboots and power outages.
- **🌐 Network Fallback**: Tries your home Wi-Fi first; falls back to an Access Point (`ESP32-Autoclicker`) if Wi-Fi is unavailable.
- **🔌 REST API**: Full JSON API for triggering presses via Python, curl, Home Assistant, or Rocky.

---

## 🛠 Hardware Required

1. **ESP32 Development Board** (NodeMCU ESP32, ESP32-WROOM, ESP32-S3, ESP32-C3, etc.)
2. **Micro Servo** (SG90 plastic gear or MG90S metal gear, 9g)
3. **Jumper Wires** (Female-to-Male or Female-to-Female)
4. **Power Supply**: 5V USB (Micro-USB or USB-C depending on your ESP32 board).
5. *(Optional)* **100µF – 470µF Electrolytic Capacitor** across 5V and GND near the servo to buffer peak motor draw.
6. *(Mounting)* Blu-Tack, double-sided tape, or a 3D-printed servo mount bracket. A small piece of pencil eraser or silicone stylus tip glued to the servo arm ensures soft, reliable button contact.

---

## 🔌 Wiring & Pinout

```text
       ESP32 Dev Board                     SG90 / MG90S Servo
    ┌──────────────────────┐               ┌─────────────────┐
    │                      │               │   [ Servo Horn ]│
    │         GPIO 18 ─────┼───────────────┤ Orange / Yellow │ (Signal / PWM)
    │                      │               │                 │
    │         VIN / 5V ────┼───────────────┤ Red             │ (Power 5V)
    │                      │               │                 │
    │             GND ─────┼───────────────┤ Brown / Black   │ (Ground)
    │                      │               └─────────────────┘
    └──────────────────────┘
```

> **Note on Power**: If the ESP32 brownouts or resets when the servo kicks in, plug a small 100µF capacitor between `5V` and `GND`, or power the servo with a dedicated 5V rail sharing common GND with the ESP32.

---

## 🚀 Flashing Firmware

### Method 1: PlatformIO (Recommended)
From the `esp32-servo-clicker` directory:
```bash
pio run -t upload
pio device monitor -b 115200
```

### Method 2: Arduino IDE
1. Open Arduino IDE.
2. Go to **Tools > Board** and select your board (e.g. `ESP32 Dev Module`).
3. Go to **Tools > Manage Libraries...** and search for:
   - `ESP32Servo` by Kevin Harrington
4. Open [esp32-servo-clicker.ino](file:///users/dad/rocky-voice/esp32-servo-clicker/esp32-servo-clicker.ino).
5. Click **Upload**.

---

## 📱 Wi-Fi Setup & Access

1. **First Boot (Hotspot Mode)**:
   - On initial power-up (or if Wi-Fi isn't configured), the ESP32 creates a Wi-Fi hotspot named:
     - **SSID**: `ESP32-Autoclicker`
     - **Password**: `12345678`
   - Connect from your phone or PC, then open:
     `http://192.168.4.1`
   - Scroll to **Wi-Fi Configuration**, enter your home Wi-Fi details, and click **Update Wi-Fi & Reboot**.
2. **Standard Mode (Home Wi-Fi)**:
   - Once connected to your local network, browse to:
     `http://autoclicker.local` (or the IP assigned by your router).

---

## ⚙️ Calibration Guide

1. Place the servo next to the physical button you want to press.
2. In the web dashboard:
   - Adjust **Rest Angle** slider (arm should be lifted ~2–5mm above button). Click **Test Rest Position**.
   - Adjust **Press Angle** slider (arm should push button firmly without stalling motor). Click **Test Press Position**.
   - Set **Hold Duration** (default 200ms is standard for a clean tactile press).
   - Set your desired **Interval** and optional **Jitter**.
3. Click **Save Settings** to write to ESP32 flash memory.

---

## 🌐 REST API Endpoints

You can trigger clicks or query state programmatically from anywhere on your local network:

| Method | Endpoint | Description | Sample Response |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/status` | Get current state & countdown | `{"auto":false,"clicks":12,"next_in":0,"state":"IDLE"}` |
| `POST` | `/api/click` | Trigger 1 immediate press | `{"status":"ok","action":"click"}` |
| `POST` | `/api/start` | Start continuous auto-click loop | `{"status":"ok","auto":true}` |
| `POST` | `/api/stop` | Stop continuous auto-click loop | `{"status":"ok","auto":false}` |
| `GET` | `/api/config` | Retrieve current configuration | `{restAngle: 20, pressAngle: 75, ...}` |
| `POST` | `/api/settings` | Save JSON configuration | `{"status":"saved"}` |
| `POST` | `/api/calibrate?angle=90`| Move servo directly to angle | `{"status":"ok","angle":90}` |

### Python CLI Example:
A test script is included in this folder:
```bash
# Check status
python3 test_clicker.py status

# Trigger single press
python3 test_clicker.py click

# Start continuous pressing
python3 test_clicker.py start

# Stop continuous pressing
python3 test_clicker.py stop
```

---

## 💡 Practical Mounting Tips

- **Soft Touch Pad**: Add a tiny rubber pad, pencil eraser tip, or silicone dot to the servo horn tip. This prevents slipping on smooth buttons and dampens mechanical impact.
- **Rigid Base**: Secure the servo firmly with Blu-Tack, heavy putty, or 3M Command strips so the reaction force doesn't push the servo backwards instead of pressing the target button.
