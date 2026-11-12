"""Rear electronics backpack for Bipedal Waddle Mode (houses PCA9685 + ESP32 + power I/O)."""

from __future__ import annotations

import build123d as bd
from build123d import Align, Mode

from .helpers import standoff
from .params import ELEC, GEO, TOL


def build_backpack_parts() -> tuple[bd.Part, bd.Part]:
    """Build (backpack_shell, backpack_lid) mounted onto torso_back at Y = -33 mm."""
    z_bot = GEO.torso_z0 + 4.0   # 82.0 mm
    z_top = GEO.torso_z0 + 72.0  # 150.0 mm
    h = z_top - z_bot            # 68.0 mm
    w = 72.0                     # Wide enough for 62.5mm PCA9685 + bottom-corner clearance
    depth = 30.0
    y_front = -33.0
    y_back = y_front - depth     # -63.0 mm
    wall = 2.6

    with bd.BuildPart() as shell_bp:
        with bd.BuildSketch() as sk:
            bd.RectangleRounded(w, h, radius=14.0)
        outer = bd.extrude(sk.sketch, amount=depth, mode=Mode.PRIVATE)
        outer = outer.rotate(bd.Axis.X, 90)  # +Z -> -Y
        outer = outer.translate((0, y_front, z_bot + h / 2))
        bd.add(outer)

        # Hollow inner cavity open toward rear (-Y) for lid
        with bd.BuildSketch() as sk_in:
            bd.RectangleRounded(w - 2 * wall, h - 2 * wall, radius=11.5)
        inner = bd.extrude(sk_in.sketch, amount=depth, mode=Mode.PRIVATE)
        inner = inner.rotate(bd.Axis.X, 90)
        inner = inner.translate((0, y_front - 3.2, z_bot + h / 2))
        bd.add(inner, mode=Mode.SUBTRACT)

        # Rabbet step for flush backpack_lid at rear (Y = y_back .. y_back + 3.0)
        with bd.BuildSketch() as sk_rab:
            bd.RectangleRounded(w - wall, h - wall, radius=12.8)
        rab = bd.extrude(sk_rab.sketch, amount=3.2, mode=Mode.PRIVATE)
        rab = rab.rotate(bd.Axis.X, 90)
        rab = rab.translate((0, y_back + 3.0, z_bot + h / 2))
        bd.add(rab, mode=Mode.SUBTRACT)

        # Wire pass-through port matching torso_back (0, Z = torso_z0 + 26 = 104)
        with bd.Locations((0, y_front - 1.5, GEO.torso_z0 + 26.0)):
            bd.Box(18.0, 10.0, 14.0, align=(Align.CENTER, Align.CENTER, Align.CENTER), mode=Mode.SUBTRACT)

        # 4x M3 clearance holes + counterbores matching torso_back bosses
        for bx in (-16.0, 16.0):
            for bz in (GEO.torso_z0 + 18.0, GEO.torso_z0 + 40.0):
                with bd.Locations((bx, y_front - 2.0, bz)):
                    bd.Cylinder(
                        radius=1.7,
                        height=10.0,
                        rotation=(90, 0, 0),
                        align=(Align.CENTER, Align.CENTER, Align.CENTER),
                        mode=Mode.SUBTRACT,
                    )

        # PCA9685 horizontal standoffs on front inner wall (Y = -36.2 toward -Y) centered at Z=133
        pca_z = z_bot + 51.0  # 133.0 mm
        for sx in (-1, 1):
            for sz in (-1, 1):
                px = sx * (ELEC.pca_hole_dx / 2)
                pz = pca_z + sz * (ELEC.pca_hole_dy / 2)
                post, hole = standoff(4.5, outer_d=5.2, hole_d=2.2)
                post = post.rotate(bd.Axis.X, 90).translate((px, y_front - 3.0, pz))
                hole = hole.rotate(bd.Axis.X, 90).translate((px, y_front - 3.2, pz))
                bd.add(post)
                bd.add(hole, mode=Mode.SUBTRACT)

        # 4x M2.5 lid corner bosses inside outer wall (clear of battery & UBEC)
        for sx in (-1, 1):
            for sz in (-1, 1):
                lx = sx * 29.0
                lz = (z_bot + h / 2) + sz * 26.5
                post, hole = standoff(8.0, outer_d=5.6, hole_d=2.2)
                post = post.rotate(bd.Axis.X, -90).translate((lx, y_back + 11.0, lz))
                hole = hole.rotate(bd.Axis.X, -90).translate((lx, y_back + 11.2, lz))
                bd.add(post)
                bd.add(hole, mode=Mode.SUBTRACT)

        # Bottom cutouts (Z = z_bot) for CyberBrick power switch & 2S USB-C charging port
        with bd.Locations((-14.0, y_back + 13.0, z_bot + 1.5)):
            bd.Box(ELEC.cb_switch_w, ELEC.cb_switch_h, 8.0, align=(Align.CENTER, Align.CENTER, Align.CENTER), mode=Mode.SUBTRACT)
        with bd.Locations((18.0, y_back + 15.0, z_bot + 1.5)):
            bd.Box(ELEC.usbc_chg_w, ELEC.usbc_chg_h, 8.0, align=(Align.CENTER, Align.CENTER, Align.CENTER), mode=Mode.SUBTRACT)
        # Internal retention cradle around the 2S USB-C charger PCB (vertical, X=+18.0, Y=-48)
        for dy in (-3.6, 3.6):
            with bd.Locations((18.0, y_back + 15.0 + dy, z_bot + 10.0)):
                bd.Box(13.5, 2.0, 15.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))

    # --- BACKPACK LID ---
    with bd.BuildPart() as lid_bp:
        with bd.BuildSketch() as sk_lid:
            bd.RectangleRounded(w - wall - 2 * TOL.slip, h - wall - 2 * TOL.slip, radius=12.4)
        lid_sol = bd.extrude(sk_lid.sketch, amount=2.8, mode=Mode.PRIVATE)
        lid_sol = lid_sol.rotate(bd.Axis.X, 90)
        lid_sol = lid_sol.translate((0, y_back + 2.8, z_bot + h / 2))
        bd.add(lid_sol)

        # CyberBrick 2S 14500 7.4V battery cradle on inner face of lid (X = -6, Z = z_bot + 34)
        batt_cx = -6.0
        batt_cz = z_bot + 34.0
        batt_y = y_back + 2.8
        # Left & right cradle side walls + bottom shelf (open top for easy battery swap)
        for sx in (-1, 1):
            with bd.Locations((batt_cx + sx * (ELEC.batt_14500_w / 2 + 1.2), batt_y + 7.5, batt_cz)):
                bd.Box(2.2, 15.0, ELEC.batt_14500_h - 8.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        with bd.Locations((batt_cx, batt_y + 7.5, batt_cz - (ELEC.batt_14500_h / 2 + 1.2))):
            bd.Box(ELEC.batt_14500_w + 4.4, 15.0, 2.2, align=(Align.CENTER, Align.CENTER, Align.CENTER))

        # 5V 5A UBEC zip-tie mounting bridge on right side of lid (X = +18)
        for sz in (-10.0, 10.0):
            with bd.Locations((18.0, batt_y + 2.5, batt_cz + sz)):
                bd.Box(12.0, 5.0, 3.5, align=(Align.CENTER, Align.CENTER, Align.CENTER))
                bd.Box(7.0, 2.5, 4.0, align=(Align.CENTER, Align.CENTER, Align.CENTER), mode=Mode.SUBTRACT)

        # 4x M2.5 lid screw clearance holes + countersinks
        for sx in (-1, 1):
            for sz in (-1, 1):
                lx = sx * 29.0
                lz = (z_bot + h / 2) + sz * 26.5
                with bd.Locations((lx, y_back + 1.5, lz)):
                    bd.Cylinder(
                        radius=1.4,
                        height=6.0,
                        rotation=(90, 0, 0),
                        align=(Align.CENTER, Align.CENTER, Align.CENTER),
                        mode=Mode.SUBTRACT,
                    )

        # Vertical cooling louvers on rear face
        for vx in (-10.0, 0.0, 10.0):
            with bd.Locations((vx, y_back + 1.4, z_bot + h / 2 + 12.0)):
                bd.Box(2.2, 6.0, 14.0, align=(Align.CENTER, Align.CENTER, Align.CENTER), mode=Mode.SUBTRACT)

    shell = shell_bp.part
    shell.label = "backpack_shell"
    shell.color = bd.Color(0.28, 0.30, 0.34)

    lid = lid_bp.part
    lid.label = "backpack_lid"
    lid.color = bd.Color(0.20, 0.22, 0.26)
    return shell, lid
