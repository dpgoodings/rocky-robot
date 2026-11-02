# Sci-Fi Voice Comms — Plug-and-Play Voice Assistant (BMO Edition)

A 100% local, zero-PyTorch-runtime Speech-to-Speech AI voice assistant featuring **60 FPS emotion-linked BMO facial expressions & lip-sync**, sub-2s streaming responses, and a **plug-and-play Character Pack registry** (`characters/*.json`).

## Built-in Character Packs

1. **🪨 Rocky** (*Project Hail Mary* film, voiced by **James Ortiz**) — `characters/rocky.json`
   - **Wake Word**: `"Hey Rocky"`
   - **Voice**: `en_US-ryan-medium` + `rocky_voice.int8.onnx` (`-5` semitones, `1.18x` speed, Eridian translator filter).
2. **🤖 Marvin the Paranoid Android** (*The Hitchhiker's Guide to the Galaxy* film, voiced by **Alan Rickman**) — `characters/marvin.json`
   - **Wake Word**: `"Hey Marvin"`
   - **Voice**: `en_GB-alan-medium` + `marvin_voice.int8.onnx` (`0` semitones, `0.92x` speed, deadpan Gloom-MO expressions).
3. **🎮 BMO** (*Adventure Time*, voiced by **Niki Yang**) — `characters/bmo.json`
   - **Wake Word**: `"Hey BMO"` (also matches `"Beemo"`, `"Bee Mo"`, `"B-MO"`)
   - **Voice**: `en_US-amy-medium` + `bmo_voice.int8.onnx` (playful living MO-series console persona + native BMO expressions).

## Quick Start (Web Console)

```bash
cd ~/rocky-voice
./start_rocky.sh
```

Opens **`http://127.0.0.1:7860`**.
- In **Wake Word** mode, call any installed character (`"Hey Rocky, ..."`, `"Hey Marvin, ..."`, `"Hey TARS, ..."`) and the console switches voice, memory, and BMO theme automatically.

## Add New Voices in 1 Command (`add_voice.py`)

Adding a voice requires **zero Python or HTML edits** and hot-reloads the live server automatically:

### Option A — Import any RVC v2 Voice Clone (`.zip`, `.pth`, or `.onnx`, local path or URL)
Exports & quantizes `.pth` -> `.int8.onnx` (`~40 MB`) in a temporary sandbox and cleans the build cache so `.venv` stays slim:

```bash
cd ~/rocky-voice
./.venv/bin/python add_voice.py \
  --id glados \
  --name "GLaDOS" \
  --title "GLaDOS (Portal)" \
  --emoji "🥔" \
  --piper-voice en_US-lessac-medium \
  --model ~/Downloads/glados_rvc.zip \
  --reference ~/Downloads/glados_sample.wav \
  --accent "#facc15" \
  --mood focused \
  --prompt "You are GLaDOS from Portal. Answer accurately in 2 short passive-aggressive sentences."
```
*(Passing `--reference sample.wav` automatically measures median $F_0$ pitch via Praat/Parselmouth and calibrates `--transpose` semitones!)*

### Option B — Instant `0 MB` Character (Reuses an Installed Piper Base Voice + Pitch/Speed Shift)

```bash
./.venv/bin/python add_voice.py \
  --id tars \
  --name "TARS" \
  --title "TARS (Interstellar)" \
  --emoji "🛰️" \
  --transpose -2 \
  --speed 1.08 \
  --accent "#38bdf8" \
  --mood focused \
  --prompt "You are TARS from Interstellar (humor setting 75%). Reply in 2 crisp spoken sentences."
```

### Manage Voices or Edit JSON Directly
```bash
./.venv/bin/python add_voice.py --list          # List installed character packs
./.venv/bin/python add_voice.py --remove tars   # Remove manifest + voice model
```
You can also copy `characters/_template.json` to `characters/<id>.json` and click **`↻ Reload Voices`** in the Web UI header (`POST /api/characters/reload`).

### Memory & Disk Efficiency on 8 GB Apple Silicon
- **Shared Base TTS**: Characters sharing a Piper `.onnx` model share a single in-memory instance (`0 MB` extra RAM/disk).
- **LRU RVC Session Cap**: Only the `3` most recently spoken RVC voices stay resident in RAM (`ROCKY_MAX_LOADED_VOICES=3`, `~40 MB` each), so installing 20+ voices never exhausts memory.

## Terminal-Only Mode

```bash
cd ~/rocky-voice
./.venv/bin/python cli_rocky.py                           # Listens for all registered wake words
./.venv/bin/python cli_rocky.py --character marvin --continuous
```
