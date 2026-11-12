"""Pan-tilt neck yoke connecting Torso Pan servo to Head Tilt pivot."""

from __future__ import annotations

import build123d as bd
from build123d import Align, Mode

from .helpers import servo_9g_cavity, servo_horn_recess
from .params import GEO, TOL


def build_neck_yoke() -> bd.Part:
    """Build the 2-axis pan-tilt neck yoke (Z = 108.8 .. 138.0 mm)."""
    z0 = GEO.neck_pan_z + 0.8   # 108.8 mm (0.8 mm clearance above torso top)
    tilt_z = GEO.neck_tilt_z    # 132.0 mm

    # Tilt servo cavity oriented with shaft pointing +X at (X = +16.5, Y = 0, Z = tilt_z)
    tilt_cav = servo_9g_cavity(tol=TOL.slip, wire_slot=True, pilot_holes=True, shaft_clearance_d=13.0)
    tilt_cav = tilt_cav.rotate(bd.Axis.Y, 90)  # local +Z -> +X, local +X -> -Z
    tilt_cav = tilt_cav.translate((6.0, 0.0, tilt_z))

    with bd.BuildPart() as yoke:
        # Bottom pan-horn flange disc (compact Ø27.6 mm for wide tilt clearance)
        with bd.Locations((0, 0, z0)):
            bd.Cylinder(
                radius=13.8,
                height=5.4,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )

        # Pan servo horn recess on underside of bottom disc (centered at X=0, Y=0, Z=z0)
        horn = servo_horn_recess(arm_angle_deg=90.0, depth=2.6, through_screw=True)
        horn = horn.translate((0, 0, z0 - 0.05))
        bd.add(horn, mode=Mode.SUBTRACT)

        # Central servo cradle block holding Tilt Servo (X = -10.5 .. +9.0, Y = -8.5 .. +8.5, Z = 113 .. 138)
        with bd.Locations((-0.8, 0.0, z0 + 4.5)):
            bd.Box(19.5, 17.5, 25.0, align=(Align.CENTER, Align.CENTER, Align.MIN))

        # Left (-X) upright pivot arm rising to tilt_z with dummy pivot pin
        with bd.Locations((-14.0, 0.0, z0 + 4.5)):
            bd.Box(5.0, 14.0, (tilt_z - z0) + 1.5, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with bd.Locations((-14.0, 0.0, tilt_z)):
            bd.Cylinder(
                radius=7.0,
                height=5.0,
                rotation=(0, 90, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )
        # Dummy pivot pin protruding outward (-X) from X=-16.5 to X=-21.5
        with bd.Locations((-16.5, 0.0, tilt_z)):
            bd.Cylinder(
                radius=2.4,
                height=10.0,
                rotation=(0, 90, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )

        # Bridge connecting (-14) upright arm to center cradle at Z = 113..117
        with bd.Locations((-11.5, 0.0, z0 + 4.0)):
            bd.Box(8.0, 14.0, 5.0, align=(Align.CENTER, Align.CENTER, Align.MIN))

        # Wire pass-through slot in rear of bottom flange (Y = -11.5)
        with bd.Locations((0, -11.5, z0 - 0.5)):
            bd.Box(11.0, 6.0, 8.0, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)

        # Subtract tilt servo pocket
        bd.add(tilt_cav, mode=Mode.SUBTRACT)

    part = yoke.part
    part.label = "neck_yoke"
    part.color = bd.Color(0.28, 0.30, 0.34)
    return part
