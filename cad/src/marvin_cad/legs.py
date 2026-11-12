"""Bipedal waddle lower body: pelvis, 2x hip/ankle legs, and double-shear waddle boots."""

from __future__ import annotations

import math

import build123d as bd
from build123d import Align, Mode

from .helpers import servo_9g_cavity, servo_horn_recess
from .params import GEO, TOL


def build_pelvis() -> bd.Part:
    """Build Marvin pelvis (Z = 47 .. 78 mm) housing 2x Hip Pitch 9g servos."""
    z0 = 47.0
    z_top = GEO.torso_z0  # 78.0 mm

    # Hip servo cavities: shafts pointing +/-X at (X=+/-28.5, Y=0, Z=GEO.hip_z=60),
    # far ends pointing -Y (into rear of pelvis), flanges at X=+/-19.0
    hip_cavs = []
    for side in (1, -1):
        cav = servo_9g_cavity(tol=TOL.slip, wire_slot=True, pilot_holes=True, shaft_clearance_d=13.2)
        cav = cav.rotate(bd.Axis.Y, 90 if side == 1 else -90)
        cav = cav.rotate(bd.Axis.X, -90 if side == 1 else 90)
        cav = cav.translate((side * 19.0, 0.0, GEO.hip_z))
        hip_cavs.append(cav)

    # Outer-side insertion pockets so servos drop straight in from +/-X
    side_pockets = []
    for side in (1, -1):
        pkt = bd.Box(8.0, 33.4, 12.8, align=(Align.CENTER, Align.CENTER, Align.CENTER)).translate(
            (side * 22.8, -5.6, GEO.hip_z)
        )
        side_pockets.append(pkt)

    with bd.BuildPart() as pel:
        # Rounded hip housing (Z = 47 .. 74)
        with bd.BuildSketch(bd.Plane.XY.offset(z0)) as sk:
            bd.RectangleRounded(48.0, 42.0, radius=9.0)
        bd.extrude(sk.sketch, amount=27.0)

        # Waist collar matching torso bottom (Z = 73 .. 78)
        with bd.BuildSketch(bd.Plane.XY.offset(73.0)) as w_sk:
            bd.Ellipse(26.2, 24.8)
        bd.extrude(w_sk.sketch, amount=z_top - 73.0)

        # Central wire tunnel (Z = 66 .. 78.5) + internal plenum
        with bd.Locations((0, 0, 66.5)):
            bd.Cylinder(
                radius=GEO.wire_tunnel_d / 2,
                height=12.5,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
                mode=Mode.SUBTRACT,
            )
        with bd.Locations((0, -2.0, 67.0)):
            bd.Box(24.0, 28.0, 7.5, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)

        # 4x M3 waist bolt clearance holes + underside access counterbores
        waist_r = GEO.waist_bolt_d / 2
        for deg in (45, 135, 225, 315):
            rad = math.radians(deg)
            wx, wy = waist_r * math.cos(rad), waist_r * math.sin(rad)
            with bd.Locations((wx, wy, 72.0)):
                bd.Cylinder(
                    radius=1.7,
                    height=7.0,
                    align=(Align.CENTER, Align.CENTER, Align.MIN),
                    mode=Mode.SUBTRACT,
                )
            with bd.Locations((wx, wy, 63.0)):
                bd.Cylinder(
                    radius=3.3,
                    height=10.5,
                    align=(Align.CENTER, Align.CENTER, Align.MIN),
                    mode=Mode.SUBTRACT,
                )

        # Rear leg wire exit notches
        for side in (-1, 1):
            with bd.Locations((side * 20.0, -14.0, 67.5)):
                bd.Box(12.0, 7.0, 6.0, align=(Align.CENTER, Align.CENTER, Align.CENTER), mode=Mode.SUBTRACT)

        for cav in hip_cavs:
            bd.add(cav, mode=Mode.SUBTRACT)
        for pkt in side_pockets:
            bd.add(pkt, mode=Mode.SUBTRACT)

    part = pel.part
    part.label = "pelvis"
    part.color = bd.Color(0.28, 0.30, 0.34)
    return part


