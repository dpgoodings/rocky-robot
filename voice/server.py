"""
Rocky Studio Voice & Robot Server.

Single-persona Rocky server backed by local Ollama (`qwen2.5:1.5b` + `qwen3-vl:2b-instruct`),
Faster-Whisper STT, Piper + RVC INT8 ONNX voice, ESP32-S3 ST7789 face & servo bridge,
persistent settings (`settings.json`), live server log ring buffer, and optional body language
& adaptive turn detection hooks.
"""

from __future__ import annotations

import asyncio
import base64
from collections import deque
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import datetime
import io
import json
import os
import queue
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import wave

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import mcp_homeassistant
import memory
import robot_vision
from brain import (
    CLOUD_PROVIDER_DEFAULT_MODELS,
    RockyBrain,
    _get_robot_ip_fast,
    _probe_ip,
    get_llm_runtime_status,
    resolve_robot_ip,
    set_cached_robot_ip,
    set_event_logger,
    set_llm_runtime_config,
)
from character_registry import REGISTRY
from face_tracker import FACE_TRACKER
from rocky_filter import (
    apply_character_filter,
    detect_utterance_emotion,
    parse_wake_utterance,
)
from voice_engine import RockyVoiceEngine

try:
    from body_language import BODY_LANGUAGE
except Exception:
    BODY_LANGUAGE = None  # type: ignore[assignment]

try:
    from turn_detector import TURN_DETECTOR
except Exception:
    TURN_DETECTOR = None  # type: ignore[assignment]

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(PROJECT_DIR, "static")
SETTINGS_PATH = os.path.join(PROJECT_DIR, "settings.json")

# -----------------------------------------------------------------------------
# Live Server Log Ring Buffer (last 200 timestamped events)
# -----------------------------------------------------------------------------
_LOG_LOCK = threading.Lock()
_LOG_RING: deque[str] = deque(maxlen=200)


def add_server_log(category: str, message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{category.upper()}] {message}"
    with _LOG_LOCK:
        _LOG_RING.append(line)
    print(line, flush=True)


set_event_logger(add_server_log)

# -----------------------------------------------------------------------------
# Persistent Studio Settings (`voice/settings.json`)
# -----------------------------------------------------------------------------
DEFAULT_SETTINGS: dict[str, Any] = {
    "transpose": -5,
    "speed": 1.18,
    "filter_intensity": "balanced",
    "vad_threshold": 0.022,
    "output_mode": "pc",
    "expr_target": "both",
    "robot_ip": "192.168.4.40",
    "body_language": True,
    "wake_mode": "wake",
    "barge_in": False,
    "say_exact": False,
    "llm_provider": "ollama",
    "cloud_model": "gemini-2.5-flash",
    "cloud_api_key": "",
}
_SETTINGS_LOCK = threading.Lock()


def _coerce_settings(raw: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS if base is None else base)
    if "transpose" in raw and raw["transpose"] is not None:
        out["transpose"] = max(-12, min(12, int(raw["transpose"])))
    if "speed" in raw and raw["speed"] is not None:
        out["speed"] = round(max(0.5, min(2.0, float(raw["speed"]))), 2)
    if "filter_intensity" in raw and raw["filter_intensity"] in ("balanced", "strict", "off"):
        out["filter_intensity"] = str(raw["filter_intensity"])
    if "vad_threshold" in raw and raw["vad_threshold"] is not None:
        out["vad_threshold"] = round(max(0.002, min(0.2, float(raw["vad_threshold"]))), 4)
    if "output_mode" in raw and raw["output_mode"] in ("pc", "browser"):
        out["output_mode"] = str(raw["output_mode"])
    if "expr_target" in raw and raw["expr_target"] in ("both", "lcd", "browser"):
        out["expr_target"] = str(raw["expr_target"])
    if "robot_ip" in raw and isinstance(raw["robot_ip"], str) and raw["robot_ip"].strip():
        out["robot_ip"] = raw["robot_ip"].strip()
    if "body_language" in raw and raw["body_language"] is not None:
        out["body_language"] = bool(raw["body_language"])
    if "wake_mode" in raw and raw["wake_mode"] in ("wake", "continuous", "ptt"):
        out["wake_mode"] = str(raw["wake_mode"])
    if "barge_in" in raw and raw["barge_in"] is not None:
        out["barge_in"] = bool(raw["barge_in"])
    if "say_exact" in raw and raw["say_exact"] is not None:
        out["say_exact"] = bool(raw["say_exact"])
    if "llm_provider" in raw and raw["llm_provider"] in ("ollama", "gemini", "openrouter", "openai"):
        out["llm_provider"] = str(raw["llm_provider"])
    if "cloud_model" in raw and isinstance(raw["cloud_model"], str):
        cm = raw["cloud_model"].strip()
        if cm:
            out["cloud_model"] = cm
        elif out["llm_provider"] in CLOUD_PROVIDER_DEFAULT_MODELS:
            out["cloud_model"] = CLOUD_PROVIDER_DEFAULT_MODELS[out["llm_provider"]]
    if raw.get("clear_cloud_api_key"):
        out["cloud_api_key"] = ""
    elif "cloud_api_key" in raw and isinstance(raw["cloud_api_key"], str) and raw["cloud_api_key"].strip():
        # Only overwrite saved key when a non-empty key is explicitly provided
        out["cloud_api_key"] = raw["cloud_api_key"].strip()
    return out


def _public_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return studio settings with secrets masked and live brain provider status attached."""
    pub = dict(cfg)
    has_key = bool(str(pub.get("cloud_api_key") or "").strip())
    pub["cloud_api_key"] = ""
    pub["cloud_api_key_set"] = has_key
    llm_st = get_llm_runtime_status()
    pub["active_provider"] = llm_st["active_provider"]
    pub["active_model"] = llm_st["active_model"]
    return pub


def load_settings() -> dict[str, Any]:
    with _SETTINGS_LOCK:
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    return _coerce_settings(raw)
            except Exception as exc:
                add_server_log("SETTINGS", f"Failed to load settings.json, using defaults: {exc}")
        return dict(DEFAULT_SETTINGS)


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    with _SETTINGS_LOCK:
        current = dict(DEFAULT_SETTINGS)
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    current = _coerce_settings(loaded)
            except Exception:
                pass
        merged = _coerce_settings(updates, base=current)
        tmp_path = SETTINGS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
        os.replace(tmp_path, SETTINGS_PATH)
    _apply_live_settings(merged)
    return merged


def _apply_live_settings(cfg: dict[str, Any]) -> None:
    ip = str(cfg.get("robot_ip") or "").strip()
    if ip:
        set_cached_robot_ip(ip)
    set_llm_runtime_config(
        provider=str(cfg.get("llm_provider") or "ollama"),
        cloud_model=str(cfg.get("cloud_model") or ""),
        cloud_api_key=str(cfg.get("cloud_api_key") or ""),
    )
    if BODY_LANGUAGE is not None:
        try:
            BODY_LANGUAGE.set_enabled(bool(cfg.get("body_language", True)))
        except Exception:
            pass


_SETTINGS = load_settings()
_apply_live_settings(_SETTINGS)

engine = RockyVoiceEngine()
brain = RockyBrain()
MODELS_READY = False


def _body_thinking() -> None:
    if BODY_LANGUAGE is None:
        return
    try:
        ip = _get_robot_ip_fast() or _SETTINGS.get("robot_ip") or "192.168.4.40"
        BODY_LANGUAGE.on_thinking(ip)
    except Exception:
        pass


def _body_speak_start(emotion: str) -> None:
    if BODY_LANGUAGE is None:
        return
    try:
        ip = _get_robot_ip_fast() or _SETTINGS.get("robot_ip") or "192.168.4.40"
        BODY_LANGUAGE.on_speak_start(emotion, ip)
    except Exception:
        pass


def _body_speak_stop() -> None:
    if BODY_LANGUAGE is None:
        return
    try:
        BODY_LANGUAGE.on_speak_stop()
    except Exception:
        pass


# Host PC/Mac Speaker Playback Queue
_speaker_queue: queue.Queue[bytes | None] = queue.Queue()
_active_afplay_lock = threading.Lock()
_active_afplay_proc: subprocess.Popen[bytes] | None = None


def stop_pc_speaker() -> None:
    """Immediately stop active Mac speaker playback and flush queued chunks (Alexa-style barge-in)."""
    while not _speaker_queue.empty():
        try:
            _speaker_queue.get_nowait()
            _speaker_queue.task_done()
        except Exception:
            break
    with _active_afplay_lock:
        proc = _active_afplay_proc
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


def _wav_duration_ms(wav_bytes: bytes) -> int:
    """Compute exact WAV playback duration in milliseconds from header."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 48000
            return max(250, int(round((frames / rate) * 1000)))
    except Exception:
        return max(400, int(round((max(0, len(wav_bytes) - 44) / 96000.0) * 1000)))


