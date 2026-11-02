# MicroPython on ESP32-S3 WROOM CAM

This folder contains resources and instructions for using **MicroPython** on the ESP32-S3 WROOM board.

---

## 1. MicroPython Firmware Flashing

Because your board features **16 MB Octal Flash** and **8 MB Octal PSRAM**, standard MicroPython firmware requires the **ESP32_GENERIC_S3** build with SPIRAM / Octal support.

### Download Official Firmware:
Download the latest ESP32-S3 with Octal SPIRAM firmware `.bin` from the official repository:
- **Build:** `ESP32_GENERIC_S3-SPIRAM_OCT`
- [MicroPython ESP32-S3 Downloads](https://micropython.org/download/ESP32_GENERIC_S3/)

### Flashing with `esptool`:
From your terminal:
```bash
# Erase existing flash
/tmp/esp_env/bin/esptool.py --port /dev/cu.usbmodem5D160824921 erase_flash

# Write MicroPython firmware at offset 0
/tmp/esp_env/bin/esptool.py --port /dev/cu.usbmodem5D160824921 --baud 460800 write_flash 0 ESP32_GENERIC_S3-SPIRAM_OCT-*.bin
```

---

## 2. MicroPython SD Card & Hardware Access

Once MicroPython is running, the onboard MicroSD card is mounted via 1-bit SDCard or SPI bus:

```python
import os
import machine
from machine import SDCard

# Mount SD Card in 1-bit mode (Freenove ESP32-S3 pins: CLK=39, CMD=38, D0=40)
try:
    sd = SDCard(slot=2, width=1, sck=machine.Pin(39), cmd=machine.Pin(38), d0=machine.Pin(40))
    os.mount(sd, "/sd")
    print("SD Card mounted successfully!")
    print("Files on SD:", os.listdir("/sd"))
except Exception as e:
    print("SD Card mount error:", e)
```

---

## 3. PlatformIO vs. MicroPython for AI Robots

- **Recommended: PlatformIO (C++/Arduino Core)**
  - Direct DMA hardware acceleration for the OV2640 camera.
  - Full 25–30 FPS MJPEG streaming over Wi-Fi with negligible latency.
  - Native dual-core FreeRTOS multi-threading (Core 0 handles video streaming, Core 1 handles reflexes & sensor reads).
- **MicroPython Role:**
  - Excellent for fast prototyping of motor control scripts, high-level mission scripting, and reading I2C sensors (IMU, distance).
