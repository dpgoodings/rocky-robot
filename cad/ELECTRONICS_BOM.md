# Marvin Bipedal Waddle Robot — Custom Electronics BOM
**Tailored for:** *Bambu Lab CyberBrick Ultimate Kit (ZK003)* + *Freenove ESP32-S3-WROOM CAM (N16R8)*

---

## Part A: What You Already Own (and How to Use It)

| Owned Item | Qty Used | Role in Marvin | Important Notes |
|---|---|---|---|
| **Freenove ESP32-S3-WROOM CAM (N16R8)** | **1×** | **Main Brain + Camera + Wi-Fi/BLE** (replaces *both* the ESP32 DevKit V1 and separate ESP32-CAM!) | 16MB Flash + 8MB Octal PSRAM + onboard OV3660/OV2640 camera (`~67 × 29 mm` PCB). Best mounted inside the **head** so its short camera ribbon plugs straight into the forehead lens bore (`Ø8.6 mm`), leaving only `5V, GND, SDA, SCL` + audio wires going down the neck. |
| **CyberBrick 180° 9g Servo (`PG001`)** | **1×** | **Head Pan Servo** (light-load joint) | Standard 9g envelope (`~23 × 12.2 mm`) with clutch protection. *(Note: the 2× CyberBrick `PG002` 360° speed servos are continuous-rotation and cannot hold joint angles, so save those for wheeled builds.)* |
| **CyberBrick 14500 7.4V 800mAh Li-Ion + Charger + Power Switch (`XH2.54`)** | **1–2×** | **Untethered Robot Battery + Power Switch** | Steps down cleanly to `5.0V` via a small 5V 5A UBEC/buck converter for cordless waddling. |
| **CyberBrick M2.5×6 BHCS Screws** | **~12×** | **PCA9685, Backpack Lid & Misc Mounts** | 100× included in kit. |
| **CyberBrick WS2812 RGB LEDs + Hub** *(Optional)* | **2×** | **Headphone Ear-Cup Accent Glow** | Can be tucked inside `headphone_cup_left` / `right` or `torso`. |
| **CyberBrick Core + Transmitter Shield + Joysticks** *(Optional)* | **1×** | **Physical Handheld Waddle Remote** | Optional ESP-NOW controller to drive Marvin's walking/expressions manually. |

---

## Part B: Remaining Parts to Buy (~$45–$75 Total)

| Qty | Component | CAD Fit / Role | Where to Buy | Est. Cost |
|---|---|---|---|---|
| **9–11×** *(includes 1–2 spares)* | **MG90S Metal-Gear 9g Micro Servo (180°)** | Fits `23.0 × 12.2 × 22.5 mm` pockets (`28.0 mm` hole spacing). Pairs with your 1× CyberBrick `PG001` to complete all **10 DOF** (Ankles, Hips, Shoulders, Head Tilt, Head Pan). | **Amazon** (`MG90S metal gear 10 pack` — Miuzei / Dorhea), **Pololu**, **AliExpress** | \$22–\$32 |
| **1×** | **PCA9685 16-Channel 12-bit PWM Servo Driver** | Fits `56.0 × 19.0 mm` (`62.5 × 25.5 mm`) standoffs in `backpack_shell`. Controlled over I2C (`SDA`/`SCL`) from the ESP32-S3; drives all 10 servos. | **Adafruit** (`Product ID 815`), **Amazon** (HiLetgo / SunFounder), **DigiKey** | \$4–\$15 |
| **1×** | **Waveshare 2.0" IPS LCD Display Module (240×320, ST7789, SPI)** | Exact fit! `58.0 × 35.0 mm` PCB (`60.0 × 36.5 mm` pocket) & `40.8 × 30.6 mm` active area behind `head_visor_bezel`. Choose non-touch SPI version. | **Waveshare**, **Amazon** (Waveshare Official Store), **PiShop.us**, **The Pi Hut** | \$12–\$16 |
| **1×** | **INMP441 I2S MEMS Microphone (`GY-INMP441`)** | Fits behind the `Ø2.2 mm` lower-visor acoustic pinhole in `head_front`. *(Adafruit alt: `SPH0645LM4H`, **Product ID 3421**).* | **Amazon** (HiLetgo / generic pack), **AliExpress** | \$5–\$8 |
| **1×** | **MAX98357A I2S 3W Class-D Mono Amp** | Tiny breakout tucked inside `torso` or `backpack_shell`; drives chest speaker from ESP32-S3 I2S audio pins. | **Adafruit** (`Product ID 3006`), **Amazon**, **DigiKey** | \$3–\$6 |
| **1×** | **28 mm Round Mini Speaker (4Ω or 8Ω, 1W–2W, ~5 mm thick)** | Fits `Ø28.4 mm` cradle behind `torso_front` chest grille. | **Amazon** (`28mm 8 ohm 2W mini speaker`), **Adafruit**, **DigiKey** | \$3–\$6 |
| **1×** | **5V 5A UBEC / Buck Converter** *(for CyberBrick 7.4V Li-Ion)* + **2S 8.4V USB-C Boost Charger Board** + **XH2.54 2-pin Pigtails** | Steps `7.4V` battery down to steady `5.0V` servo/logic rail, while the **2S `8.4V` USB-C boost charger** recharges the CyberBrick `14500` pack directly through the bottom USB-C port in `backpack_shell`. *(Do not use a `1S TP4056` `4.2V` board.)* | **Amazon** (`2S 8.4V USB-C boost charger`, `5V 5A UBEC`), **Pololu** (`D24V50F5` 5V 5A) | \$14–\$22 |
| **1 pack each** | **3-Pin Servo Extensions (150–300 mm, M-F)** + **Dupont Jumpers (M-F / F-F)** + **1000 µF 10V+ Cap** | Reaches from ankles/shoulders/head to `PCA9685` in backpack; `1000 µF` cap across `PCA9685 V+` absorbs servo spikes. | **Amazon**, **Pololu**, **Adafruit** | \$10–\$15 |
| **1 pack** | **M3 × 10–12 mm & M2 × 5 mm Screws** *(if not on hand)* | M3s join pelvis/waist, torso halves, head halves, and backpack (`M2.5×6` are already in your CyberBrick kit!). | **Amazon** (M2/M2.5/M3 assortment box) | \$8–\$10 |

---

## Important Note on GPIO Pins (Freenove ESP32-S3 N16R8)
Because your Freenove ESP32-S3-WROOM CAM uses internal/dedicated pins for the **camera**, **MicroSD**, and **Octal PSRAM (GPIO 35–37)**:
- Avoid **GPIO 35, 36, 37** (Octal PSRAM on N16R8), **GPIO 19/20** (native USB-OTG if used), and the camera/SD pins printed on your board's Freenove pinout chart.
- Keep **I2C** (`2 pins` -> PCA9685), **SPI** (`5 pins` -> ST7789 LCD), **I2S Mic** (`3 pins` -> INMP441), and **I2S Amp** (`3 pins` -> MAX98357A, can even share `BCLK`/`WS` with INMP441 or use a second I2S port!).
