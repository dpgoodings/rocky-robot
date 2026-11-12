"""Build, validate, and export Marvin robot parts to STL, 3MF, and an interactive HTML viewer."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

import build123d as bd
import trimesh

from .arms import build_arm_parts
from .backpack import build_backpack_parts
from .head import build_head_parts
from .headphones import build_headphone_parts
from .internals import build_internal_items
from .legs import build_biped_parts
from .neck import build_neck_yoke
from .torso import build_torso_parts


@dataclass
class PartItem:
    name: str
    part: bd.Part
    color_hex: str
    print_rot: tuple[float, float, float]  # Euler XYZ deg for optimal print bed orientation
    description: str


def build_all_parts() -> list[PartItem]:
    """Construct all 20 printable parts of the Bipedal Marvin desk robot with faux headphones."""
    pelvis, leg_l, leg_r, foot_l, foot_r = build_biped_parts()
    bp_shell, bp_lid = build_backpack_parts()
    torso_front, torso_back = build_torso_parts()
    neck_yoke = build_neck_yoke()
    yoke_l, yoke_r, arm_l, arm_r = build_arm_parts()
    head_front, head_back, head_visor = build_head_parts()
    hp_band, hp_cup_l, hp_cup_r = build_headphone_parts()

    return [
        PartItem("foot_left", foot_l, "#3b404a", (0, 0, 0), "Left waddle boot with double-shear ankle fork"),
        PartItem("foot_right", foot_r, "#3b404a", (0, 0, 0), "Right waddle boot with double-shear ankle fork"),
        PartItem("leg_left", leg_l, "#d6d9de", (0, -90, 0), "Left leg housing Ankle Roll servo & Hip horn recess"),
        PartItem("leg_right", leg_r, "#d6d9de", (0, 90, 0), "Right leg housing Ankle Roll servo & Hip horn recess"),
        PartItem("pelvis", pelvis, "#474c57", (0, 0, 0), "Bipedal pelvis housing 2x Hip Pitch 9g servos"),
        PartItem("torso_front", torso_front, "#dce0e6", (0, 0, 0), "Front capsule torso with 28mm speaker grille"),
        PartItem("torso_back", torso_back, "#cdd2d9", (0, 0, 0), "Rear torso with head-pan & 2x shoulder servo cradles"),
        PartItem("backpack_shell", bp_shell, "#474c57", (-90, 0, 0), "Rear backpack for PCA9685, CyberBrick switch & power"),
        PartItem("backpack_lid", bp_lid, "#2b303a", (90, 0, 0), "Backpack lid with CyberBrick 7.4V battery cradle & 5V UBEC mount"),
        PartItem("neck_yoke", neck_yoke, "#474c57", (0, 0, 0), "2-axis pan/tilt neck yoke with wire arch"),
        PartItem("shoulder_yoke_left", yoke_l, "#474c57", (0, -90, 0), "Left shoulder clevis holding arm-raise servo"),
        PartItem("shoulder_yoke_right", yoke_r, "#474c57", (0, 90, 0), "Right shoulder clevis holding arm-raise servo"),
        PartItem("arm_left", arm_l, "#d6d9de", (0, 90, 0), "Left articulated Marvin arm & rounded mitt"),
        PartItem("arm_right", arm_r, "#d6d9de", (0, -90, 0), "Right articulated Marvin arm & rounded mitt"),
        PartItem("head_front", head_front, "#dce0e6", (90, 0, 0), "Front head dome with 2.0\" LCD, vertical ESP32-S3 CAM rails, cam & mic"),
        PartItem("head_back", head_back, "#cdd2d9", (-90, 0, 0), "Rear head dome with double-shear tilt pivot brackets"),
        PartItem("head_visor_bezel", head_visor, "#1f2129", (90, 0, 0), "Dark front visor bezel with Marvin downcast brow"),
        PartItem("headphone_band", hp_band, "#2d323d", (90, 0, 0), "Over-the-head faux studio headphone arch"),
        PartItem("headphone_cup_left", hp_cup_l, "#415672", (0, -90, 0), "Left faux headphone ear cup"),
        PartItem("headphone_cup_right", hp_cup_r, "#415672", (0, 90, 0), "Right faux headphone ear cup"),
    ]


def _orient_for_print(part: bd.Part, rot_xyz: tuple[float, float, float]) -> bd.Part:
    """Rotate part into print orientation and drop its bottom flush onto Z=0."""
    p = bd.Part() + part
    rx, ry, rz = rot_xyz
    if rx:
        p = p.rotate(bd.Axis.X, rx)
    if ry:
        p = p.rotate(bd.Axis.Y, ry)
    if rz:
        p = p.rotate(bd.Axis.Z, rz)
    bb = p.bounding_box()
    cx = (bb.min.X + bb.max.X) / 2
    cy = (bb.min.Y + bb.max.Y) / 2
    return bd.Part() + p.translate((-cx, -cy, -bb.min.Z))


def _layout_print_plate(items: list[PartItem], gap: float = 10.0, max_row_w: float = 210.0) -> list[tuple[PartItem, bd.Part]]:
    """Arrange all oriented parts neatly on flat Z=0 rows."""
    oriented: list[tuple[PartItem, bd.Part, float, float]] = []
    for item in items:
        op = _orient_for_print(item.part, item.print_rot)
        bb = op.bounding_box()
        oriented.append((item, op, bb.size.X, bb.size.Y))

    oriented.sort(key=lambda t: t[3], reverse=True)
    placed: list[tuple[PartItem, bd.Part]] = []
    cursor_x = 0.0
    cursor_y = 0.0
    row_h = 0.0

    raw_placed: list[tuple[PartItem, bd.Part, float, float]] = []
    for item, op, sx, sy in oriented:
        if cursor_x > 0 and cursor_x + sx > max_row_w:
            cursor_x = 0.0
            cursor_y += row_h + gap
            row_h = 0.0
        px = cursor_x + sx / 2
        py = cursor_y + sy / 2
        raw_placed.append((item, op, px, py))
        cursor_x += sx + gap
        row_h = max(row_h, sy)

    all_x = [px for _, _, px, _ in raw_placed]
    all_y = [py for _, _, _, py in raw_placed]
    mid_x = (min(all_x) + max(all_x)) / 2
    mid_y = (min(all_y) + max(all_y)) / 2
    for item, op, px, py in raw_placed:
        placed_part = bd.Part() + op.translate((px - mid_x, py - mid_y, 0.0))
        placed_part.color = item.part.color
        placed_part.label = item.name
        placed.append((item, placed_part))

    return placed


def _write_viewer_html(viewer_path: Path, stl_entries: list[dict]) -> None:
    """Write a self-contained Three.js 3D assembly, exploded-view, and waddle previewer."""
    data_json = json.dumps(stl_entries)
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Marvin Bipedal Waddle Robot — 3D CAD Inspector</title>
<style>
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f131a; color: #e6ebf2; overflow: hidden; }}
  #hud {{ position: fixed; top: 14px; left: 14px; width: 262px; background: rgba(18, 23, 33, 0.88); backdrop-filter: blur(10px); border: 1px solid #2b3445; border-radius: 12px; padding: 12px 14px; max-height: calc(100vh - 28px); overflow-y: auto; transition: width 0.15s ease; }}
  #hud.collapsed {{ width: auto; }}
  #hud.collapsed #hudBody {{ display: none; }}
  .hdr {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
  h1 {{ font-size: 14px; margin: 0; font-weight: 600; }}
  .sub {{ font-size: 11px; color: #9aa6b8; margin: 4px 0 10px 0; }}
  .ctrl {{ margin-bottom: 10px; }}
  label {{ font-size: 11px; display: flex; justify-content: space-between; margin-bottom: 4px; color: #c4cdd9; }}
  input[type=range] {{ width: 100%; accent-color: #60a5fa; }}
  .btns {{ display: flex; gap: 5px; margin-bottom: 10px; flex-wrap: wrap; }}
  button {{ background: #1e2636; border: 1px solid #334155; color: #e2e8f0; padding: 5px 8px; border-radius: 7px; font-size: 11px; cursor: pointer; }}
  button:hover {{ background: #29344a; }}
  button.active {{ background: #2563eb; border-color: #3b82f6; color: #fff; }}
  .part-row {{ display: flex; align-items: center; gap: 7px; padding: 3px 0; font-size: 11px; border-bottom: 1px solid rgba(255,255,255,0.05); }}
  .swatch {{ width: 9px; height: 9px; border-radius: 3px; flex-shrink: 0; border: 1px solid rgba(255,255,255,0.2); }}
  .part-name {{ flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .part-dim {{ color: #8b97ab; font-size: 10px; }}
</style>
</head>
<body>
<div id="hud">
  <div class="hdr">
    <h1>Marvin Bipedal Robot</h1>
    <button id="collapseBtn">Hide</button>
  </div>
  <div id="hudBody">
    <div class="sub">20 shell parts + 18 internal servos/PCBs • ~258 mm</div>
    <div class="ctrl">
      <label><span>Exploded View</span><span id="expVal">0%</span></label>
      <input id="explode" type="range" min="0" max="100" value="0" />
    </div>
    <div class="btns">
      <button id="xrayBtn" class="active">X-Ray</button>
      <button id="internalsBtn" class="active">Internals</button>
      <button id="waddleBtn">Waddle</button>
      <button id="trackBtn">Face Track</button>
      <button id="resetCam">Reset</button>
      <button id="toggleWire">Wire</button>
      <button id="showAll">All</button>
    </div>
    <div id="partsList"></div>
  </div>
</div>
<script type="importmap">
{{
  "imports": {{
    "three": "https://unpkg.com/three@0.170.0/build/three.module.js",
    "three/addons/": "https://unpkg.com/three@0.170.0/examples/jsm/"
  }}
}}
</script>
<script type="module">
import * as THREE from 'three';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';
import {{ STLLoader }} from 'three/addons/loaders/STLLoader.js';

const PARTS = {data_json};
const EXPLODE_VECTORS = {{
  foot_left: [24, 0, -32],
  foot_right: [-24, 0, -32],
  leg_left: [34, 0, -10],
  leg_right: [-34, 0, -10],
  pelvis: [0, 0, -16],
  torso_front: [0, 34, 0],
  torso_back: [0, -34, 0],
  backpack_shell: [0, -72, 0],
  backpack_lid: [0, -108, 0],
  neck_yoke: [0, 0, 26],
  shoulder_yoke_left: [28, 0, 0],
  shoulder_yoke_right: [-28, 0, 0],
  arm_left: [58, 8, 0],
  arm_right: [-58, 8, 0],
  head_front: [0, 36, 42],
  head_back: [0, -36, 42],
  head_visor_bezel: [0, 68, 42],
  headphone_band: [0, 0, 88],
  headphone_cup_left: [72, 0, 42],
  headphone_cup_right: [-72, 0, 42],
}};

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0f131a);

const camera = new THREE.PerspectiveCamera(40, innerWidth / innerHeight, 1, 2000);
camera.position.set(220, -310, 225);
camera.up.set(0, 0, 1);

const renderer = new THREE.WebGLRenderer({{ antialias: true }});
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
document.body.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(-28, 0, 128);
controls.enableDamping = true;
controls.update();

scene.add(new THREE.AmbientLight(0xffffff, 0.65));
const key = new THREE.DirectionalLight(0xffffff, 1.35);
key.position.set(180, 220, 320);
scene.add(key);
const fill = new THREE.DirectionalLight(0x93c5fd, 0.55);
fill.position.set(-200, -180, 140);
scene.add(fill);

const grid = new THREE.GridHelper(300, 30, 0x334155, 0x1e293b);
grid.rotation.x = Math.PI / 2;
scene.add(grid);

const loader = new STLLoader();
const meshes = {{}};
const listEl = document.getElementById('partsList');

const waddleRoot = new THREE.Group();
scene.add(waddleRoot);
const upperBodyRig = new THREE.Group();
upperBodyRig.position.set(0, 0, 19);
waddleRoot.add(upperBodyRig);

const neckYawRig = new THREE.Group();
neckYawRig.position.set(0, 0, 152 - 19);
upperBodyRig.add(neckYawRig);

const headRig = new THREE.Group();
headRig.position.set(0, 0, 179 - 152);
neckYawRig.add(headRig);

const armLRig = new THREE.Group();
armLRig.position.set(58, 3, 132 - 19);
upperBodyRig.add(armLRig);

const armRRig = new THREE.Group();
armRRig.position.set(-58, 3, 132 - 19);
upperBodyRig.add(armRRig);

const UPPER_PARTS = new Set(['pelvis', 'torso_front', 'torso_back', 'backpack_shell', 'backpack_lid', 'shoulder_yoke_left', 'shoulder_yoke_right', 'leg_left', 'leg_right']);
const HEAD_PARTS = new Set(['head_front', 'head_back', 'head_visor_bezel', 'headphone_band', 'headphone_cup_left', 'headphone_cup_right']);

const shellMeshes = [];
const internalMeshes = [];

for (const item of PARTS) {{
  const bin = Uint8Array.from(atob(item.stl_b64), c => c.charCodeAt(0));
  const geom = loader.parse(bin.buffer);
  geom.computeVertexNormals();
  const isInternal = Boolean(item.is_internal);
  const mat = new THREE.MeshStandardMaterial({{
    color: new THREE.Color(item.color),
    roughness: isInternal ? 0.35 : 0.42,
    metalness: isInternal ? 0.22 : 0.12,
    transparent: !isInternal,
    opacity: isInternal ? 1.0 : 0.36,
    depthWrite: isInternal,
  }});
  const mesh = new THREE.Mesh(geom, mat);
  mesh.userData = {{ name: item.name, explode: EXPLODE_VECTORS[item.name] || [0,0,0], isInternal }};
  if (isInternal) internalMeshes.push(mesh);
  else shellMeshes.push(mesh);

  const rig = item.rig_group || (
    HEAD_PARTS.has(item.name) ? 'head' :
    item.name === 'neck_yoke' ? 'neck' :
    item.name === 'arm_left' ? 'arm_l' :
    item.name === 'arm_right' ? 'arm_r' :
    UPPER_PARTS.has(item.name) ? 'upper' : 'world'
  );

  if (rig === 'head') {{
    geom.translate(0, 0, -179);
    headRig.add(mesh);
  }} else if (rig === 'neck') {{
    geom.translate(0, 0, -152);
    neckYawRig.add(mesh);
  }} else if (rig === 'arm_l') {{
    geom.translate(-58, -3, -132);
    armLRig.add(mesh);
  }} else if (rig === 'arm_r') {{
    geom.translate(58, -3, -132);
    armRRig.add(mesh);
  }} else if (rig === 'upper') {{
    geom.translate(0, 0, -19);
    upperBodyRig.add(mesh);
  }} else {{
    waddleRoot.add(mesh);
  }}
  meshes[item.name] = mesh;

  const row = document.createElement('div');
  row.className = 'part-row';
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = true;
  cb.dataset.internal = isInternal ? '1' : '0';
  cb.onchange = () => {{ mesh.visible = cb.checked; }};
  const sw = document.createElement('div');
  sw.className = 'swatch';
  sw.style.background = item.color;
  const nm = document.createElement('div');
  nm.className = 'part-name';
  nm.title = item.description;
  nm.textContent = item.name;
  const dm = document.createElement('div');
  dm.className = 'part-dim';
  dm.textContent = item.dims;
  row.append(cb, sw, nm, dm);
  listEl.appendChild(row);
}}

const explodeSlider = document.getElementById('explode');
const expVal = document.getElementById('expVal');
explodeSlider.oninput = () => {{
  const t = Number(explodeSlider.value) / 100;
  expVal.textContent = `${{explodeSlider.value}}%`;
  for (const m of Object.values(meshes)) {{
    const [ex, ey, ez] = m.userData.explode;
    m.position.set(ex * t, ey * t, ez * t);
  }}
}};

let waddling = false;
let tracking = false;
let waddleT = 0;
let trackT = 0;
const waddleBtn = document.getElementById('waddleBtn');
const trackBtn = document.getElementById('trackBtn');
waddleBtn.onclick = () => {{
  waddling = !waddling;
  waddleBtn.classList.toggle('active', waddling);
  if (!waddling) {{
    upperBodyRig.rotation.set(0, 0, 0);
    upperBodyRig.position.set(0, 0, 19);
    if (!tracking) {{
      neckYawRig.rotation.set(0, 0, 0);
      headRig.rotation.set(0, 0, 0);
    }}
    armLRig.rotation.set(0, 0, 0);
    armRRig.rotation.set(0, 0, 0);
    meshes.foot_left.position.y = meshes.foot_left.userData.explode[1] * (Number(explodeSlider.value)/100);
    meshes.foot_right.position.y = meshes.foot_right.userData.explode[1] * (Number(explodeSlider.value)/100);
  }}
}};
trackBtn.onclick = () => {{
  tracking = !tracking;
  trackBtn.classList.toggle('active', tracking);
  if (!tracking) {{
    neckYawRig.rotation.set(0, 0, 0);
    headRig.rotation.set(0, 0, 0);
  }}
}};

let xray = true;
const xrayBtn = document.getElementById('xrayBtn');
xrayBtn.onclick = () => {{
  xray = !xray;
  xrayBtn.classList.toggle('active', xray);
  for (const m of shellMeshes) {{
    m.material.transparent = xray;
    m.material.opacity = xray ? 0.36 : 1.0;
    m.material.depthWrite = !xray;
    m.material.needsUpdate = true;
  }}
}};

let showInternals = true;
const internalsBtn = document.getElementById('internalsBtn');
internalsBtn.onclick = () => {{
  showInternals = !showInternals;
  internalsBtn.classList.toggle('active', showInternals);
  for (const m of internalMeshes) m.visible = showInternals;
  listEl.querySelectorAll('input[data-internal="1"]').forEach(c => c.checked = showInternals);
}};

document.getElementById('collapseBtn').onclick = () => {{
  const hud = document.getElementById('hud');
  hud.classList.toggle('collapsed');
  document.getElementById('collapseBtn').textContent = hud.classList.contains('collapsed') ? 'Parts' : 'Hide';
}};

document.getElementById('resetCam').onclick = () => {{
  camera.position.set(220, -310, 225);
  controls.target.set(-28, 0, 128);
  controls.update();
}};

let wire = false;
document.getElementById('toggleWire').onclick = () => {{
  wire = !wire;
  for (const m of Object.values(meshes)) m.material.wireframe = wire;
}};

document.getElementById('showAll').onclick = () => {{
  for (const m of Object.values(meshes)) m.visible = true;
  listEl.querySelectorAll('input[type=checkbox]').forEach(c => c.checked = true);
}};

window.addEventListener('resize', () => {{
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
}});

(function animate() {{
  requestAnimationFrame(animate);
  if (waddling) {{
    waddleT += 0.055;
    const roll = Math.sin(waddleT) * 0.16;
    const step = Math.cos(waddleT);
    upperBodyRig.rotation.y = roll;
    upperBodyRig.rotation.z = step * 0.08;
    if (!tracking) {{
      headRig.rotation.y = -roll * 0.45;
      headRig.rotation.x = 0.06 + Math.sin(waddleT * 2) * 0.04;
    }}
    armLRig.rotation.x = -step * 0.25;
    armRRig.rotation.x = step * 0.25;
    armLRig.rotation.y = Math.max(0, -roll) * 0.6;
    armRRig.rotation.y = -Math.max(0, roll) * 0.6;
    const exp = Number(explodeSlider.value) / 100;
    meshes.foot_left.position.y = meshes.foot_left.userData.explode[1] * exp + Math.max(0, -Math.sin(waddleT)) * 6;
    meshes.foot_right.position.y = meshes.foot_right.userData.explode[1] * exp + Math.max(0, Math.sin(waddleT)) * 6;
  }}
  if (tracking) {{
    trackT += 0.032;
    neckYawRig.rotation.z = Math.sin(trackT) * 1.15;             // ~+/-66 deg side-to-side pan
    headRig.rotation.x = 0.10 + Math.sin(trackT * 1.6) * 0.48;   // ~-17 deg down to +33 deg up tilt
  }}
  controls.update();
  renderer.render(scene, camera);
}})();
</script>
</body>
</html>
"""
    viewer_path.write_text(html, encoding="utf-8")


