# Marvin — Bipedal Waddle Servo Robot Assistant (STL / 3MF CAD)

Parametric 3D-printable CAD project built with `build123d` for a compact (~255 mm tall) Marvin-inspired bipedal waddle desktop robot assistant with faux studio headphones.

## Output Files
Run the generator to rebuild all `.stl`, `.3mf`, and the interactive 3D viewer:

```bash
PYTHONPATH=src .venv/bin/python scripts/build_all.py
```

Generated under `output/`:
- `output/3mf/marvin_print_plate.3mf` — All 20 parts oriented flat on `Z=0` (split across 2–3 beds on a 220×220 mm printer)
- `output/3mf/marvin_assembled.3mf` — Full colored 3D assembly in assembled coordinates
- `output/3mf/<part>.3mf` — Individual print-ready `.3mf` files (millimeter units)
- `output/stl/<part>.stl` — Individual `.stl` files
- `output/viewer.html` — Self-contained interactive 3D assembly, exploded-view & live **Waddle Preview** inspector

## Bill of Materials (BOM)

### Servos (10 DOF total)
- **10× SG90 or MG90S 9g micro servos** (**MG90S metal-gear strongly recommended** for ankle roll, hip pitch, head tilt, and shoulder pitch):
  1. Head Pan (Yaw) — inside `torso_back`
  2. Head Tilt (Pitch) — inside `neck_yoke`
  3. Left Shoulder Pitch — inside `torso_back` (+X)
  4. Right Shoulder Pitch — inside `torso_back` (-X)
  5. Left Shoulder Roll (Raise) — inside `shoulder_yoke_left`
  6. Right Shoulder Roll (Raise) — inside `shoulder_yoke_right`
  7. Left Hip Pitch — inside `pelvis` (+X)
  8. Right Hip Pitch — inside `pelvis` (-X)
  9. Left Ankle Roll (Waddle Lean) — inside `leg_left`
  10. Right Ankle Roll (Waddle Lean) — inside `leg_right`

### Electronics & Transducers
- **ESP32 DevKit V1** (30-pin, ~52×28 mm) — mounted inside `backpack_lid` (USB facing bottom cutout)
- **PCA9685 16-Channel 12-bit PWM Servo Driver** — mounted inside `backpack_shell` (6 channels spare!)
- **5V 3A–4A DC power supply + 5.5×2.1 mm panel-mount DC barrel jack** (bottom of `backpack_shell`, or 2S LiPo + 5V UBEC inside backpack)
- **2.0" ST7789 IPS LCD module (320×240 SPI)** — mounted horizontally inside `head_front` behind `head_visor_bezel`
- **Compact Camera Module (8.5 mm lens bore)** — mounted in upper visor brow
- **INMP441 I2S MEMS Microphone** — mounted behind lower visor pinhole
- **28 mm round mini speaker (4Ω/8Ω, 1W–2W)** + small I2S amp — speaker seats in `torso_front` chest grille

### Fasteners
- **12× M3 × 10–12 mm socket-head screws** (4× waist-to-pelvis, 4× torso split, 4× head split)
- **4× M3 × 8 mm screws** (`backpack_shell` to `torso_back`)
- **4× M2.5 × 6 mm screws** (`backpack_lid` to `backpack_shell`) + M2.5 standoffs/screws for ESP32 & PCA9685
- **4× M2 × 5 mm screws** (LCD corner retention)
- **Servo mounting & horn screws** (included with SG90/MG90S packs)

## How Bipedal Waddle Mode Works
1. Center all 10 servos at 90° (`1500 µs`).
2. **Shift weight:** Tilt both **Ankle Roll** servos together (~12–16° left) to lift the right waddle boot off the desk.
3. **Swing leg:** Pitch the **Right Hip** forward (and counter-swing **Left Arm** forward / **Right Arm** back).
4. **Return & repeat:** Level ankles, tilt ankles right to lift the left boot, and swing the **Left Hip** forward!
