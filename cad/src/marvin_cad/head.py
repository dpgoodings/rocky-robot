"""Marvin oversized dome head (front/back clamshell) + front visor bezel."""

from __future__ import annotations

import build123d as bd
from build123d import Align, Mode

from .helpers import m3_boss, make_ellipsoid, servo_horn_recess
from .params import ELEC, GEO, TOL


def _head_outer_solid() -> bd.Part:
    """Build the unsplit outer Marvin head dome + side ear discs at world Z=head_center_z."""
    rx = GEO.head_w / 2   # 48.0
    ry = GEO.head_d / 2   # 45.0
    rz = GEO.head_h / 2   # 44.0

    with bd.BuildPart() as h:
        bd.add(make_ellipsoid(rx, ry, rz))

        # Subtle Marvin side ear discs on +/-X
        for side in (-1, 1):
            with bd.Locations((side * (rx - 2.0), 0.0, 0.0)):
                bd.Cylinder(
                    radius=12.5,
                    height=5.0,
                    rotation=(0, 90, 0),
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                )

    return bd.Part() + h.part.translate((0, 0, GEO.head_center_z))


def _head_cavity_solid() -> bd.Part:
    """Build the hollow inner cavity of the head dome."""
    wall = 2.6
    rx = GEO.head_w / 2 - wall
    ry = GEO.head_d / 2 - wall
    rz = GEO.head_h / 2 - wall
    return bd.Part() + make_ellipsoid(rx, ry, rz).translate((0, 0, GEO.head_center_z))


def _visor_bay_cutter(extra_w: float = 0.0, extra_h: float = 0.0) -> bd.Part:
    """Rounded-rectangle visor profile extruded along Y for carving/framing the face."""
    w = 64.0 + extra_w
    h = 54.0 + extra_h
    with bd.BuildPart() as vb:
        with bd.BuildSketch() as sk:
            bd.RectangleRounded(w, h, radius=12.0)
        sol = bd.extrude(sk.sketch, amount=35.0, mode=Mode.PRIVATE)
        sol = sol.rotate(bd.Axis.X, -90)  # local +Z -> +Y
        bd.add(sol)
    return bd.Part() + vb.part.translate((0, GEO.face_plane_y, GEO.head_center_z - 2.0))


