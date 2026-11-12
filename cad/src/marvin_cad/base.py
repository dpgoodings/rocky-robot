"""Weighted desk base housing and removable electronics tray lid."""

from __future__ import annotations

import math

import build123d as bd
from build123d import Align, Mode

from .helpers import m3_boss, standoff
from .params import ELEC, GEO, TOL


def build_base_shell() -> bd.Part:
    """Build the tapered desk plinth (Z = 0 .. 34 mm) with rear I/O ports."""
    r_bot = GEO.base_bottom_d / 2
    r_top = GEO.base_top_d / 2
    h = GEO.base_h
    wall = 3.4
    lid_r = r_bot - wall + 1.0  # Rabbet lip radius for flush lid

    with bd.BuildPart() as base:
        # Outer tapered cylinder
        bd.Cone(
            bottom_radius=r_bot,
            top_radius=r_top,
            height=h,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        # Fillet top shoulder
        top_edges = base.edges().filter_by(lambda e: abs(e.center().Z - h) < 0.5)
        bd.fillet(top_edges, radius=3.5)

        # Hollow internal cavity (Z = 0 .. h - 4.0)
        with bd.BuildPart(mode=Mode.SUBTRACT):
            bd.Cone(
                bottom_radius=r_bot - wall,
                top_radius=r_top - wall,
                height=h - 4.0,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )
            # Rabbet step for flush bottom lid (Z = 0 .. 3.2)
            bd.Cylinder(
                radius=lid_r,
                height=GEO.base_lid_h + 0.2,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )

        # 4x M3 bosses inside perimeter for bottom lid (R = 49.5 mm)
        boss_r = 49.5
        boss_h = 16.0
        for deg in (45, 135, 225, 315):
            rad = math.radians(deg)
            bx, by = boss_r * math.cos(rad), boss_r * math.sin(rad)
            b_solid, b_hole = m3_boss(boss_h, outer_d=7.6, pilot=True, hole_depth=12.0)
            with bd.Locations((bx, by, GEO.base_lid_h)):
                bd.add(b_solid)
            with bd.Locations((bx, by, GEO.base_lid_h)):
                bd.add(b_hole, mode=Mode.SUBTRACT)

        # Top waist interface: central wire tunnel + 4x M3 waist bolts
        with bd.Locations((0, 0, h - 6.0)):
            bd.Cylinder(
                radius=GEO.wire_tunnel_d / 2,
                height=8.0,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
                mode=Mode.SUBTRACT,
            )

        waist_r = GEO.waist_bolt_d / 2
        for deg in (45, 135, 225, 315):
            rad = math.radians(deg)
            wx, wy = waist_r * math.cos(rad), waist_r * math.sin(rad)
            with bd.Locations((wx, wy, h - 6.0)):
                bd.Cylinder(
                    radius=1.7,  # M3 clearance
                    height=8.0,
                    align=(Align.CENTER, Align.CENTER, Align.MIN),
                    mode=Mode.SUBTRACT,
                )

        # Rear (-Y) USB port window aligned with ESP32 (X = -14)
        with bd.Locations((-14.0, -r_bot + 2.0, 11.5)):
            bd.Box(
                ELEC.usb_cutout_w,
                16.0,
                ELEC.usb_cutout_h,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )

        # Rear (-Y) 5.5x2.1 mm DC barrel jack bore (X = +16)
        with bd.Locations((16.0, -r_bot + 2.0, 15.0)):
            bd.Cylinder(
                radius=ELEC.barrel_jack_d / 2,
                height=18.0,
                rotation=(90, 0, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )
            # Internal flat spot so barrel jack nut seats cleanly
            with bd.Locations((0, 7.5, 0)):
                bd.Box(
                    14.0,
                    6.0,
                    14.0,
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                    mode=Mode.SUBTRACT,
                )

        # Subtle rear ventilation slots
        for vx in (-3.0, 3.0):
            with bd.Locations((vx, -r_bot + 2.0, 18.0)):
                bd.Box(
                    2.4,
                    14.0,
                    10.0,
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                    mode=Mode.SUBTRACT,
                )

    part = base.part
    part.label = "base_shell"
    part.color = bd.Color(0.84, 0.85, 0.87)  # Satin silver-white (Marvin finish)
    return part


def build_base_lid() -> bd.Part:
    """Build the removable bottom lid with ESP32 + PCA9685 standoffs and ballast pocket."""
    r_bot = GEO.base_bottom_d / 2
    wall = 3.4
    lid_r = r_bot - wall + 1.0 - TOL.slip  # Slip fit into base rabbet
    h = GEO.base_lid_h
    so_h = 5.0

    with bd.BuildPart() as lid:
        # Main bottom plate (Z = 0 .. 3.0)
        bd.Cylinder(
            radius=lid_r,
            height=h,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )

        # 4x M3 countersunk screw holes matching base bosses
        boss_r = 49.5
        for deg in (45, 135, 225, 315):
            rad = math.radians(deg)
            bx, by = boss_r * math.cos(rad), boss_r * math.sin(rad)
            with bd.Locations((bx, by, -0.5)):
                bd.Cylinder(
                    radius=1.7,
                    height=h + 1.0,
                    align=(Align.CENTER, Align.CENTER, Align.MIN),
                    mode=Mode.SUBTRACT,
                )
                # Countersink pocket on underside
                bd.Cone(
                    bottom_radius=3.2,
                    top_radius=1.7,
                    height=2.0,
                    align=(Align.CENTER, Align.CENTER, Align.MIN),
                    mode=Mode.SUBTRACT,
                )

        # ESP32 DevKit standoffs centered at X = -14.0, Y = -18.0 (USB facing -Y)
        esp_cx, esp_cy = -14.0, -18.0
        for sx in (-1, 1):
            for sy in (-1, 1):
                px = esp_cx + sx * (ELEC.esp32_hole_dx / 2)
                py = esp_cy + sy * (ELEC.esp32_hole_dy / 2)  # wait: long axis along Y!
                # Swap dx/dy so long axis runs along Y toward rear port
                px = esp_cx + sx * (ELEC.esp32_hole_dy / 2)
                py = esp_cy + sy * (ELEC.esp32_hole_dx / 2)
                post, hole = standoff(so_h, outer_d=5.2, hole_d=2.2)
                with bd.Locations((px, py, h)):
                    bd.add(post)
                with bd.Locations((px, py, h)):
                    bd.add(hole, mode=Mode.SUBTRACT)

        # PCA9685 16-ch driver standoffs centered at X = +17.5, Y = +2.0
        pca_cx, pca_cy = 17.5, 2.0
        for sx in (-1, 1):
            for sy in (-1, 1):
                px = pca_cx + sx * (ELEC.pca_hole_dy / 2)
                py = pca_cy + sy * (ELEC.pca_hole_dx / 2)
                post, hole = standoff(so_h, outer_d=5.2, hole_d=2.2)
                with bd.Locations((px, py, h)):
                    bd.add(post)
                with bd.Locations((px, py, h)):
                    bd.add(hole, mode=Mode.SUBTRACT)

        # Front ballast weight cup (holds M8 washers / coins / steel slugs)
        with bd.Locations((-10.0, 26.0, h)):
            bd.Cylinder(
                radius=15.0,
                height=5.0,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )
            bd.Cylinder(
                radius=12.8,
                height=5.5,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
                mode=Mode.SUBTRACT,
            )

    part = lid.part
    part.label = "base_lid"
    part.color = bd.Color(0.28, 0.30, 0.34)  # Dark charcoal accent
    return part
