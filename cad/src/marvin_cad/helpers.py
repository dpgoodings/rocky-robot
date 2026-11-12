"""Reusable CAD building blocks: 9g servo pockets, horn recesses, bosses, and shells."""

from __future__ import annotations

import build123d as bd
from build123d import Align, Mode

from .params import SERVO, TOL


def make_ellipsoid(rx: float, ry: float, rz: float) -> bd.Part:
    """Create a smooth solid ellipsoid centered at the origin."""
    with bd.BuildPart() as ell:
        bd.Sphere(1.0)
        bd.scale(by=(rx, ry, rz))
    return ell.part


def servo_9g_cavity(
    tol: float = TOL.slip,
    wire_slot: bool = True,
    pilot_holes: bool = True,
    shaft_clearance_d: float = 13.0,
) -> bd.Part:
    """Return a negative solid for an SG90/MG90S 9g micro servo.

    Local frame:
    - Output shaft axis is along +Z at (X=0, Y=0).
    - Underside of the mounting flanges sits at Z=0.
      * Body extends from Z = -16.0 to Z = +6.5.
      * Flanges extend from Z = 0.0 to Z = +2.6 (+ tol).
      * Output gear boss extends to Z = +11.0.
    - Long axis of servo is along X, with main body centered at X = -5.6.
    """
    cx = -SERVO.shaft_offset_from_center
    bx = SERVO.body_len + 2 * tol
    by = SERVO.body_wid + 2 * tol
    bz = SERVO.body_h + 2 * tol

    with bd.BuildPart() as cav:
        # Main rectangular body
        with bd.Locations((cx, 0, -SERVO.flange_z_from_bottom + bz / 2 - tol)):
            bd.Box(bx, by, bz, align=(Align.CENTER, Align.CENTER, Align.CENTER))

        # Mounting flange clearance
        fl_x = SERVO.flange_len + 2 * tol
        fl_z = SERVO.flange_thick + 0.6
        with bd.Locations((cx, 0, fl_z / 2 - 0.1)):
            bd.Box(fl_x, by, fl_z, align=(Align.CENTER, Align.CENTER, Align.CENTER))

        # Circular gear boss clearance above body
        with bd.Locations((0, 0, SERVO.body_h - SERVO.flange_z_from_bottom)):
            bd.Cylinder(
                radius=shaft_clearance_d / 2,
                height=SERVO.boss_h + 2.0,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )

        # Wire exit slot out the far (-X) end near bottom of servo
        if wire_slot:
            with bd.Locations((cx - bx / 2 - 3.0, 0, -11.5)):
                bd.Box(9.0, 6.0, 9.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))

        # M2 pilot holes at mounting ears (extending downward into mount shelf)
        if pilot_holes:
            for sign in (-1, 1):
                hx = cx + sign * (SERVO.hole_spacing / 2)
                with bd.Locations((hx, 0, -10.0)):
                    bd.Cylinder(
                        radius=SERVO.hole_pilot_d / 2,
                        height=14.0,
                        align=(Align.CENTER, Align.CENTER, Align.MIN),
                    )

    return cav.part


def servo_horn_recess(
    arm_angle_deg: float = 0.0,
    depth: float = 2.4,
    through_screw: bool = True,
    tol: float = TOL.tight,
) -> bd.Part:
    """Return a negative solid for a standard single-arm SG90/MG90S horn.

    Local frame:
    - Mating face (where the horn enters) is at Z=0, recessed into +Z.
    - Center of horn hub is at (0, 0).
    - Horn arm extends at `arm_angle_deg` around Z.
    """
    hub_r = SERVO.horn_hub_d / 2 + tol
    tip_r = (SERVO.horn_arm_w_tip / 2) + tol
    base_r = (SERVO.horn_arm_w_base / 2) + tol
    arm_len = SERVO.horn_arm_len - tip_r + tol

    with bd.BuildPart() as horn:
        with bd.BuildSketch() as sk:
            bd.Circle(hub_r)
            with bd.Locations((arm_len, 0)):
                bd.Circle(tip_r)
            with bd.BuildLine():
                bd.Polyline(
                    (0, base_r),
                    (arm_len, tip_r),
                    (arm_len, -tip_r),
                    (0, -base_r),
                    close=True,
                )
            bd.make_face()
        arm_solid = bd.extrude(sk.sketch, amount=depth, mode=Mode.PRIVATE)
        if arm_angle_deg != 0.0:
            arm_solid = arm_solid.rotate(bd.Axis.Z, arm_angle_deg)
        bd.add(arm_solid)

        if through_screw:
            # Center M2 screw clearance hole going all the way through wall
            bd.Cylinder(
                radius=SERVO.horn_screw_d / 2,
                height=depth + 10.0,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )
            # Counterbore on outer side for screw head
            with bd.Locations((0, 0, depth)):
                bd.Cylinder(
                    radius=2.3,
                    height=6.0,
                    align=(Align.CENTER, Align.CENTER, Align.MIN),
                )

    return horn.part


def m3_boss(
    height: float,
    outer_d: float = 7.2,
    pilot: bool = True,
    hole_depth: float | None = None,
) -> tuple[bd.Part, bd.Part]:
    """Create a cylindrical M3 screw boss and matching hole cutter.

    Returns (solid_boss, hole_cut) aligned from Z=0 to Z=height.
    """
    hole_d = 2.65 if pilot else 3.35
    hd = hole_depth if hole_depth is not None else height - 1.0
    with bd.BuildPart() as b_p:
        bd.Cylinder(radius=outer_d / 2, height=height, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with bd.BuildPart() as h_p:
        bd.Cylinder(radius=hole_d / 2, height=max(hd, 2.0), align=(Align.CENTER, Align.CENTER, Align.MIN))
    return b_p.part, h_p.part


def standoff(
    height: float,
    outer_d: float = 5.4,
    hole_d: float = 2.2,
) -> tuple[bd.Part, bd.Part]:
    """Create a PCB mounting standoff and matching pilot hole cutter (Z=0..height)."""
    with bd.BuildPart() as s_p:
        bd.Cylinder(radius=outer_d / 2, height=height, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with bd.BuildPart() as h_p:
        bd.Cylinder(radius=hole_d / 2, height=height - 0.5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return s_p.part, h_p.part
