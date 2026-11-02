#!/usr/bin/env python3
"""
add_voice.py — One-Command Character & Voice Pack Installer!

Examples:
  # 1. Add a new RVC voice from a .zip, .pth, or .onnx (local file or URL):
  ./.venv/bin/python add_voice.py \
      --id glados --name "GLaDOS" --title "GLaDOS (Portal)" --emoji "🥔" \
      --piper-voice en_US-lessac-medium \
      --model ~/Downloads/glados_rvc.zip \
      --prompt "You are GLaDOS from Portal. Reply in 2 passive-aggressive scientific sentences."

  # 2. Add an instant zero-download character reusing an installed Piper base voice:
  ./.venv/bin/python add_voice.py \
      --id tars --name "TARS" --title "TARS (Interstellar)" --emoji "🛰️" \
      --transpose -2 --speed 1.08 --accent "#38bdf8" --mood focused \
      --prompt "You are TARS from Interstellar (humor setting 75%). Reply in 2 crisp sentences."

  # 3. List or remove installed voices:
  ./.venv/bin/python add_voice.py --list
  ./.venv/bin/python add_voice.py --remove tars
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import wave
import zipfile

import httpx
import numpy as np
import parselmouth
import soundfile as sf

from character_registry import CHARACTERS_DIR, MODELS_DIR, PROJECT_DIR, REGISTRY

UV_BIN = os.path.expanduser("~/.local/bin/uv")
CONVERTER_SCRIPT = os.path.join(PROJECT_DIR, "tools", "rvc_export", "convert_to_int8.py")
SERVER_URL = os.environ.get("ROCKY_SERVER_URL", "http://127.0.0.1:7860")


def _hex_to_shell_colors(accent_hex: str) -> tuple[str, str, list[int]]:
    h = accent_hex.lstrip("#")
    if len(h) != 6:
        return "#53b09c", "#3b8978", [205, 238, 224]
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    # Blend accent gently with classic BMO teal/mint so BMO keeps cartoon readability
    sr = int(r * 0.32 + 83 * 0.68)
    sg = int(g * 0.32 + 176 * 0.68)
    sb = int(b * 0.32 + 156 * 0.68)
    dr, dg, db = int(sr * 0.72), int(sg * 0.72), int(sb * 0.72)
    cr = min(245, int(r * 0.14 + 205 * 0.86))
    cg = min(248, int(g * 0.14 + 238 * 0.86))
    cb = min(248, int(b * 0.14 + 224 * 0.86))
    return f"#{sr:02x}{sg:02x}{sb:02x}", f"#{dr:02x}{dg:02x}{db:02x}", [cr, cg, cb]


def ensure_piper_voice(voice_name: str) -> str:
    """
    Ensure `models/piper/<voice_name>.onnx` (+ `.onnx.json`) exists,
    or pass through `macos:<VoiceName>` for zero-weight macOS NativeTTS (`say`).
    """
    if voice_name.startswith("macos:"):
        return voice_name
    clean = voice_name.removesuffix(".onnx").removeprefix("piper/")
    piper_dir = os.path.join(MODELS_DIR, "piper")
    os.makedirs(piper_dir, exist_ok=True)
    onnx_path = os.path.join(piper_dir, f"{clean}.onnx")
    json_path = f"{onnx_path}.json"

    if os.path.exists(onnx_path) and os.path.exists(json_path):
        return f"piper/{clean}.onnx"

    # Parse `<lang>_<REGION>-<speaker>-<quality>`, e.g. `en_US-lessac-medium`
    m = re.match(r"^([a-z]{2})_([A-Z]{2})-([^-]+)-([^-]+)$", clean)
    if not m:
        raise ValueError(
            f"Piper voice '{clean}' not found locally and doesn't match '<lang>_<REGION>-<name>-<quality>'."
        )
    lang, region, speaker, quality = m.groups()
    base_url = (
        f"https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        f"{lang}/{lang}_{region}/{speaker}/{quality}/{clean}"
    )

    print(f"[add_voice] Downloading Piper base voice '{clean}' (~60 MB)...", flush=True)
    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
        for ext, dst in [(".onnx", onnx_path), (".onnx.json", json_path)]:
            r = client.get(base_url + ext)
            r.raise_for_status()
            with open(dst, "wb") as f:
                f.write(r.content)

    return f"piper/{clean}.onnx"


def ingest_rvc_model(model_arg: str, char_id: str, keep_uv_cache: bool = False) -> tuple[str, int]:
    """
    Accept a local path or URL to `.pth`, `.zip`, `.onnx`, or `.int8.onnx`,
    convert & quantize to `models/<char_id>_voice.int8.onnx` (~40 MB), and clean up!
    """
    tmp_dir = tempfile.mkdtemp(prefix=f"voice_{char_id}_")
    try:
        if model_arg.startswith(("http://", "https://")):
            fname = model_arg.split("?")[0].rstrip("/").split("/")[-1] or f"{char_id}.pth"
            local_src = os.path.join(tmp_dir, fname)
            print(f"[add_voice] Downloading model from {model_arg} ...", flush=True)
            with httpx.Client(follow_redirects=True, timeout=180.0) as client:
                with client.stream("GET", model_arg) as r:
                    r.raise_for_status()
                    with open(local_src, "wb") as f:
                        for chunk in r.iter_bytes():
                            f.write(chunk)
        else:
            local_src = os.path.expanduser(model_arg)
            if not os.path.exists(local_src):
                raise FileNotFoundError(f"Model file not found: {local_src}")

        if local_src.lower().endswith(".zip"):
            print(f"[add_voice] Extracting archive {os.path.basename(local_src)} ...", flush=True)
            with zipfile.ZipFile(local_src, "r") as zf:
                zf.extractall(tmp_dir)
            candidates: list[str] = []
            for root, _, files in os.walk(tmp_dir):
                for fn in files:
                    if fn.lower().endswith((".int8.onnx", ".onnx", ".pth")) and not fn.startswith("._"):
                        candidates.append(os.path.join(root, fn))
            if not candidates:
                raise RuntimeError("No .pth or .onnx RVC model found inside .zip archive!")
            candidates.sort(key=lambda p: os.path.getsize(p), reverse=True)
            local_src = candidates[0]

        out_rel = f"{char_id}_voice.int8.onnx"
        out_abs = os.path.join(MODELS_DIR, out_rel)

        if local_src.lower().endswith(".int8.onnx"):
            shutil.copy2(local_src, out_abs)
            return out_rel, 48000

        uv_exec = UV_BIN if os.path.exists(UV_BIN) else "uv"
        pkgs = ["--with", "onnx", "--with", "onnxruntime"]
        if local_src.lower().endswith(".pth"):
            pkgs.extend(["--with", "torch", "--with", "onnxscript"])

        print(f"[add_voice] Converting & quantizing {os.path.basename(local_src)} -> {out_rel} ...", flush=True)
        cmd = [
            uv_exec,
            "run",
            "--no-project",
            "--python",
            "3.12",
            *pkgs,
            "python",
            CONVERTER_SCRIPT,
            local_src,
            out_abs,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"ONNX conversion failed:\n{exc.stderr or exc.stdout}") from exc
        last_line = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1]
        meta = json.loads(last_line)
        print(f"[add_voice] Quantized {out_rel} ({meta['size_mb']} MB, {meta['sample_rate']} Hz)", flush=True)

        if not keep_uv_cache:
            subprocess.run([uv_exec, "cache", "clean"], capture_output=True, check=False)

        return out_rel, int(meta.get("sample_rate", 48000))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def measure_median_f0(audio: np.ndarray, sr: int) -> float:
    f0 = (
        parselmouth.Sound(audio.astype(np.float64), sr)
        .to_pitch_ac(time_step=0.01, voicing_threshold=0.55, pitch_floor=55.0, pitch_ceiling=600.0)
        .selected_array["frequency"]
    )
    voiced = f0[f0 > 55.0]
    if len(voiced) == 0:
        return 130.0
    return float(np.median(voiced))


def auto_calibrate_transpose(piper_rel: str, ref_audio_path: str) -> int:
    from piper import PiperVoice

    piper_abs = os.path.join(MODELS_DIR, piper_rel)
    voice = PiperVoice.load(piper_abs)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        try:
            from piper.config import SynthesisConfig

            voice.synthesize_wav(
                "Hello friend, testing one two three four five.", wf, syn_config=SynthesisConfig()
            )
        except Exception:
            voice.synthesize("Hello friend, testing one two three four five.", wf)
    buf.seek(0)
    yp, srp = sf.read(buf, dtype="float32", always_2d=False)
    yr, srr = sf.read(os.path.expanduser(ref_audio_path), dtype="float32", always_2d=False)
    if yr.ndim > 1:
        yr = yr.mean(axis=1)

    f0_piper = measure_median_f0(yp, srp)
    f0_ref = measure_median_f0(yr, srr)
    semi = int(round(12.0 * math.log2(f0_ref / f0_piper)))
    semi = max(-12, min(12, semi))
    print(
        f"[add_voice] Auto-calibrated pitch: reference={f0_ref:.1f}Hz vs Piper={f0_piper:.1f}Hz -> transpose={semi:+d} semitones",
        flush=True,
    )
    return semi


def notify_live_server() -> None:
    try:
        r = httpx.post(f"{SERVER_URL}/api/characters/reload", timeout=3.0)
        if r.status_code == 200:
            print(f"[add_voice] Hot-reloaded running server at {SERVER_URL}!", flush=True)
    except Exception:
        pass


def remove_character_pack(char_id: str, notify_server: bool = False) -> None:
    cid = re.sub(r"[^a-z0-9_-]", "", char_id.strip().lower())
    manifest = os.path.join(CHARACTERS_DIR, f"{cid}.json")
    if not os.path.exists(manifest):
        raise FileNotFoundError(f"Character '{cid}' not found at {manifest}")
    os.unlink(manifest)
    rvc_file = os.path.join(MODELS_DIR, f"{cid}_voice.int8.onnx")
    if os.path.exists(rvc_file) and cid not in ("rocky", "marvin", "bmo"):
        os.unlink(rvc_file)
    print(f"[add_voice] Removed character '{cid}'.", flush=True)
    if notify_server:
        notify_live_server()


def install_character_pack(
    *,
    char_id: str,
    name: str | None = None,
    title: str | None = None,
    emoji: str = "🎙️",
    subtitle: str | None = None,
    wake: str | None = None,
    piper_voice: str = "en_US-ryan-medium",
    model_source: str | None = None,
    reference_audio: str | None = None,
    transpose: int | None = None,
    speed: float = 1.02,
    style: str = "clean",
    prompt: str | None = None,
    accent: str = "#38bdf8",
    mood: str = "neutral",
    keep_uv_cache: bool = False,
    notify_server: bool = False,
) -> dict:
    cid = re.sub(r"[^a-z0-9_-]", "", char_id.strip().lower())
    if not cid:
        raise ValueError("Character ID must contain lowercase letters or numbers.")
    disp_name = (name or cid.capitalize()).strip()
    disp_title = (title or disp_name).strip()
    wake_words = [w.strip().lower() for w in (wake or cid).split(",") if w.strip()]

    piper_rel = ensure_piper_voice(piper_voice)
    rvc_rel: str | None = None
    sr = 48000
    if model_source and model_source.strip():
        rvc_rel, sr = ingest_rvc_model(model_source.strip(), cid, keep_uv_cache=keep_uv_cache)

    if transpose is not None:
        tr_val = int(transpose)
    elif reference_audio and os.path.exists(reference_audio):
        tr_val = auto_calibrate_transpose(piper_rel, reference_audio)
    else:
        tr_val = 0

    sys_prompt = (prompt or "").strip() or (
        f"You are {disp_name}, acting as the user's fast spoken voice assistant.\n\n"
        f"SPEAKING RULES:\n"
        f"1. Always write TWO short spoken sentences (under 30 words total). No markdown, lists, or stage directions.\n"
        f"2. Sentence 1 MUST be a brief 3-to-5 word in-character opener.\n"
        f"3. Sentence 2 MUST directly and accurately answer the user's question."
    )

    shell_light, shell_dark, screen_rgb = _hex_to_shell_colors(accent)
    REGISTRY.reload()
    existing_spec = REGISTRY._specs.get(cid)
    if existing_spec:
        order_val = existing_spec.order
    else:
        existing_orders = [s.order for s in REGISTRY.all_specs()]
        order_val = (max(existing_orders) + 1) if existing_orders else 1

    manifest_data = {
        "id": cid,
        "order": order_val,
        "name": disp_name,
        "title": disp_title,
        "emoji": emoji or "🎙️",
        "subtitle": (subtitle or "").strip()
        or f"{disp_title} • {'RVC INT8 clone' if rvc_rel else ('macOS NativeTTS' if str(piper_rel).startswith('macos:') else 'Piper neural voice')}",
        "wake_words": wake_words,
        "voice": {
            "piper_onnx": piper_rel,
            "rvc_onnx": rvc_rel,
            "sample_rate": sr,
            "default_transpose": tr_val,
            "default_speed": round(float(speed), 2),
        },
        "persona": {
            "speech_style": style,
            "phrase_rewrites": [],
            "system_prompt": sys_prompt,
            "wake_replies": [
                f"{disp_name} here. What do you need?",
                f"{disp_name} online and listening.",
            ],
            "common_openers": [
                "Right away.",
                "Good question.",
            ],
            "sleep_reply": f"{disp_name} going to standby.",
            "reset_reply": f"{disp_name} memory cleared.",
            "fallback_reply": "Could you repeat that?",
        },
        "ui": {
            "accent": accent,
            "bmo_shell": shell_light,
            "bmo_shell_dark": shell_dark,
            "bmo_screen_rgb": screen_rgb,
            "bmo_badge_prefix": f"{disp_name.upper()[:6]}-MO",
            "idle_mood": mood,
        },
    }

    os.makedirs(CHARACTERS_DIR, exist_ok=True)
    out_manifest = os.path.join(CHARACTERS_DIR, f"{cid}.json")
    with open(out_manifest, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"[add_voice] Saved character manifest -> {out_manifest}", flush=True)
    if notify_server:
        notify_live_server()
    return manifest_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Add, list, or remove character voice packs")
    parser.add_argument("--list", action="store_true", help="List installed characters")
    parser.add_argument("--remove", metavar="ID", help="Remove a character by ID")

    parser.add_argument("--id", help="Short lowercase ID (e.g. tars, glados, hal)")
    parser.add_argument("--name", help="Display name (e.g. TARS)")
    parser.add_argument("--title", help="Card title (e.g. 'TARS (Interstellar)')")
    parser.add_argument("--emoji", default="🎙️", help="Card emoji icon")
    parser.add_argument("--subtitle", help="Card subtitle description")
    parser.add_argument("--wake", help="Comma-separated wake words (default: --id)")
    parser.add_argument(
        "--piper-voice",
        default="en_US-ryan-medium",
        help="Base Piper TTS voice (e.g. en_US-ryan-medium, en_GB-alan-medium, en_US-lessac-medium)",
    )
    parser.add_argument("--model", help="Optional RVC .pth, .zip, or .onnx path or URL")
    parser.add_argument("--reference", help="Optional reference WAV to auto-calibrate pitch transpose")
    parser.add_argument("--transpose", type=int, default=None, help="Pitch shift in semitones")
    parser.add_argument("--speed", type=float, default=1.02, help="Speech speed multiplier")
    parser.add_argument(
        "--style",
        choices=["clean", "eridian", "british_deadpan"],
        default="clean",
        help="Speech text filter style",
    )
    parser.add_argument("--prompt", help="Character LLM system prompt")
    parser.add_argument("--accent", default="#38bdf8", help="UI accent hex color")
    parser.add_argument(
        "--mood",
        default="neutral",
        choices=["neutral", "happy", "excited", "curious", "focused", "alarmed", "sad", "bored", "pained"],
        help="Default idle BMO expression",
    )
    parser.add_argument("--keep-uv-cache", action="store_true", help="Keep uv download cache after .pth export")
    args = parser.parse_args()

    if args.list:
        REGISTRY.reload()
        for spec in REGISTRY.all_specs():
            mode = f"RVC ({os.path.basename(spec.rvc_onnx)})" if spec.rvc_onnx else f"Piper ({os.path.basename(spec.piper_onnx)})"
            print(f"  {spec.emoji} {spec.id:<10} | {spec.title:<30} | Wake: '{spec.primary_wake_phrase}' | {mode}")
        return

    if args.remove:
        try:
            remove_character_pack(args.remove, notify_server=True)
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from exc
        return

    if not args.id:
        parser.error("--id is required when adding a voice (or pass --list / --remove)")

    manifest_data = install_character_pack(
        char_id=args.id,
        name=args.name,
        title=args.title,
        emoji=args.emoji,
        subtitle=args.subtitle,
        wake=args.wake,
        piper_voice=args.piper_voice,
        model_source=args.model,
        reference_audio=args.reference,
        transpose=args.transpose,
        speed=args.speed,
        style=args.style,
        prompt=args.prompt,
        accent=args.accent,
        mood=args.mood,
        keep_uv_cache=args.keep_uv_cache,
        notify_server=True,
    )
    print(
        f"[add_voice] Done! Say 'Hey {manifest_data['name']}, ...' or select {manifest_data['name']} in http://127.0.0.1:7860"
    )


if __name__ == "__main__":
    main()
