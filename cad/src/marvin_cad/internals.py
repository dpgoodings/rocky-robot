"""3D visualization models of internal servos, PCBs, battery, speaker, and camera."""

from __future__ import annotations

from dataclasses import dataclass

import build123d as bd
from build123d import Align

from .params import GEO, SERVO


@dataclass
class InternalItem:
    name: str
    part: bd.Part
    color_hex: str
    rig_group: str  # "world", "upper", "neck", "head", "arm_l", "arm_r"
    description: str


def _servo_9g_model() -> bd.Part:
    """Create a 1:1 visual model of a 9g MG90S/SG90 servo in local servo coords."""
    cx = -SERVO.shaft_offset_from_center
    with bd.BuildPart() as srv:
        # Rectangular body (Z = -16.0 .. +6.5)
        with bd.Locations((cx, 0, -SERVO.flange_z_from_bottom + SERVO.body_h / 2)):
            bd.Box(SERVO.body_len, SERVO.body_wid, SERVO.body_h, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        # Mounting flange (Z = 0 .. +2.5)
        with bd.Locations((cx, 0, SERVO.flange_thick / 2)):
            bd.Box(SERVO.flange_len, SERVO.body_wid, SERVO.flange_thick, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        # Gear housing boss (Z = +6.5 .. +10.5)
        with bd.Locations((0, 0, SERVO.body_h - SERVO.flange_z_from_bottom)):
            bd.Cylinder(radius=SERVO.boss_d / 2, height=SERVO.boss_h, align=(Align.CENTER, Align.CENTER, Align.MIN))
        # Output spline (Z = +10.5 .. +14.0)
        with bd.Locations((0, 0, SERVO.body_h - SERVO.flange_z_from_bottom + SERVO.boss_h)):
            bd.Cylinder(radius=SERVO.spline_d / 2, height=3.5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return srv.part


def build_internal_items() -> list[InternalItem]:
    """Build all 10 servos and 8 electronic/transducer visualization models."""
    base_srv = _servo_9g_model()
    items: list[InternalItem] = []

    # 1. 10x 9g Servos placed at exact pocket coordinates
    servo_placements = [
        (
            "srv_head_pan",
            base_srv.rotate(bd.Axis.Z, 90).translate((0, 0, GEO.neck_pan_z - 10.5)),
            "upper",
            "Head Pan 9g servo (CyberBrick PG001)",
        ),
        (
            "srv_head_tilt",
            base_srv.rotate(bd.Axis.Y, 90).translate((6.0, 0, GEO.neck_tilt_z)),
            "neck",
            "Head Tilt 9g servo (MG90S)",
        ),
        (
            "srv_shoulder_pitch_l",
            base_srv.rotate(bd.Axis.Y, 90).translate((GEO.shoulder_x - 6.5, 0, GEO.shoulder_z)),
            "upper",
            "Left Shoulder Pitch 9g servo (MG90S)",
        ),
        (
            "srv_shoulder_pitch_r",
            base_srv.rotate(bd.Axis.Y, -90).translate((-(GEO.shoulder_x - 6.5), 0, GEO.shoulder_z)),
            "upper",
            "Right Shoulder Pitch 9g servo (MG90S)",
        ),
        (
            "srv_shoulder_roll_l",
            base_srv.rotate(bd.Axis.Y, -90).rotate(bd.Axis.Z, -90).translate((58.0, 3.0 - 10.5, GEO.shoulder_z)),
            "upper",
            "Left Shoulder Roll 9g servo (MG90S)",
        ),
        (
            "srv_shoulder_roll_r",
            bd.Part() + bd.mirror(
                base_srv.rotate(bd.Axis.Y, -90).rotate(bd.Axis.Z, -90).translate((58.0, 3.0 - 10.5, GEO.shoulder_z)),
                bd.Plane.YZ,
            ),
            "upper",
            "Right Shoulder Roll 9g servo (MG90S)",
        ),
        (
            "srv_hip_pitch_l",
            base_srv.rotate(bd.Axis.Y, 90).rotate(bd.Axis.X, -90).translate((19.0, 0.0, GEO.hip_z)),
            "upper",
            "Left Hip Pitch 9g servo (MG90S)",
        ),
        (
            "srv_hip_pitch_r",
            base_srv.rotate(bd.Axis.Y, -90).rotate(bd.Axis.X, 90).translate((-19.0, 0.0, GEO.hip_z)),
            "upper",
            "Right Hip Pitch 9g servo (MG90S)",
        ),
        (
            "srv_ankle_roll_l",
            base_srv.rotate(bd.Axis.Z, 90).rotate(bd.Axis.X, -90).translate((GEO.stance_x, 0.0, GEO.ankle_z)),
            "upper",
            "Left Ankle Roll 9g servo (MG90S)",
        ),
        (
            "srv_ankle_roll_r",
            bd.Part() + bd.mirror(
                base_srv.rotate(bd.Axis.Z, 90).rotate(bd.Axis.X, -90).translate((GEO.stance_x, 0.0, GEO.ankle_z)),
                bd.Plane.YZ,
            ),
            "upper",
            "Right Ankle Roll 9g servo (MG90S)",
        ),
    ]

    for name, part, rig, desc in servo_placements:
        part.label = name
        part.color = bd.Color(0.16, 0.45, 0.85)
        items.append(InternalItem(name, bd.Part() + part, "#2563eb", rig, desc))

    # 2. Waveshare 2.0" ST7789 IPS LCD (Head)
    lcd_z = GEO.head_center_z - 3.5
    with bd.BuildPart() as lcd_bp:
        with bd.Locations((0, GEO.face_plane_y - 2.6, lcd_z)):
            bd.Box(58.0, 1.6, 35.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        with bd.Locations((0, GEO.face_plane_y - 0.8, lcd_z)):
            bd.Box(40.8, 2.0, 30.6, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    lcd_p = lcd_bp.part
    lcd_p.label = "elec_st7789_lcd"
    lcd_p.color = bd.Color(0.06, 0.55, 0.72)
    items.append(InternalItem("elec_st7789_lcd", lcd_p, "#0891b2", "head", "Waveshare 2.0\" ST7789 IPS LCD"))

    # 3. Camera Sensor + Short Orange FPC Ribbon folding forward from ESP32-S3 CAM
    cam_z = GEO.head_center_z + 19.5
    with bd.BuildPart() as cam_bp:
        # 8.5x8.5 mm sensor head + Ø8.0 mm lens barrel protruding into forehead bore
        with bd.Locations((0, GEO.face_plane_y - 3.3, cam_z)):
            bd.Box(10.0, 2.2, 10.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        with bd.Locations((0, GEO.face_plane_y + 1.0, cam_z)):
            bd.Cylinder(radius=4.0, height=7.5, rotation=(90, 0, 0), align=(Align.CENTER, Align.CENTER, Align.CENTER))
        # Short FPC ribbon bridging from ESP32-S3 FPC connector (Y=15.5, Z=211) up to sensor
        with bd.Locations((0, 18.8, cam_z - 6.0)):
            bd.Box(8.0, 5.5, 11.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    cam_p = cam_bp.part
    cam_p.label = "elec_camera_lens"
    cam_p.color = bd.Color(0.12, 0.14, 0.18)
    items.append(InternalItem("elec_camera_lens", cam_p, "#1f2937", "head", "OV2640/OV3660 Camera & Short FPC Ribbon"))

    # 4. Compact Dual-USB-C ESP32-S3-WROOM CAM (N16R8, 57.5x28 mm) mounted vertically at Y=14.5
    s3_z = GEO.head_center_z + 2.0  # spans Z = 178.25 .. 235.75 (207 +/- 28.75)
    with bd.BuildPart() as s3_bp:
        # Matte-black 57.5 x 28.0 mm PCB facing +Y
        with bd.Locations((0, 14.5, s3_z)):
            bd.Box(28.0, 1.6, 57.5, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        # Silver WROOM-1 shield near top (antenna end)
        with bd.Locations((0, 16.4, s3_z + 13.0)):
            bd.Box(18.0, 2.8, 22.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        # Dual USB-C ports at bottom edge
        for ux in (-7.2, 7.2):
            with bd.Locations((ux, 16.5, s3_z - 25.0)):
                bd.Box(9.0, 3.2, 7.5, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    s3_p = s3_bp.part
    s3_p.label = "elec_esp32_s3_cam"
    s3_p.color = bd.Color(0.09, 0.45, 0.24)
    items.append(InternalItem("elec_esp32_s3_cam", s3_p, "#15803d", "head", "ESP32-S3-WROOM CAM (N16R8, 57.5×28 mm)"))

    # 5. INMP441 I2S Mic (Head)
    mic_z = GEO.head_center_z - 25.5
    with bd.BuildPart() as mic_bp:
        with bd.Locations((0, GEO.face_plane_y - 3.3, mic_z)):
            bd.Box(13.8, 1.8, 13.8, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    mic_p = mic_bp.part
    mic_p.label = "elec_inmp441_mic"
    mic_p.color = bd.Color(0.45, 0.20, 0.65)
    items.append(InternalItem("elec_inmp441_mic", mic_p, "#7e22ce", "head", "INMP441 I2S MEMS Microphone"))

    # 6. 28 mm Chest Speaker (Torso)
    with bd.BuildPart() as spk_bp:
        with bd.Locations((0, 25.8, GEO.speaker_z)):
            bd.Cylinder(radius=14.0, height=5.0, rotation=(90, 0, 0), align=(Align.CENTER, Align.CENTER, Align.CENTER))
    spk_p = spk_bp.part
    spk_p.label = "elec_28mm_speaker"
    spk_p.color = bd.Color(0.28, 0.30, 0.35)
    items.append(InternalItem("elec_28mm_speaker", spk_p, "#4b5563", "upper", "28 mm 8-Ohm Mini Speaker"))

    # 7. PCA9685 16-Ch Servo Driver (Backpack Front Wall, seated on Y=-40.5 standoffs)
    with bd.BuildPart() as pca_bp:
        with bd.Locations((0, -41.5, GEO.torso_z0 + 55.0)):
            bd.Box(62.5, 1.8, 25.5, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        with bd.Locations((-22.0, -45.0, GEO.torso_z0 + 55.0)):
            bd.Box(10.0, 5.0, 10.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    pca_p = pca_bp.part
    pca_p.label = "elec_pca9685"
    pca_p.color = bd.Color(0.10, 0.42, 0.78)
    items.append(InternalItem("elec_pca9685", pca_p, "#1d4ed8", "upper", "PCA9685 16-Channel PWM Servo Driver"))

    # 8. CyberBrick 2S 14500 7.4V Li-Ion Pack + 5V UBEC + 2S USB-C Charger + Switch (Backpack)
    with bd.BuildPart() as batt_bp:
        for dx in (-7.2, 7.2):
            with bd.Locations((-6.0 + dx, -52.5, GEO.torso_z0 + 38.0)):
                bd.Cylinder(radius=7.1, height=50.5, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    batt_p = batt_bp.part
    batt_p.label = "elec_cyberbrick_battery"
    batt_p.color = bd.Color(0.12, 0.62, 0.45)
    items.append(InternalItem("elec_cyberbrick_battery", batt_p, "#059669", "upper", "CyberBrick 2S 14500 7.4V 800mAh Li-Ion"))

    with bd.BuildPart() as pwr_bp:
        # 5V 5A UBEC (strapped in front of UBEC bridge)
        with bd.Locations((18.0, -52.0, GEO.torso_z0 + 38.0)):
            bd.Box(11.0, 5.5, 26.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        # 2S USB-C Boost Charger Board (between Y=-48+/-2.5 cradle walls)
        with bd.Locations((18.0, -48.0, GEO.torso_z0 + 18.0)):
            bd.Box(13.0, 3.6, 22.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        # CyberBrick Power Switch seated over bottom switch cutout
        with bd.Locations((-14.0, -50.0, GEO.torso_z0 + 10.0)):
            bd.Box(13.0, 8.0, 8.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    pwr_p = pwr_bp.part
    pwr_p.label = "elec_power_usbc_ubec"
    pwr_p.color = bd.Color(0.85, 0.46, 0.08)
    items.append(InternalItem("elec_power_usbc_ubec", pwr_p, "#d97706", "upper", "5V 5A UBEC + 2S USB-C Charger + CyberBrick Switch"))

    return items