def export_all(output_dir: Path) -> list[dict]:
    """Generate all STL, 3MF, print-plate 3MF, and viewer HTML files."""
    stl_dir = output_dir / "stl"
    mf3_dir = output_dir / "3mf"
    stl_dir.mkdir(parents=True, exist_ok=True)
    mf3_dir.mkdir(parents=True, exist_ok=True)

    items = build_all_parts()
    report: list[dict] = []
    viewer_entries: list[dict] = []

    assembled_mesher = bd.Mesher(unit=bd.Unit.MM)

    for item in items:
        stl_path = stl_dir / f"{item.name}.stl"
        mf3_path = mf3_dir / f"{item.name}.3mf"

        # Export STL with fine tolerance for smooth curved surfaces
        bd.export_stl(item.part, str(stl_path), tolerance=0.015, angular_tolerance=0.05)

        # Strip tiny 1-face degenerate BRepMesh pole slivers if main solid is watertight
        mesh = trimesh.load(str(stl_path), force="mesh")
        if not mesh.is_watertight:
            real_bodies = [b for b in mesh.split(only_watertight=False) if len(b.faces) >= 10]
            if real_bodies and all(b.is_watertight for b in real_bodies):
                mesh = trimesh.util.concatenate(real_bodies)
                mesh.export(str(stl_path))

        # Export individual 3MF (oriented flat for printing)
        print_part = _orient_for_print(item.part, item.print_rot)
        print_part.color = item.part.color
        print_part.label = item.name
        single_mesher = bd.Mesher(unit=bd.Unit.MM)
        single_mesher.add_shape(print_part, linear_deflection=0.015, angular_deflection=0.05, part_number=item.name)
        single_mesher.write(str(mf3_path))

        # Add assembled-position part to full assembly 3MF
        assembled_mesher.add_shape(item.part, linear_deflection=0.015, angular_deflection=0.05, part_number=item.name)

        bb = item.part.bounding_box()
        dims_str = f"{bb.size.X:.0f}×{bb.size.Y:.0f}×{bb.size.Z:.0f}"
        report.append({
            "name": item.name,
            "watertight": bool(mesh.is_watertight),
            "volume_cm3": round(float(mesh.volume) / 1000.0, 2),
            "triangles": int(len(mesh.faces)),
            "dims_mm": dims_str,
            "stl": str(stl_path),
            "3mf": str(mf3_path),
        })

        viewer_entries.append({
            "name": item.name,
            "color": item.color_hex,
            "description": item.description,
            "dims": f"{dims_str} mm",
            "stl_b64": base64.b64encode(stl_path.read_bytes()).decode("ascii"),
        })

    assembled_path = mf3_dir / "marvin_assembled.3mf"
    assembled_mesher.write(str(assembled_path))

    # Also write marvin_with_internals.3mf and embed internal electronics/servos into viewer
    full_mesher = bd.Mesher(unit=bd.Unit.MM)
    for item in items:
        full_mesher.add_shape(item.part, linear_deflection=0.015, angular_deflection=0.05, part_number=item.name)

    internals_tmp_dir = output_dir / "_internals_stl"
    internals_tmp_dir.mkdir(parents=True, exist_ok=True)
    for int_item in build_internal_items():
        full_mesher.add_shape(int_item.part, linear_deflection=0.03, angular_deflection=0.1, part_number=int_item.name)
        int_stl = internals_tmp_dir / f"{int_item.name}.stl"
        bd.export_stl(int_item.part, str(int_stl), tolerance=0.03, angular_tolerance=0.1)
        bb = int_item.part.bounding_box()
        dims_str = f"{bb.size.X:.0f}×{bb.size.Y:.0f}×{bb.size.Z:.0f}"
        viewer_entries.append({
            "name": int_item.name,
            "color": int_item.color_hex,
            "description": int_item.description,
            "dims": f"{dims_str} mm",
            "is_internal": True,
            "rig_group": int_item.rig_group,
            "stl_b64": base64.b64encode(int_stl.read_bytes()).decode("ascii"),
        })
    full_mesher.write(str(mf3_dir / "marvin_with_internals.3mf"))

    plate_mesher = bd.Mesher(unit=bd.Unit.MM)
    for item, placed_part in _layout_print_plate(items):
        plate_mesher.add_shape(placed_part, linear_deflection=0.015, angular_deflection=0.05, part_number=item.name)
    plate_path = mf3_dir / "marvin_print_plate.3mf"
    plate_mesher.write(str(plate_path))

    _write_viewer_html(output_dir / "viewer.html", viewer_entries)
    return report