def build_head_parts() -> tuple[bd.Part, bd.Part, bd.Part]:
    """Build (head_front, head_back, head_visor_bezel)."""
    outer = _head_outer_solid()
    inner = _head_cavity_solid()

    # Neck clearance cutout at bottom of head (allows wide face-tracking pitch -32..+42 deg)
    neck_cut = bd.Part() + bd.scale(bd.Cylinder(1.0, 28.0), (22.5, 26.5, 1.0)).translate(
        (0, -1.5, GEO.head_center_z - 40.0)
    )

    front_half = bd.Box(200, 200, 200, align=(Align.CENTER, Align.MIN, Align.CENTER)).translate(
        (0, 0, GEO.head_center_z)
    )
    back_half = bd.Box(200, 200, 200, align=(Align.CENTER, Align.MAX, Align.CENTER)).translate(
        (0, 0, GEO.head_center_z)
    )

    # 4x M3 split screw coordinates (outside visor & clear of Freenove S3 rails & pivot brackets)
    split_screws = [
        (-33.0, GEO.head_center_z + 24.0),
        (33.0, GEO.head_center_z + 24.0),
        (-35.0, GEO.head_center_z - 20.0),
        (35.0, GEO.head_center_z - 20.0),
    ]

    lcd_z = GEO.head_center_z - 3.5     # 154.5 mm
    cam_z = GEO.head_center_z + 19.5    # 177.5 mm
    mic_z = GEO.head_center_z - 25.5    # 132.5 mm

    visor_cut = _visor_bay_cutter(extra_w=0.0, extra_h=0.0)
    shell_f = (outer - inner) & front_half
    shell_b = (outer - inner) & back_half
    bulk_slab = bd.Box(74.0, 3.8, 64.0, align=(Align.CENTER, Align.CENTER, Align.CENTER)).translate(
        (0, GEO.face_plane_y - 1.9, GEO.head_center_z - 2.0)
    ) & outer
    rear_clear = bd.Box(70.0, 24.0, 62.0, align=(Align.CENTER, Align.MAX, Align.CENTER)).translate(
        (0, GEO.face_plane_y - 3.8, GEO.head_center_z - 2.0)
    ) & inner

    # Vertical slide-in rail brackets in head_front for compact ESP32-S3 CAM (58x28.5 mm)
    # Board sits upright at Y = 14.5 (facing +Y), X = +/-14.25, Z = 178..236 (camera aligns to cam_z)
    s3_y = 14.5
    s3_rail_x = ELEC.s3_cam_wid / 2 + 1.5  # +/-15.75 mm
    s3_slot_x = ELEC.s3_cam_wid / 2 - 0.3  # +/-13.95 mm
    s3_rails = []
    s3_slots = []
    for side in (-1, 1):
        # Upper mounting arms (above LCD: Z = 221 .. 233) bridging from front bulkhead (Y=23.5) back to Y=12.5
        arm_up = bd.Box(4.8, 11.5, 11.0, align=(Align.CENTER, Align.CENTER, Align.CENTER)).translate(
            (side * s3_rail_x, 18.0, GEO.head_center_z + 22.0)
        ) & outer
        slot_up = bd.Box(3.0, ELEC.s3_cam_thick + 0.4, 14.0, align=(Align.CENTER, Align.CENTER, Align.CENTER)).translate(
            (side * s3_slot_x, s3_y, GEO.head_center_z + 22.0)
        )
        # Lower support arms (below LCD: Z = 179 .. 184)
        arm_lo = bd.Box(4.8, 11.5, 6.0, align=(Align.CENTER, Align.CENTER, Align.CENTER)).translate(
            (side * s3_rail_x, 18.0, GEO.head_center_z - 23.0)
        ) & outer
        slot_lo = bd.Box(3.0, ELEC.s3_cam_thick + 0.4, 8.0, align=(Align.CENTER, Align.CENTER, Align.CENTER)).translate(
            (side * s3_slot_x, s3_y, GEO.head_center_z - 23.0)
        )
        s3_rails.extend([arm_up, arm_lo])
        s3_slots.extend([slot_up, slot_lo])

    # --- HEAD FRONT ---
    with bd.BuildPart() as hf:
        bd.add(shell_f)
        bd.add(bulk_slab)
        bd.add(visor_cut, mode=Mode.SUBTRACT)
        bd.add(rear_clear, mode=Mode.SUBTRACT)
        for r in s3_rails:
            bd.add(r)
        for sl in s3_slots:
            bd.add(sl, mode=Mode.SUBTRACT)

        # 2.0" ST7789 IPS LCD active window + rear PCB pocket
        with bd.Locations((0, GEO.face_plane_y, lcd_z)):
            bd.Box(
                ELEC.lcd_active_w,
                16.0,
                ELEC.lcd_active_h,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )
        # Rear PCB pocket (Y = 22.5 .. 26.2)
        with bd.Locations((0, GEO.face_plane_y - 3.2, lcd_z)):
            bd.Box(
                ELEC.lcd_pcb_w,
                3.6,
                ELEC.lcd_pcb_h,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )

        # 4x small M2 screw bosses behind top/bottom edges of LCD PCB
        for sx in (-1, 1):
            for sz in (-1, 1):
                with bd.Locations((sx * 22.0, GEO.face_plane_y - 5.0, lcd_z + sz * 20.0)):
                    bd.Cylinder(
                        radius=2.3,
                        height=4.0,
                        rotation=(90, 0, 0),
                        align=(Align.CENTER, Align.CENTER, Align.CENTER),
                    )
                    bd.Cylinder(
                        radius=0.9,
                        height=5.5,
                        rotation=(90, 0, 0),
                        align=(Align.CENTER, Align.CENTER, Align.CENTER),
                        mode=Mode.SUBTRACT,
                    )

        # Camera 8.5 mm lens bore + rear PCB recess
        with bd.Locations((0, GEO.face_plane_y, cam_z)):
            bd.Cylinder(
                radius=ELEC.cam_lens_d / 2,
                height=16.0,
                rotation=(90, 0, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )
        with bd.Locations((0, GEO.face_plane_y - 3.2, cam_z)):
            bd.Box(
                ELEC.cam_pcb_w,
                2.5,
                16.0,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )

        # INMP441 Mic acoustic pinhole + rear PCB seat
        with bd.Locations((0, GEO.face_plane_y, mic_z)):
            bd.Cylinder(
                radius=ELEC.mic_port_d / 2,
                height=16.0,
                rotation=(90, 0, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )
        with bd.Locations((0, GEO.face_plane_y - 3.2, mic_z)):
            bd.Box(
                ELEC.mic_pcb_w,
                2.5,
                ELEC.mic_pcb_h,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )

        # 4x M3 pilot bosses at Y = 0 facing -Y
        for sx, sz in split_screws:
            b_sol, b_hole = m3_boss(11.0, outer_d=7.2, pilot=True, hole_depth=9.5)
            b_sol = b_sol.rotate(bd.Axis.X, -90).translate((sx, 0.0, sz))
            b_hole = b_hole.rotate(bd.Axis.X, -90).translate((sx, -0.2, sz))
            bd.add(b_sol)
            bd.add(b_hole, mode=Mode.SUBTRACT)

        bd.add(neck_cut, mode=Mode.SUBTRACT)

    # --- HEAD BACK ---
    tilt_z = GEO.neck_tilt_z
    pivot_bars = []
    for side in (-1, 1):
        bx = side * 20.5
        bar = (
            bd.Box(6.0, 34.0, 17.0, align=(Align.CENTER, Align.CENTER, Align.CENTER)).translate(
                (bx, -12.0, tilt_z)
            )
            & outer
        )
        pivot_bars.append(bar)

    with bd.BuildPart() as hb:
        bd.add(shell_b)

        # Internal tilt pivot brackets extending from rear wall to (X = +/-17.5, Y = 0, Z = neck_tilt_z)
        for side, bar in zip((-1, 1), pivot_bars):
            bx = side * 20.5
            bd.add(bar)
            # Round pivot boss at Y = 0, Z = tilt_z
            with bd.Locations((bx, 0.0, tilt_z)):
                bd.Cylinder(
                    radius=8.5,
                    height=6.0,
                    rotation=(0, 90, 0),
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                )

        # +X side: servo horn recess facing inward (-X) at X = +17.5
        horn = servo_horn_recess(arm_angle_deg=0.0, depth=2.6, through_screw=True)
        # Rotate so local +Z (recess depth) points +X (into bracket), local +X points -Z
        horn = horn.rotate(bd.Axis.Y, 90)
        horn = horn.translate((17.45, 0.0, tilt_z))
        bd.add(horn, mode=Mode.SUBTRACT)

        # -X side: dummy pivot socket (Ø5.1 mm) at X = -17.5 .. -23.5
        with bd.Locations((-20.5, 0.0, tilt_z)):
            bd.Cylinder(
                radius=2.55,
                height=10.0,
                rotation=(0, 90, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )

        # 4x M3 clearance holes + rear counterbores
        for sx, sz in split_screws:
            b_sol, _ = m3_boss(13.0, outer_d=7.2, pilot=False)
            b_sol = b_sol.rotate(bd.Axis.X, 90).translate((sx, 0.0, sz))
            bd.add(b_sol)
            with bd.Locations((sx, -16.0, sz)):
                bd.Cylinder(
                    radius=1.7,
                    height=32.0,
                    rotation=(90, 0, 0),
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                    mode=Mode.SUBTRACT,
                )
            with bd.Locations((sx, -21.0, sz)):
                bd.Cylinder(
                    radius=3.1,
                    height=22.0,
                    rotation=(90, 0, 0),
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                    mode=Mode.SUBTRACT,
                )

        bd.add(neck_cut, mode=Mode.SUBTRACT)

    # --- VISOR BEZEL ---
    with bd.BuildPart() as vz_plate:
        with bd.BuildSketch() as sk:
            bd.RectangleRounded(64.0 - 2 * TOL.slip, 54.0 - 2 * TOL.slip, radius=11.8)
        bd.extrude(sk.sketch, amount=3.2)
    plate = vz_plate.part.rotate(bd.Axis.X, -90).translate((0, GEO.face_plane_y + 0.15, GEO.head_center_z - 2.0)) & outer

    brows = []
    for side in (-1, 1):
        brow = bd.Box(22.0, 2.2, 3.0, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        brow = brow.rotate(bd.Axis.Y, side * 8.0).translate((side * 12.0, GEO.face_plane_y + 3.0, lcd_z + 17.5))
        brows.append(brow & outer)

    with bd.BuildPart() as vz:
        bd.add(plate)
        for b in brows:
            bd.add(b)

        # Cutout for 2.0" ST7789 IPS LCD screen
        with bd.Locations((0, GEO.face_plane_y + 2.0, lcd_z)):
            bd.Box(
                ELEC.lcd_active_w,
                14.0,
                ELEC.lcd_active_h,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )

        # Cutout for 8.5 mm camera lens
        with bd.Locations((0, GEO.face_plane_y + 2.0, cam_z)):
            bd.Cylinder(
                radius=ELEC.cam_lens_d / 2 + 0.2,
                height=14.0,
                rotation=(90, 0, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )

        # Pinhole for INMP441 mic
        with bd.Locations((0, GEO.face_plane_y + 2.0, mic_z)):
            bd.Cylinder(
                radius=ELEC.mic_port_d / 2,
                height=14.0,
                rotation=(90, 0, 0),
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
                mode=Mode.SUBTRACT,
            )

    front = hf.part
    front.label = "head_front"
    front.color = bd.Color(0.86, 0.87, 0.89)

    back = hb.part
    back.label = "head_back"
    back.color = bd.Color(0.80, 0.82, 0.85)

    bezel = vz.part
    bezel.label = "head_visor_bezel"
    bezel.color = bd.Color(0.12, 0.13, 0.16)  # Glossy dark visor

    return front, back, bezel
