"""Marvin capsule torso (split front/back) with 3 servo mounts and chest speaker grille."""

from __future__ import annotations

import math

import build123d as bd
from build123d import Align, Mode

from .helpers import m3_boss, servo_9g_cavity
from .params import ELEC, GEO, SERVO, TOL


def _torso_outer_loft(inset: float = 0.0, z_min: float = 0.0, z_max: float = 74.0) -> bd.Part:
    """Create the smooth Marvin torso capsule in local coords (Z = 0 .. 74 above waist)."""
    # Profile stations: (z, rx, ry)
    stations = [
        (0.0, 26.5, 25.0),
        (16.0, 36.5, 31.0),
        (48.0, 39.0, 32.0),
        (68.0, 31.5, 27.5),
        (74.0, 22.0, 22.0),
    ]
    sketches = []
    for z, rx, ry in stations:
        if z < z_min - 0.1 or z > z_max + 0.1:
            continue
        with bd.BuildSketch(bd.Plane.XY.offset(z)) as sk:
            bd.Ellipse(max(rx - inset, 4.0), max(ry - inset, 4.0))
        sketches.append(sk.sketch)

    if z_min > 0.1 and not any(abs(s[0] - z_min) < 0.2 for s in stations):
        with bd.BuildSketch(bd.Plane.XY.offset(z_min)) as sk:
            bd.Ellipse(26.5 - inset, 25.0 - inset)
        sketches.insert(0, sk.sketch)

    if z_max < 73.9 and not any(abs(s[0] - z_max) < 0.2 for s in stations):
        with bd.BuildSketch(bd.Plane.XY.offset(z_max)) as sk:
            bd.Ellipse(24.0 - inset, 22.5 - inset)
        sketches.append(sk.sketch)

    return bd.Part() + bd.loft(sketches)


def _base_torso_shell() -> bd.Part:
    """Build the unsplit hollow torso shell in world Z (34 .. 108 mm)."""
    wall = 2.8
    with bd.BuildPart() as t:
        bd.add(_torso_outer_loft(inset=0.0))
        # Hollow interior leaving 4.5 mm waist floor and 3.0 mm neck crown
        with bd.BuildPart(mode=Mode.SUBTRACT):
            inner = _torso_outer_loft(inset=wall, z_min=4.5, z_max=70.5)
            bd.add(inner)

        # Waist wire tunnel (Z = 0 .. 6)
        with bd.Locations((0, 0, -0.5)):
            bd.Cylinder(
                radius=GEO.wire_tunnel_d / 2,
                height=7.0,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
                mode=Mode.SUBTRACT,
            )

        # Waist mounting bosses (4x M3 pilot holes matching base_shell)
        waist_r = GEO.waist_bolt_d / 2
        for deg in (45, 135, 225, 315):
            rad = math.radians(deg)
            wx, wy = waist_r * math.cos(rad), waist_r * math.sin(rad)
            b_sol, b_hole = m3_boss(11.0, outer_d=7.8, pilot=True, hole_depth=10.0)
            with bd.Locations((wx, wy, 0.0)):
                bd.add(b_sol)
            with bd.Locations((wx, wy, -0.2)):
                bd.add(b_hole, mode=Mode.SUBTRACT)

    # Shift to world Z = torso_z0 (34.0)
    return bd.Part() + t.part.translate((0, 0, GEO.torso_z0))


def _shoulder_cavity_in_world(side: int) -> bd.Part:
    """Return shoulder pitch servo cavity positioned in world coordinates.

    side = +1 for Left (+X), -1 for Right (-X).
    Shaft axis points outward along side * X at (Y=0, Z=GEO.shoulder_z).
    """
    cav = servo_9g_cavity(tol=TOL.slip, wire_slot=True, pilot_holes=True, shaft_clearance_d=13.2)
    # Local servo: shaft at +Z, long axis +X (far end at -X).
    # Rotate so shaft (+Z) -> +X (for side=+1) and far end (-X) -> -Z (downward):
    # Rotate -90 around Y -> local +Z becomes -X, local +X becomes +Z.
    # For side=+1 (+X shaft): rotate +90 around Y -> local +Z becomes +X, local +X becomes -Z!
    if side == 1:
        cav = cav.rotate(bd.Axis.Y, 90)
        flange_x = GEO.shoulder_x - 6.5  # boss extends ~6.5..10.5 out to X=39..43
        cav = cav.translate((flange_x, 0, GEO.shoulder_z))
    else:
        cav = cav.rotate(bd.Axis.Y, -90)
        flange_x = -(GEO.shoulder_x - 6.5)
        cav = cav.translate((flange_x, 0, GEO.shoulder_z))
    return bd.Part() + cav


