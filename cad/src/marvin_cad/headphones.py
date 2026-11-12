"""Faux studio headphones (headband arch + snap-on ear cups) for Marvin's head."""

from __future__ import annotations

import build123d as bd
from build123d import Align, Mode

from .params import GEO, TOL


def _build_left_cup() -> bd.Part:
    """Build Left (+X) faux studio headphone ear cup fitting over Marvin's ear disc."""
    cx = GEO.head_w / 2 - 1.5   # 46.5 mm (inner ear disc sits at X = 46..48.5)
    cz = GEO.head_center_z      # 202.0 mm

    with bd.BuildPart() as cup:
        # Soft ear cushion torus/ring (X = 46.25 .. 51.25, Ø40 mm)
        with bd.Locations((cx + 2.25, 0.0, cz)):
            bd.Cylinder(
                radius=20.0,
                height=5.0,
                rotation=(0, 90, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )
        with bd.Locations((cx + 8.5, 0.0, cz)):
            bd.Cone(
                bottom_radius=18.5,
                top_radius=13.5,
                height=8.0,
                rotation=(0, 90, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )

        # Subtle recessed decorative ring on outer cup face (X = 59.0)
        with bd.Locations((cx + 12.2, 0.0, cz)):
            bd.Cylinder(
                radius=10.5,
                height=1.2,
                rotation=(0, 90, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )
        with bd.Locations((cx + 12.0, 0.0, cz)):
            bd.Cylinder(
                radius=8.5,
                height=1.2,
                rotation=(0, 90, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )

        # Inner ear-disc slip pocket (Ø25.4 mm, X = 45.0 .. 49.5)
        with bd.Locations((cx + 0.5, 0.0, cz)):
            bd.Cylinder(
                radius=12.75,
                height=6.0,
                rotation=(0, 90, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )

        # Top headband socket tab slot (8.4 x 10.4 mm at Z = cz + 15..22)
        with bd.Locations((cx + 6.5, 0.0, cz + 17.5)):
            bd.Box(7.4, 12.4, 10.0, align=(Align.CENTER, Align.CENTER, Align.CENTER), mode=Mode.SUBTRACT)

    return cup.part


def build_headphone_band() -> bd.Part:
    """Build over-the-head curved studio headphone band arch with end mounting tabs."""
    cz = GEO.head_center_z  # 202.0 mm

    # Elliptical arch over head crown (head dome top is cz + 44 = 246 mm, width X = +/-48 mm)
    # Outer arch: rx=58.5, rz=53.0; Inner arch: rx=52.0, rz=47.0 (3.0 mm crown gap)
    with bd.BuildPart() as band:
        with bd.BuildSketch() as sk:
            bd.Ellipse(58.5, 53.0)
            bd.Ellipse(52.0, 47.0, mode=Mode.SUBTRACT)
            # Keep upper arch Z >= 20.0 in local sketch coords
            with bd.Locations((0, -33.0)):
                bd.Rectangle(130.0, 106.0, mode=Mode.SUBTRACT)
        arch = bd.extrude(sk.sketch, amount=9.0, both=True, mode=Mode.PRIVATE)
        # Rotate so sketch XY becomes XZ (normal Y, arch spans X and rises +Z)
        arch = arch.rotate(bd.Axis.X, 90)
        arch = arch.translate((0, 0, cz))
        bd.add(arch)

        # Left & Right lower end tabs plugging into ear cup sockets at X = +/-53.0, Z = cz + 16..22
        for side in (-1, 1):
            with bd.Locations((side * 53.0, 0.0, cz + 18.0)):
                bd.Box(
                    6.8 - 2 * TOL.slip,
                    12.0 - 2 * TOL.slip,
                    11.0,
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                )

    part = band.part
    part.label = "headphone_band"
    part.color = bd.Color(0.20, 0.22, 0.27)
    return part


def build_headphone_parts() -> tuple[bd.Part, bd.Part, bd.Part]:
    """Return (headphone_band, headphone_cup_left, headphone_cup_right)."""
    band = build_headphone_band()

    cup_l = _build_left_cup()
    cup_l.label = "headphone_cup_left"
    cup_l.color = bd.Color(0.26, 0.34, 0.45)  # Cool slate-blue studio ear cups

    cup_r = bd.Part() + bd.mirror(cup_l, bd.Plane.YZ)
    cup_r.label = "headphone_cup_right"
    cup_r.color = bd.Color(0.26, 0.34, 0.45)

    return band, cup_l, cup_r
