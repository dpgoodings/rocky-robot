"""Parametric dimensions, tolerances, and hardware specs for the Marvin desk robot."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Tolerances:
    tight: float = 0.15      # Snug/press fit (pins, lens holes)
    slip: float = 0.25       # Sliding / mating shell fit
    loose: float = 0.40      # Clearance holes, cable tunnels
    wall: float = 2.4        # Standard shell wall thickness


@dataclass(frozen=True)
class Servo9gSpec:
    """Standard SG90 / MG90S 9g micro servo dimensions (mm)."""
    body_len: float = 23.0          # X/long dimension of rectangular body
    body_wid: float = 12.2          # Y/narrow dimension of body
    body_h: float = 22.5            # Z height from bottom to top of gearcase shoulder
    flange_len: float = 32.5        # Total width across mounting ears
    flange_z_from_bottom: float = 16.0  # Bottom of flange above servo bottom
    flange_thick: float = 2.5
    hole_spacing: float = 28.0      # Mounting screw center-to-center
    hole_pilot_d: float = 1.8       # M2 self-tapping pilot
    shaft_offset_from_center: float = 5.6  # Offset of output shaft toward one end
    boss_d: float = 12.0            # Circular gear housing boss diameter
    boss_h: float = 4.0             # Height above body_h
    spline_d: float = 4.8
    horn_hub_d: float = 7.4         # Standard single-arm horn hub diameter
    horn_arm_len: float = 18.0      # From shaft center to tip of horn arm
    horn_arm_w_base: float = 5.4
    horn_arm_w_tip: float = 3.6
    horn_thick: float = 2.0
    horn_screw_d: float = 2.2       # Center M2 screw clearance


@dataclass(frozen=True)
class ElectronicsSpec:
    """PCB and transducer dimensions inside base, torso, and head."""
    # ESP32-S3-WROOM CAM (N16R8 44-pin dual-USB-C compact layout) mounted vertically in head_front
    s3_cam_len: float = 58.0        # Z height with clearance
    s3_cam_wid: float = 28.5        # X width with clearance
    s3_cam_thick: float = 2.0       # PCB slot thickness

    # Optional legacy ESP32 DevKit V1 (30-pin) for plinth base_lid
    esp32_len: float = 52.5
    esp32_wid: float = 28.5
    esp32_hole_dx: float = 47.0
    esp32_hole_dy: float = 23.0

    # CyberBrick 2S 14500 7.4V 800mAh Li-Ion pack + XH2.54 power switch + 2S USB-C charger
    batt_14500_w: float = 30.5
    batt_14500_d: float = 16.0
    batt_14500_h: float = 53.0
    cb_switch_w: float = 14.0
    cb_switch_h: float = 9.0
    usbc_chg_w: float = 10.2
    usbc_chg_h: float = 4.6

    # PCA9685 16-Channel 12-bit PWM Servo Driver
    pca_len: float = 62.5
    pca_wid: float = 25.5
    pca_hole_dx: float = 56.0
    pca_hole_dy: float = 19.0

    # Rear ports on base
    usb_cutout_w: float = 14.0
    usb_cutout_h: float = 9.0
    barrel_jack_d: float = 8.2      # 5.5x2.1 mm panel-mount DC jack

    # 28 mm round mini speaker (torso chest)
    speaker_d: float = 28.4
    speaker_depth: float = 5.5

    # 2.0" ST7789 IPS LCD module (320x240 landscape)
    lcd_pcb_w: float = 60.0         # Pocket width (fits 58-59 mm PCB)
    lcd_pcb_h: float = 36.5         # Pocket height (fits 34.4-35.5 mm PCB)
    lcd_pcb_t: float = 3.2          # PCB + display thickness
    lcd_active_w: float = 41.2      # Visible opening width
    lcd_active_h: float = 31.0      # Visible opening height

    # Camera module (OV2640 / ESP32-CAM / compact USB cam lens)
    cam_lens_d: float = 8.6         # 8.5 mm lens bore + tiny clearance
    cam_pcb_w: float = 22.0         # Internal retainer pocket
    cam_pcb_h: float = 22.0

    # INMP441 I2S MEMS Microphone
    mic_port_d: float = 2.2
    mic_pcb_w: float = 14.5
    mic_pcb_h: float = 14.5


@dataclass(frozen=True)
class RobotGeometry:
    """Overall assembly coordinates (Z=0 at desk, +Y is front, +X is robot left)."""
    # Base (Z = 0 .. 34)
    base_bottom_d: float = 118.0
    base_top_d: float = 104.0
    base_h: float = 34.0
    base_lid_h: float = 3.0
    waist_bolt_d: float = 44.0
    wire_tunnel_d: float = 22.0

    # Bipedal Lower Body (Z = 0 .. 78)
    hip_z: float = 60.0             # Hip pitch axis height
    hip_x: float = 28.5             # Hip pitch horn X plane
    ankle_z: float = 19.0           # Ankle roll axis height
    stance_x: float = 37.0          # Center X of each leg/foot

    # Torso (Z = 78 .. 152 in bipedal mode)
    torso_z0: float = 78.0
    torso_h: float = 74.0
    torso_w: float = 78.0           # X width at shoulders
    torso_d: float = 64.0           # Y depth
    shoulder_z: float = 132.0       # Height of shoulder pitch axis
    shoulder_x: float = 39.0        # X plane where shoulder yoke clears torso
    speaker_z: float = 106.0        # Chest speaker center height

    # Arms
    arm_upper_len: float = 36.0
    arm_forearm_len: float = 38.0
    arm_width: float = 16.0

    # Neck & Head (Neck shaft at Z=152, Head tilt axis at Z=179, Head center Z=205)
    neck_pan_z: float = 152.0
    neck_tilt_z: float = 179.0
    head_center_z: float = 205.0
    head_w: float = 96.0            # X diameter
    head_d: float = 90.0            # Y diameter
    head_h: float = 88.0            # Z diameter
    face_plane_y: float = 27.0      # Front flat/recessed visor plane Y


TOL = Tolerances()
SERVO = Servo9gSpec()
ELEC = ElectronicsSpec()
GEO = RobotGeometry()