def _pan_cavity_in_world() -> bd.Part:
    """Return head pan servo cavity in world coordinates.

    Shaft points +Z at (X=0, Y=0), top of boss near Z = GEO.neck_pan_z (108).
    """
    cav = servo_9g_cavity(tol=TOL.slip, wire_slot=True, pilot_holes=True, shaft_clearance_d=13.2)
    # Rotate +90 around Z so servo body extends toward -Y (into Torso Back)
    cav = cav.rotate(bd.Axis.Z, 90)
    # Underside of flange at Z = neck_pan_z - 10.5 = 97.5
    cav = cav.translate((0, 0, GEO.neck_pan_z - 10.5))
    return bd.Part() + cav


def build_torso_parts() -> tuple[bd.Part, bd.Part]:
    """Build Torso Front (Y >= 0) and Torso Back (Y <= 0) with all internal mounts."""
    shell = _base_torso_shell()

    # Split planes (built outside BuildPart so no context side effects)
    split_z = GEO.torso_z0 + GEO.torso_h / 2
    front_cutter = bd.Box(200, 200, 200, align=(Align.CENTER, Align.MIN, Align.CENTER)).translate((0, 0, split_z))
    back_cutter = bd.Box(200, 200, 200, align=(Align.CENTER, Align.MAX, Align.CENTER)).translate((0, 0, split_z))
    shell_front = shell & front_cutter
    shell_back = shell & back_cutter
    pan_cav = _pan_cavity_in_world()
    sh_cav_l = _shoulder_cavity_in_world(1)
    sh_cav_r = _shoulder_cavity_in_world(-1)

    # Fastener boss coordinates (X, Z) verified clear of servos & speaker
    split_screws = [
        (-18.0, GEO.torso_z0 + 67.5),   # Z = 101.5
        (18.0, GEO.torso_z0 + 67.5),
        (-24.0, GEO.torso_z0 + 15.0),   # Z = 49.0
        (24.0, GEO.torso_z0 + 15.0),
    ]

    # --- TORSO FRONT ---
    with bd.BuildPart() as tf:
        bd.add(shell_front)

        # M3 screw pilot bosses on inside of front shell facing -Y
        for sx, sz in split_screws:
            b_sol, b_hole = m3_boss(12.0, outer_d=7.2, pilot=True, hole_depth=10.0)
            b_sol = b_sol.rotate(bd.Axis.X, -90).translate((sx, 0.0, sz))
            b_hole = b_hole.rotate(bd.Axis.X, -90).translate((sx, -0.2, sz))
            bd.add(b_sol)
            bd.add(b_hole, mode=Mode.SUBTRACT)

        # Chest 28 mm speaker cradle (centered at X=0, Z=GEO.speaker_z=62)
        sp_y = 29.0  # inner front wall near Z=62 is ~Y=28..30
        with bd.Locations((0, sp_y - 3.0, GEO.speaker_z)):
            bd.Cylinder(
                radius=ELEC.speaker_d / 2 + 2.4,
                height=7.0,
                rotation=(90, 0, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.ADD,
            )
            bd.Cylinder(
                radius=ELEC.speaker_d / 2,
                height=9.0,
                rotation=(90, 0, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )

        # Perforated speaker grille holes through front wall
        grille_pts = [(0.0, 0.0)]
        for ring_r, n_pts in ((5.5, 6), (10.5, 12)):
            for i in range(n_pts):
                ang = 2 * math.pi * i / n_pts
                grille_pts.append((ring_r * math.cos(ang), ring_r * math.sin(ang)))
        for gx, gz in grille_pts:
            with bd.Locations((gx, 32.0, GEO.speaker_z + gz)):
                bd.Cylinder(
                    radius=1.1,
                    height=14.0,
                    rotation=(90, 0, 0),
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                    mode=Mode.SUBTRACT,
                )

        # Subtract shoulder boss & pan boss clearance along Y=0 split seam
        bd.add(sh_cav_l, mode=Mode.SUBTRACT)
        bd.add(sh_cav_r, mode=Mode.SUBTRACT)
        bd.add(pan_cav, mode=Mode.SUBTRACT)

    inner_clear = _torso_outer_loft(inset=3.1, z_min=4.5, z_max=70.5).translate((0, 0, GEO.torso_z0))
    shoulder_saddles = [
        bd.Box(7.0, 25.0, 36.0, align=(Align.CENTER, Align.CENTER, Align.CENTER)).translate(
            (side * (GEO.shoulder_x - 8.5), -6.0, GEO.shoulder_z - 5.6)
        )
        & inner_clear
        for side in (-1, 1)
    ]

    # --- TORSO BACK ---
    with bd.BuildPart() as tb:
        bd.add(shell_back)

        # Internal pan-servo tray extending from rear wall (Y=-24) to Y=+7 at Z=94.5..98.5
        with bd.Locations((0, -8.5, GEO.neck_pan_z - 12.5)):
            bd.Box(24.0, 31.0, 4.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
        # Extra material around pan servo mounting ears
        with bd.Locations((0, -5.6, GEO.neck_pan_z - 15.5)):
            bd.Box(16.0, 35.0, 5.0, align=(Align.CENTER, Align.CENTER, Align.MIN))

        # Internal shoulder servo saddles (Left +X and Right -X), clipped with 0.3 mm wall clearance
        for saddle in shoulder_saddles:
            bd.add(saddle)

        # M3 clearance holes + rear counterbores for split screws
        for sx, sz in split_screws:
            b_sol, _ = m3_boss(14.0, outer_d=7.2, pilot=False)
            b_sol = b_sol.rotate(bd.Axis.X, 90).translate((sx, 0.0, sz))
            bd.add(b_sol)
            # Through clearance hole along Y (Y = -34 .. 0)
            with bd.Locations((sx, -17.0, sz)):
                bd.Cylinder(
                    radius=1.7,
                    height=34.0,
                    rotation=(90, 0, 0),
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                    mode=Mode.SUBTRACT,
                )
            # Rear counterbore for M3 socket cap screw head (Y = -35 .. -10)
            with bd.Locations((sx, -22.5, sz)):
                bd.Cylinder(
                    radius=3.1,
                    height=25.0,
                    rotation=(90, 0, 0),
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                    mode=Mode.SUBTRACT,
                )

        # Subtract the 3 torso servo cavities cleanly
        bd.add(pan_cav, mode=Mode.SUBTRACT)
        bd.add(sh_cav_l, mode=Mode.SUBTRACT)
        bd.add(sh_cav_r, mode=Mode.SUBTRACT)

        # Cable routing slot behind neck pan servo (Y = -14.5)
        with bd.Locations((0, -14.5, GEO.neck_pan_z - 2.0)):
            bd.Box(14.0, 7.0, 14.0, align=(Align.CENTER, Align.CENTER, Align.CENTER), mode=Mode.SUBTRACT)

        # Backpack mounting bosses (4x M3 pilot holes facing -Y) & wire pass-through port
        bp_port_z = GEO.torso_z0 + 26.0  # Z = 104 mm
        with bd.Locations((0, -29.0, bp_port_z)):
            bd.Box(18.0, 14.0, 14.0, align=(Align.CENTER, Align.CENTER, Align.CENTER), mode=Mode.SUBTRACT)

        for bx in (-16.0, 16.0):
            for bz in (GEO.torso_z0 + 18.0, GEO.torso_z0 + 40.0):
                bp_sol, bp_hole = m3_boss(8.5, outer_d=7.2, pilot=True, hole_depth=7.5)
                bp_sol = bp_sol.rotate(bd.Axis.X, 90).translate((bx, -24.5, bz))
                bp_hole = bp_hole.rotate(bd.Axis.X, 90).translate((bx, -25.7, bz))
                bd.add(bp_sol)
                bd.add(bp_hole, mode=Mode.SUBTRACT)

    front = tf.part
    front.label = "torso_front"
    front.color = bd.Color(0.86, 0.87, 0.89)

    back = tb.part
    back.label = "torso_back"
    back.color = bd.Color(0.80, 0.82, 0.85)
    return front, back