def _pc_speaker_loop() -> None:
    global _active_afplay_proc
    while True:
        wav_data = _speaker_queue.get()
        if wav_data is None:
            break
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                tf.write(wav_data)
                tpath = tf.name
            if sys.platform == "darwin":
                proc = subprocess.Popen(["afplay", tpath])
                with _active_afplay_lock:
                    _active_afplay_proc = proc
                proc.wait()
                with _active_afplay_lock:
                    if _active_afplay_proc is proc:
                        _active_afplay_proc = None
            try:
                os.remove(tpath)
            except OSError:
                pass
        except Exception as exc:
            add_server_log("AUDIO", f"PC speaker output error: {exc}")
        finally:
            _speaker_queue.task_done()


threading.Thread(target=_pc_speaker_loop, daemon=True, name="pc_speaker_worker").start()


def queue_pc_speaker(wav_data: bytes) -> None:
    """Queues synthesized speech to play directly out of the host PC/Mac speakers."""
    _speaker_queue.put(wav_data)


# -----------------------------------------------------------------------------
# Acoustic Feedback Echo Guard (Prevents mic from transcribing Rocky's speaker audio)
# -----------------------------------------------------------------------------
_RECENT_SPOKEN_LOCK = threading.Lock()
_RECENT_SPOKEN_SENTENCES: list[tuple[float, str]] = []


def record_spoken_sentence(sentence: str) -> None:
    """Records speech outputted by Rocky so microphone feedback can be recognized and dropped."""
    clean = sentence.strip()
    if not clean:
        return
    now = time.time()
    with _RECENT_SPOKEN_LOCK:
        _RECENT_SPOKEN_SENTENCES.append((now, clean))
        cutoff = now - 15.0
        while _RECENT_SPOKEN_SENTENCES and _RECENT_SPOKEN_SENTENCES[0][0] < cutoff:
            _RECENT_SPOKEN_SENTENCES.pop(0)


def is_self_acoustic_echo(transcript: str, window_seconds: float = 12.0) -> bool:
    """Detects if microphone transcript is an acoustic echo of words recently spoken by Rocky."""
    now = time.time()
    with _RECENT_SPOKEN_LOCK:
        cutoff = now - window_seconds
        while _RECENT_SPOKEN_SENTENCES and _RECENT_SPOKEN_SENTENCES[0][0] < cutoff:
            _RECENT_SPOKEN_SENTENCES.pop(0)
        if not _RECENT_SPOKEN_SENTENCES:
            return False

        t_words = [
            w for w in re.findall(r"[a-z0-9]+", transcript.lower())
            if len(w) > 1 and w not in ("the", "a", "an", "is", "it", "to", "in", "of")
        ]
        if not t_words:
            return True

        spoken_text = " ".join(s for _, s in _RECENT_SPOKEN_SENTENCES).lower()
        spoken_words = set(re.findall(r"[a-z0-9]+", spoken_text))

        matched_words = [w for w in t_words if w in spoken_words]
        match_ratio = len(matched_words) / len(t_words)

        if len(t_words) <= 5 and match_ratio >= 0.50:
            return True
        if match_ratio >= 0.75:
            return True
        return False


def _prewarm_rocky_phrases() -> None:
    spec = REGISTRY.get("rocky")
    phrases = [
        *spec.wake_replies,
        *spec.common_openers,
        spec.sleep_reply,
        spec.reset_reply,
    ]
    for phrase in phrases:
        if phrase:
            try:
                engine.synthesize_character_wav(phrase, character="rocky")
            except Exception as exc:
                add_server_log("STARTUP", f"Prewarm skipped for Rocky phrase: {exc}")


