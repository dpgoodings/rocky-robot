"""2-DOF Marvin arms: shoulder pitch yokes and curved upper/forearm + mitt hands."""

from __future__ import annotations

import build123d as bd
from build123d import Align, Mode

from .helpers import make_ellipsoid, servo_9g_cavity, servo_horn_recess
from .params import GEO, TOL


def _build_left_shoulder_yoke() -> bd.Part:
    """Build Left (+X) shoulder pitch yoke connecting torso pitch servo to arm roll servo."""
    pitch_x = GEO.shoulder_x + 4.5   # 43.5 mm (0.5 mm clear of servo gear boss at X=43.0)
    roll_x = 58.0                    # Output shaft X of roll servo
    roll_y = 3.0                     # Output shaft plane Y
    sz = GEO.shoulder_z              # 88.0 mm

    # Roll servo cavity: shaft pointing +Y at (roll_x, roll_y, sz), body extending -Z and -Y
    roll_cav = servo_9g_cavity(tol=TOL.slip, wire_slot=True, pilot_holes=True, shaft_clearance_d=13.0)
    # First rotate +90 around Z so far end (-X) becomes -Y? Wait:
    # Local: shaft +Z, far end -X.
    # Rotate -90 around X -> local +Z becomes +Y, local +Y becomes +Z, local +X stays +X.
    # Rotate +90 around Z before X -> local +X becomes +Y, so after -90 X:
    # Let's compose explicitly: rotate Z +90 -> far end (-X) goes to -Y;
    # No, we want far end to go to -Z (downward) and shaft to go to +Y!
    # Step 1: rotate Y +90 -> local +Z goes +X, local -X goes +Z.
    # Step 2: rotate Z +90 -> +X goes +Y, +Z stays +Z! Wait: if local -X goes -Z, rotate Y -90?
    # Let's check: rotate_Y(-90) sends (0,0,1)->(-1,0,0) and (1,0,0)->(0,0,1), so far end (-1,0,0)->(0,0,-1) = -Z!
    # Then rotate_Z(-90) sends (-1,0,0)->(0,+1,0) = +Y!
    roll_cav = roll_cav.rotate(bd.Axis.Y, -90)
    roll_cav = roll_cav.rotate(bd.Axis.Z, -90)
    # Now shaft points +Y at local (0,0,0), far end extends toward -Z!
    # Flange underside is at Y = roll_y - 10.5 = -7.5 mm
    roll_cav = roll_cav.translate((roll_x, roll_y - 10.5, sz))

    with bd.BuildPart() as yoke:
        # Inner disc mating onto torso pitch servo horn (X = 41.5 .. 46.5)
        with bd.Locations((pitch_x + 2.5, 0.0, sz)):
            bd.Cylinder(
                radius=10.0,
                height=5.0,
                rotation=(0, 90, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )

        # Bridge & outer pod cradling the roll servo (X = 45 .. 64, Y = -23.5 .. -1.5, Z = 67 .. 98)
        with bd.BuildSketch(bd.Plane.YZ.offset(45.0)) as pod_sk:
            with bd.Locations((-12.5, sz - 5.5)):
                bd.RectangleRounded(22.0, 31.0, radius=3.5)
        bd.extrude(pod_sk.sketch, amount=19.0)

        # Bridge rib connecting inner disc (Y=0) to pod (Y=-12.5)
        with bd.Locations((pitch_x + 5.0, -5.0, sz)):
            bd.Box(8.0, 14.0, 16.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))

        # Subtract pitch horn recess on inner face (X = pitch_x)
        horn = servo_horn_recess(arm_angle_deg=0.0, depth=2.6, through_screw=True)
        # Local +Z points into part (+X), local +X points -Z (arm down)
        horn = horn.rotate(bd.Axis.Y, 90)
        horn = horn.translate((pitch_x - 0.05, 0.0, sz))
        bd.add(horn, mode=Mode.SUBTRACT)

        # Subtract roll servo pocket
        bd.add(roll_cav, mode=Mode.SUBTRACT)

    return yoke.part


def _build_left_arm() -> bd.Part:
    """Build Left (+X) articulated Marvin arm + elbow collar + rounded mitt hand."""
    roll_x = 58.0
    arm_y_in = 5.5   # 2.5 mm clear of roll servo boss
    sz = GEO.shoulder_z

    with bd.BuildPart() as arm:
        # Shoulder mounting hub (Y = 5.5 .. 13.5)
        with bd.Locations((roll_x, arm_y_in + 4.0, sz)):
            bd.Cylinder(
                radius=9.5,
                height=8.0,
                rotation=(90, 0, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )

        # Smooth organic Marvin arm loft from shoulder down to wrist
        stations = [
            (sz, roll_x, arm_y_in + 5.5, 7.5, 5.5),
            (sz - 28.0, roll_x + 3.0, arm_y_in + 9.0, 6.8, 5.5),   # Elbow
            (sz - 56.0, roll_x + 1.0, arm_y_in + 17.5, 6.0, 5.0),  # Wrist
        ]
        sketches = []
        for z, cx, cy, rx, ry in stations:
            with bd.BuildSketch(bd.Plane.XY.offset(z)) as sk:
                with bd.Locations((cx, cy)):
                    bd.Ellipse(rx, ry)
            sketches.append(sk.sketch)
        bd.loft(sketches)

        # Decorative Marvin elbow band
        with bd.Locations((roll_x + 3.0, arm_y_in + 9.0, sz - 28.0)):
            bd.Sphere(7.8)

        # Rounded Marvin mitt hand at wrist
        hx, hy, hz = roll_x + 1.0, arm_y_in + 19.5, sz - 62.0
        mitt = make_ellipsoid(7.5, 9.0, 10.0).translate((hx, hy, hz))
        bd.add(mitt)
        # Cute thumb bump on inner-front side
        thumb = make_ellipsoid(3.5, 4.2, 5.0).translate((hx - 5.2, hy + 4.5, hz + 1.0))
        bd.add(thumb)

        # Subtract roll servo horn recess on inner face (Y = arm_y_in)
        # Rotate so local +Z (into recess) points +Y, and local +X (arm) points -Z
        horn = servo_horn_recess(arm_angle_deg=0.0, depth=2.6, through_screw=True)
        horn = horn.rotate(bd.Axis.Y, 90)
        horn = horn.rotate(bd.Axis.Z, 90)
        horn = horn.translate((roll_x, arm_y_in - 0.05, sz))
        bd.add(horn, mode=Mode.SUBTRACT)

    return arm.part


def build_arm_parts() -> tuple[bd.Part, bd.Part, bd.Part, bd.Part]:
    """Return (yoke_left, yoke_right, arm_left, arm_right)."""
    yoke_l = _build_left_shoulder_yoke()
    yoke_l.label = "shoulder_yoke_left"
    yoke_l.color = bd.Color(0.28, 0.30, 0.34)

    yoke_r = bd.Part() + bd.mirror(yoke_l, bd.Plane.YZ)
    yoke_r.label = "shoulder_yoke_right"
    yoke_r.color = bd.Color(0.28, 0.30, 0.34)

    arm_l = _build_left_arm()
    arm_l.label = "arm_left"
    arm_l.color = bd.Color(0.84, 0.85, 0.87)

    arm_r = bd.Part() + bd.mirror(arm_l, bd.Plane.YZ)
    arm_r.label = "arm_right"
    arm_r.color = bd.Color(0.84, 0.85, 0.87)

    return yoke_l, yoke_r, arm_l, arm_r
