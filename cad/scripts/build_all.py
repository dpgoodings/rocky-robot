"""CLI entrypoint to build and verify all Marvin robot STL and 3MF files."""

from __future__ import annotations

import json
from pathlib import Path

from marvin_cad.export import export_all


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    out_dir = root / "output"
    report = export_all(out_dir)

    print(f"Exported {len(report)} parts to {out_dir}")
    print(f"{'Part':<22} {'Watertight':<11} {'Vol (cm³)':<10} {'Tris':<8} {'Size (mm)'}")
    print("-" * 68)
    total_vol = 0.0
    all_ok = True
    for r in report:
        wt = "YES" if r["watertight"] else "NO"
        if not r["watertight"]:
            all_ok = False
        total_vol += r["volume_cm3"]
        print(f"{r['name']:<22} {wt:<11} {r['volume_cm3']:<10.2f} {r['triangles']:<8} {r['dims_mm']}")
    print("-" * 68)
    print(f"Total plastic volume: {total_vol:.2f} cm³ (~{total_vol * 1.24:.1f} g PLA at 100% infill)")
    print("Combined 3MFs: output/3mf/marvin_assembled.3mf, output/3mf/marvin_print_plate.3mf")
    print("3D Viewer:     output/viewer.html")

    (out_dir / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not all_ok:
        raise SystemExit("ERROR: One or more meshes are not watertight!")


if __name__ == "__main__":
    main()