def _background_init() -> None:
    global MODELS_READY
    try:
        add_server_log("STARTUP", "Initializing Rocky voice & Ollama brain...")
        engine.load_all()
        brain.warmup()
        _prewarm_rocky_phrases()
        MODELS_READY = True
        add_server_log("STARTUP", f"Rocky ready! LLM={brain.model}, Vision={brain.vision_model}")
    except Exception as exc:
        add_server_log("ERROR", f"Startup error: {exc}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_background_init, daemon=True).start()
    yield


app = FastAPI(title="Rocky Local Voice & Robot Studio", lifespan=lifespan)
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class TextTurnRequest(BaseModel):
    text: str
    character: str = "rocky"
    filter_intensity: str = "balanced"
    transpose: int | None = None
    speed: float | None = None
    say_exact: bool = False
    play_host_speaker: bool = True


def _ndjson(obj: dict[str, Any]) -> str:
    return json.dumps(obj) + "\n"


def _stream_llm_and_voice(
    query: str,
    resp_char: str,
    filter_intensity: str,
    tr_val: int,
    sp_val: float,
    state_out: dict[str, bool] | None = None,
    play_host_speaker: bool = True,
) -> Iterator[str]:
    sent_q: queue.Queue[dict[str, Any] | None | Exception] = queue.Queue()
    _body_thinking()

    def _producer() -> None:
        try:
            for item in brain.ask_stream(
                query, character=resp_char, filter_intensity=filter_intensity
            ):
                sent_q.put(item)
        except Exception as exc:
            sent_q.put(exc)
        finally:
            sent_q.put(None)

    threading.Thread(target=_producer, daemon=True).start()
    speaking_started = False

    try:
        while True:
            item = sent_q.get()
            if item is None:
                break
            if isinstance(item, Exception):
                add_server_log("ERROR", f"Brain stream error: {item}")
                yield _ndjson({"type": "error", "detail": str(item)})
                break

            if item.get("end_conversation") and state_out is not None:
                state_out["end_conversation"] = True

            if not item.get("sentence"):
                continue

            chunk_speed = min(sp_val, 0.94) if item.get("is_reading") else sp_val
            wav_bytes, voice_ms = engine.synthesize_character_wav(
                item["sentence"], character=resp_char, transpose=tr_val, speed=chunk_speed
            )
            record_spoken_sentence(item["sentence"])
            forced_emotion = item.get("emotion")
            chunk_emotion = forced_emotion or detect_utterance_emotion(
                item["sentence"], character=resp_char
            )
            if not speaking_started:
                _body_speak_start(chunk_emotion)
                speaking_started = True
            if play_host_speaker:
                queue_pc_speaker(wav_bytes)
            if forced_emotion:
                _CURRENT_FACE_STATE["mood"] = item.get("esp_mood") or forced_emotion
                _CURRENT_FACE_STATE["anim"] = item.get("esp_anim")
                _CURRENT_FACE_STATE["updated_at"] = time.time()
            chunk_payload: dict[str, Any] = {
                "type": "audio_chunk",
                "character": resp_char,
                "index": item["index"],
                "sentence": item["sentence"],
                "emotion": chunk_emotion,
                "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
                "duration_ms": _wav_duration_ms(wav_bytes),
                "llm_ms": item["clause_ms"],
                "voice_ms": voice_ms,
            }
            if item.get("hold_emotion_ms"):
                chunk_payload["hold_emotion_ms"] = int(item["hold_emotion_ms"])
            yield _ndjson(chunk_payload)

        if play_host_speaker:
            _speaker_queue.join()
    finally:
        _body_speak_stop()


@app.get("/")
def index():
    return FileResponse(
        os.path.join(STATIC_DIR, "index.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/emulator")
@app.get("/lcd-emulator")
def emulator_page():
    return FileResponse(
        os.path.join(STATIC_DIR, "emulator.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    llm_st = get_llm_runtime_status()
    out: dict[str, Any] = {
        "status": "ready" if MODELS_READY else "loading",
        "ready": MODELS_READY,
        "llm_model": brain.model,
        "vision_model": brain.vision_model,
        "active_provider": llm_st["active_provider"],
        "active_model": llm_st["active_model"],
        "default_character": REGISTRY.default_id,
        "characters": REGISTRY.public_map(),
    }
    if BODY_LANGUAGE is not None:
        try:
            out["body_language"] = BODY_LANGUAGE.status()
        except Exception:
            pass
    if TURN_DETECTOR is not None:
        try:
            out["turn_detector"] = TURN_DETECTOR.status()
        except Exception:
            pass
    return out


@app.get("/api/characters")
def list_characters() -> dict[str, Any]:
    return {
        "default_character": REGISTRY.default_id,
        "characters": REGISTRY.public_map(),
    }


@app.post("/api/characters/reload")
def reload_characters() -> dict[str, Any]:
    REGISTRY.reload()
    return {
        "status": "reloaded",
        "default_character": REGISTRY.default_id,
        "characters": REGISTRY.public_map(),
    }


# -----------------------------------------------------------------------------
# Persistent Studio Settings & Live Server Log Endpoints
# -----------------------------------------------------------------------------
@app.get("/api/settings")
def get_settings_api() -> dict[str, Any]:
    global _SETTINGS
    _SETTINGS = load_settings()
    _apply_live_settings(_SETTINGS)
    return _public_settings(_SETTINGS)


@app.post("/api/settings")
def update_settings_api(payload: dict[str, Any]) -> dict[str, Any]:
    global _SETTINGS
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Settings payload must be a JSON object")
    _SETTINGS = save_settings(payload)
    if "robot_ip" in payload and _SETTINGS.get("robot_ip"):
        try:
            robot_vision.save_camera_config({"esp32_ip": _SETTINGS["robot_ip"]})
        except Exception:
            pass
    safe_keys = sorted(k for k in payload.keys() if k != "cloud_api_key")
    if "cloud_api_key" in payload and str(payload.get("cloud_api_key") or "").strip():
        safe_keys.append("cloud_api_key(updated)")
    add_server_log("SETTINGS", f"Updated studio settings: {', '.join(safe_keys)}")
    return _public_settings(_SETTINGS)


# -----------------------------------------------------------------------------
# Teach Rocky: Spoken Rules, Live Prompt Editor & Guarded Auto-Fix Feedback
# -----------------------------------------------------------------------------
AGY_BIN = os.path.expanduser("~/.local/bin/agy")
REPO_ROOT = os.path.dirname(PROJECT_DIR)
ROCKY_CHAR_PATH = os.path.join(PROJECT_DIR, "characters", "rocky.json")


@app.get("/api/rules")
def get_rules_api() -> dict[str, Any]:
    return {"rules": memory.list_rules()}


@app.post("/api/rules")
def add_rule_api(payload: dict[str, Any]) -> dict[str, Any]:
    rule_text = str((payload or {}).get("rule", "")).strip()
    if len(rule_text) < 3:
        raise HTTPException(status_code=400, detail="Rule text must be at least 3 characters")
    saved = memory.add_rule(rule_text)
    add_server_log("RULE", f"Saved behavior rule #{saved.get('id')}: {rule_text}")
    return {"status": "saved", "rule": saved, "rules": memory.list_rules()}


@app.delete("/api/rules/{rule_id}")
def delete_rule_api(rule_id: int) -> dict[str, Any]:
    ok = memory.delete_rule(rule_id)
    add_server_log("RULE", f"Deleted behavior rule #{rule_id}: {ok}")
    return {"status": "deleted", "deleted": ok, "rules": memory.list_rules()}


@app.get("/api/rocky/prompt")
def get_rocky_prompt_api() -> dict[str, Any]:
    try:
        with open(ROCKY_CHAR_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        prompt = str(data.get("persona", {}).get("system_prompt", ""))
        return {"name": data.get("name", "Rocky"), "system_prompt": prompt}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read rocky.json: {exc}")


@app.post("/api/rocky/prompt")
def update_rocky_prompt_api(payload: dict[str, Any]) -> dict[str, Any]:
    new_prompt = str((payload or {}).get("system_prompt", "")).strip()
    if len(new_prompt) < 20:
        raise HTTPException(status_code=400, detail="System prompt is too short")
    try:
        with open(ROCKY_CHAR_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("persona", {})["system_prompt"] = new_prompt
        tmp_path = ROCKY_CHAR_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, ROCKY_CHAR_PATH)
        REGISTRY.reload()
        add_server_log("PROMPT", f"Updated Rocky system prompt ({len(new_prompt)} chars) and hot-reloaded")
        return {"status": "saved", "system_prompt": new_prompt}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save rocky.json: {exc}")


@app.get("/api/feedback")
def list_feedback_api() -> dict[str, Any]:
    return {
        "flags": memory.list_feedback_flags(limit=30),
        "agy_available": os.path.exists(AGY_BIN),
    }


@app.post("/api/feedback")
def add_feedback_api(payload: dict[str, Any]) -> dict[str, Any]:
    body = payload or {}
    user_text = str(body.get("user_text", "")).strip()
    rocky_reply = str(body.get("rocky_reply", "")).strip()
    note = str(body.get("note", "")).strip()
    if not note and not rocky_reply and not user_text:
        raise HTTPException(status_code=400, detail="Feedback payload cannot be empty")
    flag = memory.add_feedback_flag(user_text=user_text, rocky_reply=rocky_reply, note=note)
    add_server_log("FEEDBACK", f"Flagged turn #{flag['id']}: \"{note[:80]}\"")
    return {"status": "saved", "flag": flag}


@app.delete("/api/feedback/{flag_id}")
def delete_feedback_api(flag_id: int) -> dict[str, Any]:
    ok = memory.delete_feedback_flag(flag_id)
    add_server_log("FEEDBACK", f"Deleted feedback flag #{flag_id}: {ok}")
    return {"status": "deleted", "deleted": ok}


def _run_autofix_worker(flag_id: int, flag: dict[str, Any], recent_logs: list[str]) -> None:
    logs_excerpt = "\n".join(recent_logs[-25:])
    prompt = (
        "You are fixing a live user-reported issue in Rocky Robot Studio (/Users/dad/rocky-robot).\n"
        f"User said to Rocky: {flag.get('user_text', '')!r}\n"
        f"Rocky replied: {flag.get('rocky_reply', '')!r}\n"
        f"User's requested fix: {flag.get('note', '')!r}\n\n"
        "Recent server logs:\n"
        f"{logs_excerpt}\n\n"
        "Instructions:\n"
        "1. Keep Rocky-only persona (no Marvin/BMO/TARS) and preserve all existing features.\n"
        "2. Make the smallest clean fix in /Users/dad/rocky-robot/voice/ (or rocky.json) that addresses the user's note.\n"
        "3. Run unit tests: /Users/dad/rocky-robot/voice/.venv/bin/python -m unittest discover -s /Users/dad/rocky-robot/voice/tests -v\n"
        "4. Reply with a concise 2-4 line summary of what changed and test results."
    )
    add_server_log("AUTOFIX", f"Starting agy auto-fix for flag #{flag_id}")
    try:
        proc = subprocess.run(
            [AGY_BIN, "-p", prompt, "--dangerously-skip-permissions", "--print-timeout", "120s"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=150,
        )
        out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
        trimmed = out[-3000:] if len(out) > 3000 else out
        if proc.returncode == 0:
            memory.update_feedback_flag(flag_id, status="done", fix_output=trimmed or "Auto-fix completed.")
            add_server_log("AUTOFIX", f"Flag #{flag_id} auto-fix completed successfully")
        else:
            memory.update_feedback_flag(
                flag_id,
                status="failed",
                fix_output=trimmed or f"agy exited with code {proc.returncode}",
            )
            add_server_log("AUTOFIX", f"Flag #{flag_id} auto-fix failed (exit {proc.returncode})")
    except Exception as exc:
        memory.update_feedback_flag(flag_id, status="failed", fix_output=f"Auto-fix error: {exc}")
        add_server_log("AUTOFIX", f"Flag #{flag_id} auto-fix exception: {exc}")


@app.post("/api/feedback/{flag_id}/autofix")
def trigger_feedback_autofix(flag_id: int) -> dict[str, Any]:
    flag = memory.get_feedback_flag(flag_id)
    if not flag:
        raise HTTPException(status_code=404, detail="Feedback flag not found")
    if flag.get("status") == "running":
        return {"status": "running", "flag": flag}
    if not os.path.exists(AGY_BIN):
        raise HTTPException(status_code=503, detail=f"Antigravity CLI not found at {AGY_BIN}")

    updated = memory.update_feedback_flag(flag_id, status="running", fix_output="Dispatching to Antigravity CLI (agy)…")
    with _LOG_LOCK:
        recent_logs = list(_LOG_RING)
    threading.Thread(
        target=_run_autofix_worker,
        args=(flag_id, flag, recent_logs),
        daemon=True,
        name=f"agy_autofix_{flag_id}",
    ).start()
    return {"status": "running", "flag": updated}


@app.get("/api/logs")
def get_server_logs() -> dict[str, Any]:
    with _LOG_LOCK:
        lines = list(_LOG_RING)
    return {"lines": lines, "count": len(lines)}


@app.post("/api/logs/clear")
@app.delete("/api/logs")
def clear_server_logs() -> dict[str, Any]:
    with _LOG_LOCK:
        _LOG_RING.clear()
    return {"lines": [], "count": 0}


@app.get("/api/turn/status")
def turn_detector_status() -> dict[str, Any]:
    if TURN_DETECTOR is None:
        return {"available": False, "method": "none"}
    try:
        return {"available": True, **TURN_DETECTOR.status()}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


@app.post("/api/audio/stop")
def api_audio_stop() -> dict[str, Any]:
    """Immediately stop active Mac speaker playback for instant Alexa-style interruption."""
    stop_pc_speaker()
    return {"status": "stopped"}


@app.post("/api/reset")
def reset_memory(character: str | None = None) -> dict[str, str]:
    brain.reset_history(character=character or "rocky")
    add_server_log("MEMORY", "Conversation history cleared")
    return {"status": "cleared"}


@app.get("/api/memories")
def get_memories() -> dict[str, Any]:
    return {
        "facts": memory.get_facts(),
        "recent_observations": memory.get_recent_visual_memories(limit=10),
    }


@app.post("/api/memories/fact")
def save_memory_fact(payload: dict[str, Any]) -> dict[str, Any]:
    key = str(payload.get("key", "")).strip()
    value = str(payload.get("value", "")).strip()
    category = str(payload.get("category", "user_defined")).strip()
    if key and value:
        memory.set_fact(key, value, category=category)
        return {"status": "saved", "key": key, "value": value}
    return {"status": "error", "message": "key and value required"}


# -----------------------------------------------------------------------------
# Home Assistant MCP Endpoints
# -----------------------------------------------------------------------------
@app.get("/api/hass/status")
def hass_status() -> dict[str, Any]:
    return mcp_homeassistant.test_connection()


@app.post("/api/hass/config")
def hass_config(payload: dict[str, Any]) -> dict[str, Any]:
    token = str(payload.get("token", "")).strip()
    url = str(payload.get("url", mcp_homeassistant.DEFAULT_HASS_URL)).strip()
    if token:
        mcp_homeassistant.save_config(token=token, url=url)
        return mcp_homeassistant.test_connection()
    return {"connected": False, "error": "Token is required"}


@app.get("/api/hass/entities")
def hass_entities() -> list[dict[str, Any]]:
    return mcp_homeassistant.fetch_entities()


@app.get("/api/hass/check-config")
def hass_check_config() -> dict[str, Any]:
    return mcp_homeassistant.check_config()


@app.get("/api/hass/broken-entities")
def hass_broken_entities() -> list[dict[str, Any]]:
    return mcp_homeassistant.find_broken_entities()


@app.post("/api/hass/reload")
def hass_reload(payload: dict[str, Any]) -> dict[str, Any]:
    domain = str(payload.get("domain", "all")).strip()
    return mcp_homeassistant.reload_services(domain)


@app.post("/api/hass/gemini")
def hass_gemini_query(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    response_text = mcp_homeassistant.query_gemini_ha(prompt)
    return {"prompt": prompt, "response": response_text}


# -----------------------------------------------------------------------------
# Robot Face Tracking, Dance, Servo & Assembly Control Proxies
# -----------------------------------------------------------------------------
@app.get("/api/robot/tracking")
def robot_tracking_status() -> dict[str, Any]:
    return FACE_TRACKER.status()


@app.post("/api/robot/tracking")
def robot_tracking_toggle(payload: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(payload.get("enabled", True))
    if enabled:
        ip = str(payload.get("robot_ip") or _get_robot_ip_fast() or resolve_robot_ip()).strip()
        add_server_log("TRACK", f"Face tracking enabled ({ip})")
        return FACE_TRACKER.start(ip)
    add_server_log("TRACK", "Face tracking disabled")
    return FACE_TRACKER.stop()


_DANCE_ROUTINE_MAP = {
    "": "dance",
    "random": "dance",
    "dance": "dance",
    "groove": "dance_groove",
    "dance_groove": "dance_groove",
    "robot": "dance_robot",
    "dance_robot": "dance_robot",
    "sprinkler": "dance_sprinkler",
    "dance_sprinkler": "dance_sprinkler",
    "disco": "dance_disco",
    "dance_disco": "dance_disco",
}


@app.post("/api/robot/dance")
async def robot_dance(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = payload or {}
    routine = str(body.get("routine") or body.get("action") or "random").strip().lower()
    action_name = _DANCE_ROUTINE_MAP.get(routine, "dance")
    ip = str(body.get("robot_ip") or _get_robot_ip_fast() or await asyncio.to_thread(resolve_robot_ip)).strip()
    add_server_log("DANCE", f"Triggering '{action_name}' on {ip}")
    try:
        client = get_robot_async_client()
        resp = await client.post(f"http://{ip}/action", json={"action": action_name})
        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                data = {"raw": resp.text}
            return {"status": "ok", "action": action_name, "routine": routine, "robot_ip": ip, "data": data}
        return {"status": "error", "code": resp.status_code, "action": action_name, "robot_ip": ip}
    except Exception as exc:
        add_server_log("ERROR", f"Dance dispatch failed ({ip}): {exc}")
        return {"status": "error", "message": str(exc), "action": action_name, "robot_ip": ip}


@app.post("/api/robot/servo")
async def robot_servo(payload: dict[str, Any]) -> dict[str, Any]:
    ip = str(payload.get("robot_ip") or _get_robot_ip_fast() or await asyncio.to_thread(resolve_robot_ip)).strip()
    fwd: dict[str, Any] = {}
    pan_val: float | None = None
    tilt_val: float | None = None
    if "pan" in payload and payload["pan"] is not None:
        pan_val = round(max(15.0, min(165.0, float(payload["pan"]))), 1)
        fwd["pan"] = pan_val
    if "tilt" in payload and payload["tilt"] is not None:
        tilt_val = round(max(58.0, min(132.0, float(payload["tilt"]))), 1)
        fwd["tilt"] = tilt_val
    if "speed" in payload and payload["speed"] is not None:
        fwd["speed"] = max(30, min(600, int(payload["speed"])))
    else:
        fwd["speed"] = 180
    if not ("pan" in fwd or "tilt" in fwd):
        pan_val, tilt_val = 90.0, 90.0
        fwd["pan"], fwd["tilt"] = pan_val, tilt_val

    if hasattr(FACE_TRACKER, "set_manual_pose"):
        try:
            cur_pan = pan_val if pan_val is not None else float(getattr(FACE_TRACKER, "pan_deg", 90.0))
            cur_tilt = tilt_val if tilt_val is not None else float(getattr(FACE_TRACKER, "tilt_deg", 90.0))
            FACE_TRACKER.set_manual_pose(cur_pan, cur_tilt)
        except Exception:
            pass

    add_server_log("SERVO", f"POST /servo {fwd} -> {ip}")
    try:
        client = get_robot_async_client()
        resp = await client.post(f"http://{ip}/servo", json=fwd)
        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                data = {"raw": resp.text}
            return {"status": "ok", **fwd, "robot_ip": ip, "data": data}
        return {"status": "error", "code": resp.status_code, **fwd, "robot_ip": ip}
    except Exception as exc:
        add_server_log("ERROR", f"Servo dispatch failed ({ip}): {exc}")
        return {"status": "error", "message": str(exc), **fwd, "robot_ip": ip}


@app.post("/api/robot/assembly")
async def robot_assembly(payload: dict[str, Any]) -> dict[str, Any]:
    if "enabled" in payload:
        enabled = bool(payload["enabled"])
    elif "on" in payload:
        enabled = bool(payload["on"])
    else:
        enabled = False
    ip = str(payload.get("robot_ip") or _get_robot_ip_fast() or await asyncio.to_thread(resolve_robot_ip)).strip()
    add_server_log("SERVO", f"Assembly mode={'ON' if enabled else 'OFF'} -> {ip}")
    try:
        client = get_robot_async_client()
        resp = await client.post(
            f"http://{ip}/assembly",
            json={"enabled": enabled, "on": 1 if enabled else 0},
        )
        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                data = {"raw": resp.text}
            return {"status": "ok", "enabled": enabled, "robot_ip": ip, "data": data}
        return {"status": "error", "code": resp.status_code, "enabled": enabled, "robot_ip": ip}
    except Exception as exc:
        add_server_log("ERROR", f"Assembly mode failed ({ip}): {exc}")
        return {"status": "error", "message": str(exc), "enabled": enabled, "robot_ip": ip}


# -----------------------------------------------------------------------------
# Robot Vision & Motion Sentry Endpoints
# -----------------------------------------------------------------------------
@app.get("/api/vision/status")
def vision_status() -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": robot_vision.GUARD_ENGINE.last_status,
        "config": robot_vision.load_camera_config(),
        "enrolled_faces": robot_vision.list_enrolled_faces(),
    }
    if hasattr(robot_vision, "get_vision_status"):
        try:
            out["vision"] = robot_vision.get_vision_status()
        except Exception:
            pass
    return out


@app.post("/api/vision/config")
def vision_config(payload: dict[str, Any]) -> dict[str, Any]:
    robot_vision.save_camera_config(payload)
    return {
        "status": "saved",
        "config": robot_vision.load_camera_config(),
    }


@app.post("/api/camera/frame")
async def upload_camera_frame(req: Request) -> dict[str, Any]:
    """Receives uploaded JPEG frame from client or MacBook Pro FaceTime HD camera."""
    body = await req.body()
    if body:
        robot_vision.store_uploaded_frame(body)
        return {"status": "ok", "bytes": len(body)}
    return {"status": "empty"}


@app.get("/api/vision/faces")
def vision_faces() -> list[dict[str, Any]]:
    return robot_vision.list_enrolled_faces()


@app.post("/api/vision/enroll")
async def vision_enroll(
    name: str = Form(...),
    image: UploadFile | None = File(None),
) -> dict[str, Any]:
    if image is not None and image.filename:
        raw = await image.read()
        ok, msg = robot_vision.enroll_face_from_bytes(name, raw)
    else:
        frame = robot_vision.fetch_live_camera_frame()
        if frame is None:
            return {"success": False, "message": "Camera is offline or unreachable"}
        ok, msg = robot_vision.enroll_face(name, frame)
    add_server_log("VISION", f"Enroll '{name}': {ok} ({msg})")
    return {"success": ok, "message": msg, "enrolled_faces": robot_vision.list_enrolled_faces()}


@app.delete("/api/vision/faces/{name}")
def vision_delete_face(name: str) -> dict[str, Any]:
    ok = robot_vision.delete_enrolled_face(name)
    add_server_log("VISION", f"Deleted enrolled face '{name}': {ok}")
    return {"success": ok, "enrolled_faces": robot_vision.list_enrolled_faces()}


@app.post("/api/vision/recognize")
async def vision_recognize(image: UploadFile | None = File(None)) -> dict[str, Any]:
    if image is not None and image.filename:
        import numpy as np
        import cv2
        raw = await image.read()
        nparr = np.frombuffer(raw, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    else:
        frame = robot_vision.fetch_live_camera_frame()
    if frame is None:
        return {"faces": [], "error": "No frame available from camera"}
    faces = robot_vision.recognize_faces(frame)
    return {"faces": faces, "count": len(faces)}


# -----------------------------------------------------------------------------
# Voice & Text Streaming Conversation Endpoints
# -----------------------------------------------------------------------------
@app.post("/api/voice-turn-stream")
async def voice_turn_stream(
    request: Request,
    audio: UploadFile = File(...),
    character: str = Form("rocky"),
    mode: str = Form("wake"),
    in_followup: str = Form("false"),
    filter_intensity: str = Form("balanced"),
    transpose: str = Form(""),
    speed: str = Form(""),
):
    raw_bytes = await audio.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty audio payload")

    play_host_speaker = request.headers.get("x-host-speaker", "true").lower() != "false"
    robot_vision.mark_conversation_active()

    def _gen() -> Iterator[str]:
        stt = engine.transcribe_audio_bytes(raw_bytes)
        transcript = stt["text"]
        if not transcript:
            yield _ndjson({"type": "ignored", "reason": "silence", "stt_ms": stt["latency_ms"]})
            return

        if is_self_acoustic_echo(transcript):
            add_server_log("ECHO", f"Suppressed acoustic feedback: '{transcript}'")
            yield _ndjson(
                {
                    "type": "ignored",
                    "reason": "self_acoustic_echo",
                    "transcript": transcript,
                    "stt_ms": stt["latency_ms"],
                }
            )
            return

        parsed = parse_wake_utterance(
            transcript,
            active_character="rocky",
            require_wake_word=(mode == "wake"),
            in_followup_window=(in_followup.lower() == "true"),
        )
        if not parsed["triggered"]:
            yield _ndjson(
                {
                    "type": "ignored",
                    "reason": "no_wake_word",
                    "transcript": transcript,
                    "stt_ms": stt["latency_ms"],
                }
            )
            return

        resp_spec = REGISTRY.get("rocky")
        resp_char = resp_spec.id
        def_tr, def_sp = engine.get_defaults(resp_char)
        tr_val = int(transpose) if transpose else def_tr
        sp_val = float(speed) if speed else def_sp

        add_server_log("TURN", f"Voice ({mode}): \"{transcript}\" ({stt['latency_ms']}ms STT)")
        yield _ndjson(
            {
                "type": "transcript",
                "character": resp_char,
                "transcript": transcript,
                "stt_ms": stt["latency_ms"],
            }
        )

        if parsed["wake_only"]:
            reply = random.choice(resp_spec.wake_replies)
            emo = detect_utterance_emotion(reply, character=resp_char)
            try:
                _body_speak_start(emo)
                wav_bytes, tts_ms = engine.synthesize_character_wav(
                    reply, character=resp_char, transpose=tr_val, speed=sp_val
                )
                record_spoken_sentence(reply)
                if play_host_speaker:
                    queue_pc_speaker(wav_bytes)
                    _speaker_queue.join()
            finally:
                _body_speak_stop()
            yield _ndjson(
                {
                    "type": "audio_chunk",
                    "character": resp_char,
                    "index": 0,
                    "sentence": reply,
                    "emotion": emo,
                    "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
                    "llm_ms": 0,
                    "voice_ms": tts_ms,
                }
            )
            yield _ndjson({"type": "done", "action": "wake_ack", "enter_followup": True})
            return

        if parsed["sleep"]:
            reply = resp_spec.sleep_reply
            try:
                _body_speak_start("sleepy")
                wav_bytes, tts_ms = engine.synthesize_character_wav(
                    reply, character=resp_char, transpose=tr_val, speed=sp_val
                )
                record_spoken_sentence(reply)
                if play_host_speaker:
                    queue_pc_speaker(wav_bytes)
                    _speaker_queue.join()
            finally:
                _body_speak_stop()
            yield _ndjson(
                {
                    "type": "audio_chunk",
                    "character": resp_char,
                    "index": 0,
                    "sentence": reply,
                    "emotion": "sleepy",
                    "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
                    "llm_ms": 0,
                    "voice_ms": tts_ms,
                }
            )
            yield _ndjson({"type": "done", "action": "sleep", "enter_followup": False})
            return

        if parsed["reset"]:
            brain.reset_history(character=resp_char)
            reply = resp_spec.reset_reply
            emo = detect_utterance_emotion(reply, character=resp_char)
            try:
                _body_speak_start(emo)
                wav_bytes, tts_ms = engine.synthesize_character_wav(
                    reply, character=resp_char, transpose=tr_val, speed=sp_val
                )
                record_spoken_sentence(reply)
                if play_host_speaker:
                    queue_pc_speaker(wav_bytes)
                    _speaker_queue.join()
            finally:
                _body_speak_stop()
            yield _ndjson(
                {
                    "type": "audio_chunk",
                    "character": resp_char,
                    "index": 0,
                    "sentence": reply,
                    "emotion": emo,
                    "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
                    "llm_ms": 0,
                    "voice_ms": tts_ms,
                }
            )
            yield _ndjson({"type": "done", "action": "reset", "enter_followup": True})
            return

        turn_state = {"end_conversation": False}
        yield from _stream_llm_and_voice(
            parsed["query"],
            resp_char,
            filter_intensity,
            tr_val,
            sp_val,
            state_out=turn_state,
            play_host_speaker=play_host_speaker,
        )
        ended = turn_state["end_conversation"]
        yield _ndjson({"type": "done", "action": "sleep" if ended else "reply", "enter_followup": not ended})

    return StreamingResponse(_gen(), media_type="application/x-ndjson")


@app.post("/api/text-turn-stream")
def text_turn_stream(req: TextTurnRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty")

    robot_vision.mark_conversation_active()
    spec = REGISTRY.get("rocky")
    char_id = spec.id
    if req.say_exact:
        query_text = text
        wake_only = False
    else:
        parsed = parse_wake_utterance(
            text, active_character=char_id, require_wake_word=False
        )
        query_text = parsed["query"] or text
        wake_only = bool(parsed["wake_only"])

    def_tr, def_sp = engine.get_defaults(char_id)
    tr_val = def_tr if req.transpose is None else req.transpose
    sp_val = def_sp if req.speed is None else req.speed

    add_server_log("TURN", f"{'Exact TTS' if req.say_exact else 'Text'}: \"{text[:90]}\"")

    def _gen() -> Iterator[str]:
        yield _ndjson(
            {
                "type": "transcript",
                "character": char_id,
                "transcript": f"[Exact TTS] {text}" if req.say_exact else text,
                "stt_ms": 0,
            }
        )
        if req.say_exact:
            emo = detect_utterance_emotion(text, character=char_id)
            reply_text = apply_character_filter(
                text, character=char_id, intensity=req.filter_intensity
            )
            if emo == spec.ui.get("idle_mood", "neutral"):
                emo = detect_utterance_emotion(reply_text, character=char_id)
            try:
                _body_speak_start(emo)
                wav_bytes, tts_ms = engine.synthesize_character_wav(
                    reply_text, character=char_id, transpose=tr_val, speed=sp_val
                )
                record_spoken_sentence(reply_text)
                if req.play_host_speaker:
                    queue_pc_speaker(wav_bytes)
                    _speaker_queue.join()
            finally:
                _body_speak_stop()
            yield _ndjson(
                {
                    "type": "audio_chunk",
                    "character": char_id,
                    "index": 0,
                    "sentence": reply_text,
                    "emotion": emo,
                    "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
                    "llm_ms": 0,
                    "voice_ms": tts_ms,
                }
            )
        elif wake_only:
            reply = random.choice(spec.wake_replies)
            emo = detect_utterance_emotion(reply, character=char_id)
            try:
                _body_speak_start(emo)
                wav_bytes, tts_ms = engine.synthesize_character_wav(
                    reply, character=char_id, transpose=tr_val, speed=sp_val
                )
                record_spoken_sentence(reply)
                if req.play_host_speaker:
                    queue_pc_speaker(wav_bytes)
                    _speaker_queue.join()
            finally:
                _body_speak_stop()
            yield _ndjson(
                {
                    "type": "audio_chunk",
                    "character": char_id,
                    "index": 0,
                    "sentence": reply,
                    "emotion": emo,
                    "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
                    "llm_ms": 0,
                    "voice_ms": tts_ms,
                }
            )
        else:
            turn_state = {"end_conversation": False}
            yield from _stream_llm_and_voice(
                query_text,
                char_id,
                req.filter_intensity,
                tr_val,
                sp_val,
                state_out=turn_state,
                play_host_speaker=req.play_host_speaker,
            )
            ended = turn_state["end_conversation"]
            yield _ndjson({"type": "done", "action": "sleep" if ended else "reply", "enter_followup": not ended})
            return
        yield _ndjson({"type": "done", "action": "reply", "enter_followup": True})

    return StreamingResponse(_gen(), media_type="application/x-ndjson")


@app.post("/api/say-stream")
def say_stream(req: TextTurnRequest):
    """Exact-text Rocky TTS streaming endpoint."""
    req_copy = req.model_copy(update={"say_exact": True, "character": "rocky"})
    return text_turn_stream(req_copy)


@app.post("/api/direct-s2s")
async def direct_s2s(
    audio: UploadFile = File(...),
    character: str = Form("rocky"),
    transpose: int | None = Form(None),
):
    raw_bytes = await audio.read()
    wav_bytes, voice_ms = engine.convert_uploaded_audio_to_character(
        raw_bytes, character="rocky", transpose=transpose
    )
    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"X-Voice-Ms": str(voice_ms)},
    )


# =====================================================================
# ESP32-CAM Robot Edge Vision & Multimodal Bridge Endpoints
# =====================================================================
class RobotInspectRequest(BaseModel):
    character: str = "rocky"
    prompt: str = "Describe what you see right now."
    robot_ip: str | None = None
    filter_intensity: str = "balanced"
    play_host_speaker: bool = True


class RobotSaveRequest(BaseModel):
    robot_ip: str | None = None


class RobotFaceRequest(BaseModel):
    mood: str | None = None
    anim: str | None = None
    action: str | None = None
    seq: str | None = None
    pos: str | None = None
    gx: float | None = None
    gy: float | None = None
    speaking: bool | None = None
    text: str | None = None
    robot_ip: str | None = None


def _ask_ollama_vision(image_bytes: bytes, prompt: str, char_id: str = "rocky") -> str:
    """1-hop direct VLM inspection via RockyBrain (respects prompt, zero Hop-2 hallucination)."""
    reply = brain.inspect_image(
        image_bytes=image_bytes,
        prompt=prompt,
        character="rocky",
        apply_filter=False,
    )
    if reply:
        return reply
    return "Camera frame grabbed, friend, but optical brain momentarily quiet."


def _stream_speech_chunks(
    text: str,
    char_id: str = "rocky",
    filter_intensity: str = "balanced",
    tr_val: int = -5,
    sp_val: float = 1.18,
    play_host_speaker: bool = False,
) -> Iterator[str]:
    filtered = apply_character_filter(text, character="rocky", intensity=filter_intensity)
    yield _ndjson({
        "type": "transcript",
        "character": "rocky",
        "transcript": filtered,
        "stt_ms": 0,
    })

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", filtered) if s.strip()]
    if not sentences:
        sentences = [filtered]

    speaking_started = False
    try:
        for idx, sentence in enumerate(sentences):
            emo = detect_utterance_emotion(sentence, character="rocky")
            if not speaking_started:
                _body_speak_start(emo)
                speaking_started = True
            wav_bytes, voice_ms = engine.synthesize_character_wav(
                sentence, character="rocky", transpose=tr_val, speed=sp_val
            )
            record_spoken_sentence(sentence)
            if play_host_speaker:
                queue_pc_speaker(wav_bytes)
            yield _ndjson({
                "type": "audio_chunk",
                "character": "rocky",
                "index": idx,
                "sentence": sentence,
                "emotion": emo,
                "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
                "llm_ms": 0,
                "voice_ms": voice_ms,
            })
        if play_host_speaker:
            _speaker_queue.join()
    finally:
        _body_speak_stop()
    yield _ndjson({"type": "done", "action": "reply", "enter_followup": True})


@app.get("/api/robot/status")
async def robot_status(robot_ip: str | None = None) -> dict[str, Any]:
    client = get_robot_async_client()
    if robot_ip:
        try:
            resp = await client.get(f"http://{robot_ip}/status")
            if resp.status_code == 200:
                return {"online": True, "robot_ip": robot_ip, "data": resp.json()}
        except Exception:
            pass

    ip = await asyncio.to_thread(resolve_robot_ip)
    try:
        resp = await client.get(f"http://{ip}/status")
        if resp.status_code == 200:
            return {"online": True, "robot_ip": ip, "data": resp.json()}
    except Exception as exc:
        return {"online": False, "robot_ip": ip, "error": str(exc)}
    return {"online": False, "robot_ip": ip, "error": "Non-200 response from robot"}


@app.get("/api/robot/capture")
async def robot_capture(robot_ip: str | None = None):
    ip = robot_ip or await asyncio.to_thread(resolve_robot_ip)
    frame_bytes = await _fetch_cached_camera_frame(ip)
    if frame_bytes:
        return Response(
            content=frame_bytes,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    raise HTTPException(status_code=502, detail="Robot camera capture failed")


_LATEST_CAMERA_FRAME: bytes | None = None
_LAST_FRAME_TIME: float = 0.0
_ROBOT_ASYNC_CLIENT: httpx.AsyncClient | None = None


def get_robot_async_client() -> httpx.AsyncClient:
    global _ROBOT_ASYNC_CLIENT
    if _ROBOT_ASYNC_CLIENT is None or _ROBOT_ASYNC_CLIENT.is_closed:
        limits = httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=1.8)
        timeout = httpx.Timeout(connect=1.0, read=2.5, write=1.0, pool=1.0)
        _ROBOT_ASYNC_CLIENT = httpx.AsyncClient(limits=limits, timeout=timeout)
    return _ROBOT_ASYNC_CLIENT


_FRAME_LOCK: asyncio.Lock | None = None


async def _fetch_cached_camera_frame(ip: str) -> bytes | None:
    global _LATEST_CAMERA_FRAME, _LAST_FRAME_TIME, _FRAME_LOCK
    now = time.time()
    if _LATEST_CAMERA_FRAME is not None and (now - _LAST_FRAME_TIME) < 0.08:
        return _LATEST_CAMERA_FRAME

    cfg = robot_vision.load_camera_config()
    if cfg.get("source_type") in ("macbook", "webcam", "local"):
        up_bytes = robot_vision.get_latest_uploaded_frame_bytes(max_age=3.5)
        if up_bytes:
            _LATEST_CAMERA_FRAME = up_bytes
            _LAST_FRAME_TIME = now
            return up_bytes

    if _FRAME_LOCK is None:
        _FRAME_LOCK = asyncio.Lock()

    async with _FRAME_LOCK:
        now = time.time()
        if _LATEST_CAMERA_FRAME is not None and (now - _LAST_FRAME_TIME) < 0.08:
            return _LATEST_CAMERA_FRAME

        try:
            frame_bytes = await asyncio.to_thread(
                robot_vision.fetch_active_frame_bytes, ip, 0.075
            )
            if frame_bytes:
                _LATEST_CAMERA_FRAME = frame_bytes
                _LAST_FRAME_TIME = time.time()
                return frame_bytes
        except Exception:
            pass
        return _LATEST_CAMERA_FRAME


@app.get("/api/robot/stream")
async def robot_stream(robot_ip: str | None = None):
    ip = robot_ip
    if not ip or not _probe_ip(ip):
        ip = await asyncio.to_thread(resolve_robot_ip)

    async def _stream_generator():
        boundary = "frame"
        while True:
            try:
                frame_bytes = await _fetch_cached_camera_frame(ip)
                if frame_bytes:
                    header = (
                        f"--{boundary}\r\n"
                        f"Content-Type: image/jpeg\r\n"
                        f"Content-Length: {len(frame_bytes)}\r\n\r\n"
                    ).encode("ascii")
                    yield header + frame_bytes + b"\r\n"
                await asyncio.sleep(0.06)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.2)

    return StreamingResponse(
        _stream_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.post("/api/robot/save")
async def robot_save(req: RobotSaveRequest) -> dict[str, Any]:
    ip = req.robot_ip or _get_robot_ip_fast() or await asyncio.to_thread(resolve_robot_ip)
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(f"http://{ip}/save")
            add_server_log("VISION", f"Saved photo to SD on {ip}")
            return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to trigger SD card save: {exc}")


_CURRENT_FACE_STATE: dict[str, Any] = {
    "mood": "neutral",
    "anim": None,
    "action": None,
    "seq": None,
    "pos": "CENTER",
    "gx": 0.0,
    "gy": 0.0,
    "speaking": False,
    "text": None,
    "updated_at": time.time(),
}


@app.get("/api/robot/ping")
async def robot_ping(robot_ip: str | None = None) -> dict[str, Any]:
    ip = robot_ip if (robot_ip and _probe_ip(robot_ip)) else await asyncio.to_thread(resolve_robot_ip)
    try:
        t0 = time.time()
        client = get_robot_async_client()
        resp = await client.get(f"http://{ip}/ping")
        latency_ms = round((time.time() - t0) * 1000, 1)
        return {
            "online": resp.status_code == 200,
            "status_code": resp.status_code,
            "robot_ip": ip,
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        return {
            "online": False,
            "robot_ip": ip,
            "error": str(exc),
        }


@app.get("/api/robot/face/state")
def robot_face_state() -> dict[str, Any]:
    return dict(_CURRENT_FACE_STATE)


_ROBOT_FACE_LOCK = asyncio.Lock()


@app.post("/api/robot/face")
async def robot_face_post(req: RobotFaceRequest) -> dict[str, Any]:
    ip = req.robot_ip or _get_robot_ip_fast() or await asyncio.to_thread(resolve_robot_ip)
    payload: dict[str, Any] = {}
    if req.mood is not None:
        payload["mood"] = req.mood
        _CURRENT_FACE_STATE["mood"] = req.mood
    if req.anim is not None:
        payload["anim"] = req.anim
        _CURRENT_FACE_STATE["anim"] = req.anim
    if req.action is not None:
        payload["action"] = req.action
        _CURRENT_FACE_STATE["action"] = req.action
    if req.seq is not None:
        payload["seq"] = req.seq
        _CURRENT_FACE_STATE["seq"] = req.seq
    if req.pos is not None:
        payload["pos"] = req.pos
        _CURRENT_FACE_STATE["pos"] = req.pos
    if req.gx is not None:
        payload["gx"] = req.gx
        _CURRENT_FACE_STATE["gx"] = req.gx
    if req.gy is not None:
        payload["gy"] = req.gy
        _CURRENT_FACE_STATE["gy"] = req.gy
    if req.speaking is not None:
        payload["speaking"] = req.speaking
        _CURRENT_FACE_STATE["speaking"] = req.speaking
    if req.text is not None:
        payload["text"] = req.text
        _CURRENT_FACE_STATE["text"] = req.text
    _CURRENT_FACE_STATE["updated_at"] = time.time()

    if req.mood is not None or req.anim is not None or req.speaking is not None:
        add_server_log("FACE", f"POST /face {payload} -> {ip}")

    try:
        async with _ROBOT_FACE_LOCK:
            client = get_robot_async_client()
            resp = await client.post(f"http://{ip}/face", json=payload)
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return {"status": "ok", "robot_ip": ip, "state": _CURRENT_FACE_STATE}
        return {"status": "error", "code": resp.status_code, "robot_ip": ip, "state": _CURRENT_FACE_STATE}
    except Exception as exc:
        return {"status": "error", "message": str(exc), "robot_ip": ip, "state": _CURRENT_FACE_STATE}


@app.get("/api/robot/face")
async def robot_face_get(
    mood: str | None = None,
    anim: str | None = None,
    pos: str | None = None,
    text: str | None = None,
    robot_ip: str | None = None,
) -> dict[str, Any]:
    ip = robot_ip or _get_robot_ip_fast() or await asyncio.to_thread(resolve_robot_ip)
    params: dict[str, str] = {}
    if mood:
        params["mood"] = mood
        _CURRENT_FACE_STATE["mood"] = mood
    if anim:
        params["anim"] = anim
        _CURRENT_FACE_STATE["anim"] = anim
    if pos:
        params["pos"] = pos
        _CURRENT_FACE_STATE["pos"] = pos
    if text:
        params["text"] = text
        _CURRENT_FACE_STATE["text"] = text
    _CURRENT_FACE_STATE["updated_at"] = time.time()
    try:
        client = get_robot_async_client()
        resp = await client.get(f"http://{ip}/face", params=params)
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return {"status": "ok", "robot_ip": ip}
        return {"status": "error", "code": resp.status_code, "robot_ip": ip}
    except Exception as exc:
        return {"status": "error", "message": str(exc), "robot_ip": ip}


@app.post("/api/robot/inspect")
async def robot_inspect(req: RobotInspectRequest):
    ip = req.robot_ip or _get_robot_ip_fast() or await asyncio.to_thread(resolve_robot_ip)
    def_tr, def_sp = engine.get_defaults("rocky")

    img_bytes = await _fetch_cached_camera_frame(ip)
    if not img_bytes:
        raise HTTPException(status_code=502, detail=f"Could not acquire camera frame from {ip}")

    add_server_log("VISION", f"Scene inspect ({ip}): \"{req.prompt[:70]}\"")
    _body_thinking()
    reply_text = await asyncio.to_thread(_ask_ollama_vision, img_bytes, req.prompt, "rocky")

    return StreamingResponse(
        _stream_speech_chunks(
            reply_text,
            "rocky",
            req.filter_intensity,
            def_tr,
            def_sp,
            play_host_speaker=req.play_host_speaker,
        ),
        media_type="application/x-ndjson",
    )
