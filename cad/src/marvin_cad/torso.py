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
        (68.0, 33.5, 29.0),
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
    # Mirror-symmetric: both Left (+1) and Right (-1) have far end pointing -Z (downward)
    if side == 1:
        cav = cav.rotate(bd.Axis.Y, 90).rotate(bd.Axis.X, 180)
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

    # Split planes & outer/inner clippers (built outside BuildPart so no context side effects)
    split_z = GEO.torso_z0 + GEO.torso_h / 2
    front_cutter = bd.Box(200, 200, 200, align=(Align.CENTER, Align.MIN, Align.CENTER)).translate((0, 0, split_z))
    back_cutter = bd.Box(200, 200, 200, align=(Align.CENTER, Align.MAX, Align.CENTER)).translate((0, 0, split_z))
    outer_clip = _torso_outer_loft(inset=0.5).translate((0, 0, GEO.torso_z0))
    shell_front = shell & front_cutter
    shell_back = shell & back_cutter
    pan_cav = _pan_cavity_in_world()
    sh_cav_l = _shoulder_cavity_in_world(1)
    sh_cav_r = _shoulder_cavity_in_world(-1)

    # Extra clean Ø13.4mm side bore around shoulder shafts to prevent tiny shell slivers
    sh_bore = bd.Cylinder(
        radius=6.7,
        height=90.0,
        rotation=(0, 90, 0),
        align=(Align.CENTER, Align.CENTER, Align.CENTER),
    ).translate((0, 0, GEO.shoulder_z))

    # Fastener boss coordinates (X, Z) verified clear of servos & speaker
    split_screws = [
        (-18.0, GEO.torso_z0 + 67.5),   # Z = 101.5
        (18.0, GEO.torso_z0 + 67.5),
        (-24.0, GEO.torso_z0 + 15.0),   # Z = 49.0
        (24.0, GEO.torso_z0 + 15.0),
    ]

    # Precompute M3 front bosses clipped cleanly inside outer torso wall so they never float
    front_bosses = []
    front_holes = []
    for sx, sz in split_screws:
        b_sol, b_hole = m3_boss(36.0, outer_d=7.4, pilot=True, hole_depth=12.0)
        b_sol = b_sol.rotate(bd.Axis.X, -90).translate((sx, 0.0, sz)) & outer_clip
        b_hole = b_hole.rotate(bd.Axis.X, -90).translate((sx, -0.2, sz))
        front_bosses.append(b_sol)
        front_holes.append(b_hole)

    # Reinforced Ø21 mm shoulder collar bosses (X = +/-34.75 .. +/-39.75, clear of saddles)
    sh_collar_front = [
        (
            bd.Cylinder(radius=10.5, height=5.0, rotation=(0, 90, 0), align=(Align.CENTER, Align.CENTER, Align.CENTER))
            .translate((side * (GEO.shoulder_x - 1.75), 0, GEO.shoulder_z))
            & front_cutter
        )
        for side in (-1, 1)
    ]
    sh_collar_back = [
        (
            bd.Cylinder(radius=10.5, height=5.0, rotation=(0, 90, 0), align=(Align.CENTER, Align.CENTER, Align.CENTER))
            .translate((side * (GEO.shoulder_x - 1.75), 0, GEO.shoulder_z))
            & back_cutter
        )
        for side in (-1, 1)
    ]

    # --- TORSO FRONT ---
    with bd.BuildPart() as tf:
        bd.add(shell_front)
        for col in sh_collar_front:
            bd.add(col)

        # M3 screw pilot bosses fusing all the way to the front inner wall
        for b_sol in front_bosses:
            bd.add(b_sol)
        for b_hole in front_holes:
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
        bd.add(sh_bore, mode=Mode.SUBTRACT)
        bd.add(pan_cav, mode=Mode.SUBTRACT)

    # Precompute shoulder saddles (fused to torso_back wall for Y<=0, 0.3mm clear of torso_front for Y>0)
    inner_clear_front = _torso_outer_loft(inset=3.1, z_min=4.5, z_max=70.5).translate((0, 0, GEO.torso_z0))
    back_clip = outer_clip & back_cutter
    front_clear_clip = inner_clear_front & front_cutter
    shoulder_saddles = []
    for side in (-1, 1):
        s_box = bd.Box(8.0, 26.0, 36.0, align=(Align.CENTER, Align.CENTER, Align.CENTER)).translate(
            (side * (GEO.shoulder_x - 8.5), -6.5, GEO.shoulder_z - 5.6)
        )
        shoulder_saddles.append((s_box & back_clip) + (s_box & front_clear_clip))

    # Precompute pan-servo tray & 45-deg gusset ribs fused to torso_back rear wall & neck crown
    with bd.BuildPart() as pan_tray_bp:
        with bd.Locations((0, -11.5, GEO.neck_pan_z - 12.5)):
            bd.Box(24.0, 37.0, 4.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with bd.Locations((0, -8.5, GEO.neck_pan_z - 15.5)):
            bd.Box(16.0, 41.0, 5.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
        for gx in (-9.5, 9.5):
            with bd.BuildSketch(bd.Plane.YZ.offset(gx)) as gsk:
                with bd.BuildLine():
                    bd.Polyline(
                        (-29.0, GEO.neck_pan_z - 12.5),
                        (6.0, GEO.neck_pan_z - 12.5),
                        (-29.0, GEO.neck_pan_z - 40.0),
                        close=True,
                    )
                bd.make_face()
            bd.extrude(gsk.sketch, amount=2.4, both=True)
    pan_tray_solid = pan_tray_bp.part & outer_clip

    back_bosses = []
    for sx, sz in split_screws:
        b_sol, _ = m3_boss(36.0, outer_d=7.4, pilot=False)
        back_bosses.append(b_sol.rotate(bd.Axis.X, 90).translate((sx, 0.0, sz)) & outer_clip)

    # --- TORSO BACK ---
    with bd.BuildPart() as tb:
        bd.add(shell_back)
        for col in sh_collar_back:
            bd.add(col)
        bd.add(pan_tray_solid)

        # Internal shoulder servo saddles (fused into torso_back wall)
        for saddle in shoulder_saddles:
            bd.add(saddle)

        # M3 clearance holes + rear counterbores for split screws
        for (sx, sz), b_sol in zip(split_screws, back_bosses):
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
        bd.add(sh_bore, mode=Mode.SUBTRACT)

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

    front = max(tf.part.solids(), key=lambda s: s.volume)
    front = bd.Part() + front
    front.label = "torso_front"
    front.color = bd.Color(0.86, 0.87, 0.89)

    back = max(tb.part.solids(), key=lambda s: s.volume)
    back = bd.Part() + back
    back.label = "torso_back"
    back.color = bd.Color(0.80, 0.82, 0.85)
    return front, back