def _build_left_leg() -> bd.Part:
    """Build Left (+X) leg housing Ankle Roll servo and Hip Pitch horn recess."""
    lx = GEO.stance_x  # 37.0 mm
    hip_x_in = 30.0    # 1.5 mm clear of pelvis boss
    hz = GEO.hip_z     # 60.0 mm
    az = GEO.ankle_z   # 19.0 mm

    # Ankle servo cavity: shaft +Y at (lx, +10.5, az), far end +Z (upward), flange at Y=0
    ankle_cav = servo_9g_cavity(tol=TOL.slip, wire_slot=True, pilot_holes=True, shaft_clearance_d=13.2)
    ankle_cav = ankle_cav.rotate(bd.Axis.Z, 90)
    ankle_cav = ankle_cav.rotate(bd.Axis.X, -90)
    ankle_cav = ankle_cav.translate((lx, 0.0, az))

    # Front insertion pocket so ankle servo drops straight in from +Y
    front_pkt = bd.Box(12.8, 12.0, 33.4, align=(Align.CENTER, Align.CENTER, Align.CENTER)).translate(
        (lx, 6.0, az + 5.6)
    )

    # Hip horn recess on inner face (X = hip_x_in) with arm pointing down (-Z)
    hip_horn = servo_horn_recess(arm_angle_deg=0.0, depth=2.6, through_screw=True)
    hip_horn = hip_horn.rotate(bd.Axis.Y, 90).translate((hip_x_in - 0.05, 0.0, hz))

    with bd.BuildPart() as leg:
        # Upper hip hub & thigh column (Z = 38 .. 68)
        with bd.BuildSketch(bd.Plane.XY.offset(38.0)) as sk_up:
            with bd.Locations((lx, -2.0)):
                bd.RectangleRounded(14.0, 24.0, radius=4.5)
        bd.extrude(sk_up.sketch, amount=30.0)

        # Rounded hip boss cap around X=30..44, Y=0, Z=60
        with bd.Locations((lx, 0.0, hz)):
            bd.Cylinder(
                radius=9.5,
                height=14.0,
                rotation=(0, 90, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )

        # Decorative Marvin knee band at Z = 39
        with bd.Locations((lx, -2.0, 39.0)):
            bd.Cylinder(
                radius=13.0,
                height=5.0,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )

        # Lower shin housing Ankle Roll servo + solid rear pivot wall (Z = 10.5 .. 38)
        with bd.BuildSketch(bd.Plane.XY.offset(10.5)) as sk_lo:
            with bd.Locations((lx, -4.0)):
                bd.RectangleRounded(15.0, 31.0, radius=4.0)
        bd.extrude(sk_lo.sketch, amount=28.0)

        # Rear wire channel running up back of thigh (Y = -12.5, Z = 36 .. 62)
        with bd.Locations((lx, -12.5, 49.0)):
            bd.Box(5.5, 4.5, 28.0, align=(Align.CENTER, Align.CENTER, Align.CENTER), mode=Mode.SUBTRACT)

        # Rear ankle dummy pivot socket (Ø5.1 mm) at Y = -19.5 .. -16.2
        with bd.Locations((lx, -18.0, az)):
            bd.Cylinder(
                radius=2.55,
                height=5.5,
                rotation=(90, 0, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )

        bd.add(ankle_cav, mode=Mode.SUBTRACT)
        bd.add(front_pkt, mode=Mode.SUBTRACT)
        bd.add(hip_horn, mode=Mode.SUBTRACT)

    return leg.part


def _build_left_foot() -> bd.Part:
    """Build Left (+X) rounded Marvin waddle boot with double-shear ankle fork."""
    lx = GEO.stance_x  # 37.0 mm
    az = GEO.ankle_z   # 19.0 mm

    # Front ankle horn recess facing -Y at Y = +11.5 (recessed into +Y), arm down (-Z)
    ankle_horn = servo_horn_recess(arm_angle_deg=0.0, depth=2.6, through_screw=True)
    ankle_horn = ankle_horn.rotate(bd.Axis.Y, 90).rotate(bd.Axis.Z, 90)
    ankle_horn = ankle_horn.translate((lx, 11.45, az))

    with bd.BuildPart() as foot:
        # Wide flat-bottomed boot sole (Z = 0 .. 7.0)
        with bd.BuildSketch(bd.Plane.XY.offset(0.0)) as sole_sk:
            with bd.Locations((lx, 5.0)):
                bd.RectangleRounded(38.0, 64.0, radius=14.0)
        bd.extrude(sole_sk.sketch, amount=7.0)

        # Chamfered upper boot rim (Z = 7.0 .. 11.0)
        with bd.BuildSketch(bd.Plane.XY.offset(7.0)) as rim_sk:
            with bd.Locations((lx, 5.0)):
                bd.RectangleRounded(34.0, 60.0, radius=12.0)
        bd.extrude(rim_sk.sketch, amount=4.0)

        # Hollow leg swing bay inside boot (Z = 7.0 .. 12.0, leaves 1.5 mm clear under leg at Z=10.5)
        with bd.Locations((lx, -3.5, 9.5)):
            bd.Box(21.0, 34.0, 6.0, align=(Align.CENTER, Align.CENTER, Align.CENTER), mode=Mode.SUBTRACT)

        # Front ankle servo horn tab (Y = +11.5 .. +16.5, Z = 6.5 .. 26.5)
        with bd.Locations((lx, 14.0, 16.5)):
            bd.Box(15.0, 5.0, 20.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        with bd.Locations((lx, 14.0, az)):
            bd.Cylinder(
                radius=7.5,
                height=5.0,
                rotation=(90, 0, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )

        # Rear ankle dummy pivot heel tab (Y = -25.5 .. -20.5, Z = 6.5 .. 26.5)
        with bd.Locations((lx, -23.0, 16.5)):
            bd.Box(15.0, 5.0, 20.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        with bd.Locations((lx, -23.0, az)):
            bd.Cylinder(
                radius=7.5,
                height=5.0,
                rotation=(90, 0, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )
        # Rear dummy pivot pin protruding forward (+Y) from Y=-21.0 to Y=-16.5
        with bd.Locations((lx, -18.8, az)):
            bd.Cylinder(
                radius=2.4,
                height=5.0,
                rotation=(90, 0, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )

        bd.add(ankle_horn, mode=Mode.SUBTRACT)

    return foot.part


def build_biped_parts() -> tuple[bd.Part, bd.Part, bd.Part, bd.Part, bd.Part]:
    """Return (pelvis, leg_left, leg_right, foot_left, foot_right)."""
    pelvis = build_pelvis()

    leg_l = _build_left_leg()
    leg_l.label = "leg_left"
    leg_l.color = bd.Color(0.84, 0.85, 0.87)

    leg_r = bd.Part() + bd.mirror(leg_l, bd.Plane.YZ)
    leg_r.label = "leg_right"
    leg_r.color = bd.Color(0.84, 0.85, 0.87)

    foot_l = _build_left_foot()
    foot_l.label = "foot_left"
    foot_l.color = bd.Color(0.28, 0.30, 0.34)

    foot_r = bd.Part() + bd.mirror(foot_l, bd.Plane.YZ)
    foot_r.label = "foot_right"
    foot_r.color = bd.Color(0.28, 0.30, 0.34)

    return pelvis, leg_l, leg_r, foot_l, foot_r
