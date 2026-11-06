"""
Sci-Fi Comms Console — Generic Plug-and-Play Multi-Character Voice Server.

Discovers characters dynamically from `characters/*.json` via `character_registry.REGISTRY`,
exposes `POST /api/characters/reload` for zero-downtime hot-loading of newly added voices,
and streams NDJSON audio + emotion chunks in parallel!
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterator
from contextlib import asynccontextmanager
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import mcp_homeassistant
import memory
import robot_vision
from add_voice import install_character_pack, remove_character_pack
from brain import RockyBrain, resolve_robot_ip, _probe_ip
from character_registry import REGISTRY
from rocky_filter import (
    apply_character_filter,
    detect_utterance_emotion,
    parse_wake_utterance,
)
from voice_engine import RockyVoiceEngine

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(PROJECT_DIR, "static")

engine = RockyVoiceEngine()
brain = RockyBrain()
MODELS_READY = False

# Host PC/Mac Speaker Playback Queue
_speaker_queue: queue.Queue[bytes | None] = queue.Queue()

def _pc_speaker_loop() -> None:
    while True:
        wav_data = _speaker_queue.get()
        if wav_data is None:
            break
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                tf.write(wav_data)
                tpath = tf.name
            if sys.platform == "darwin":
                subprocess.run(["afplay", tpath], check=False)
            try:
                os.remove(tpath)
            except OSError:
                pass
        except Exception as exc:
            print(f"[PC Speaker Output Error] {exc}", flush=True)
        finally:
            _speaker_queue.task_done()

threading.Thread(target=_pc_speaker_loop, daemon=True, name="pc_speaker_worker").start()

def queue_pc_speaker(wav_data: bytes) -> None:
    """Queues synthesized speech to play directly out of the host PC/Mac speakers."""
    _speaker_queue.put(wav_data)


# -----------------------------------------------------------------------------
# Acoustic Feedback Echo Guard (Prevents mic from transcribing robot's own speaker audio)
# -----------------------------------------------------------------------------

_RECENT_SPOKEN_LOCK = threading.Lock()
_RECENT_SPOKEN_SENTENCES: list[tuple[float, str]] = []  # (timestamp, sentence)


def record_spoken_sentence(sentence: str) -> None:
    """Records speech outputted by Rocky/BMO so microphone feedback can be recognized and dropped."""
    clean = sentence.strip()
    if not clean:
        return
    now = time.time()
    with _RECENT_SPOKEN_LOCK:
        _RECENT_SPOKEN_SENTENCES.append((now, clean))
        # Keep only the last 15 seconds of assistant speech
        cutoff = now - 15.0
        while _RECENT_SPOKEN_SENTENCES and _RECENT_SPOKEN_SENTENCES[0][0] < cutoff:
            _RECENT_SPOKEN_SENTENCES.pop(0)


def is_self_acoustic_echo(transcript: str, window_seconds: float = 12.0) -> bool:
    """Detects if microphone transcript is an acoustic echo of words recently spoken by the speakers."""
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

        # Short echo fragment (1-5 words) with majority overlap
        if len(t_words) <= 5 and match_ratio >= 0.50:
            return True
        # Any utterance with high overlap of the robot's recent speech
        if match_ratio >= 0.75:
            return True
        return False



def _prewarm_character_phrases(char_ids: list[str] | None = None) -> None:
    specs = [REGISTRY.get(cid) for cid in char_ids] if char_ids else REGISTRY.all_specs()
    for spec in specs:
        phrases = [
            *spec.wake_replies,
            *spec.common_openers,
            spec.sleep_reply,
            spec.reset_reply,
        ]
        for phrase in phrases:
            if phrase:
                try:
                    engine.synthesize_character_wav(phrase, character=spec.id)
                except Exception as exc:
                    print(f"[Server] Prewarm skipped for '{spec.id}': {exc}", flush=True)


def _background_init() -> None:
    global MODELS_READY
    try:
        engine.load_all()
        brain.warmup()
        default_id = REGISTRY.default_id
        _prewarm_character_phrases([default_id])
        MODELS_READY = True
        print(
            f"[Server] Ready with primary character '{default_id}'! All characters: {', '.join(REGISTRY.ids())}",
            flush=True,
        )
        remaining = [cid for cid in REGISTRY.ids() if cid != default_id]
        if remaining:
            threading.Thread(target=_prewarm_character_phrases, args=(remaining,), daemon=True).start()
    except Exception as exc:
        print(f"[Server] Startup error: {exc}", flush=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_background_init, daemon=True).start()
    yield


app = FastAPI(title="Generic Multi-Character Streaming Voice Assistant", lifespan=lifespan)
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

    while True:
        item = sent_q.get()
        if item is None:
            break
        if isinstance(item, Exception):
            yield _ndjson({"type": "error", "detail": str(item)})
            break

        if item.get("end_conversation") and state_out is not None:
            state_out["end_conversation"] = True

        if not item.get("sentence"):
            continue

        wav_bytes, voice_ms = engine.synthesize_character_wav(
            item["sentence"], character=resp_char, transpose=tr_val, speed=sp_val
        )
        record_spoken_sentence(item["sentence"])
        if play_host_speaker:
            queue_pc_speaker(wav_bytes)
        yield _ndjson(
            {
                "type": "audio_chunk",
                "character": resp_char,
                "index": item["index"],
                "sentence": item["sentence"],
                "emotion": detect_utterance_emotion(item["sentence"], character=resp_char),
                "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
                "llm_ms": item["clause_ms"],
                "voice_ms": voice_ms,
            }
        )

    if play_host_speaker:
        _speaker_queue.join()


@app.get("/")
def index():
    return FileResponse(
        os.path.join(STATIC_DIR, "index.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ready" if MODELS_READY else "loading",
        "ready": MODELS_READY,
        "llm_model": brain.model,
        "default_character": REGISTRY.default_id,
        "characters": REGISTRY.public_map(),
    }


@app.get("/api/characters")
def list_characters() -> dict[str, Any]:
    return {
        "default_character": REGISTRY.default_id,
        "characters": REGISTRY.public_map(),
    }


@app.post("/api/characters/reload")
def reload_characters() -> dict[str, Any]:
    prev_ids = set(REGISTRY.ids())
    ids = REGISTRY.reload()
    new_ids = [cid for cid in ids if cid not in prev_ids]
    if new_ids:
        threading.Thread(target=_prewarm_character_phrases, args=(new_ids,), daemon=True).start()
    return {
        "status": "reloaded",
        "characters": REGISTRY.public_map(),
        "added": new_ids,
    }


@app.post("/api/characters/add")
async def add_character_api(
    char_id: str = Form(...),
    name: str = Form(""),
    title: str = Form(""),
    emoji: str = Form("🎙️"),
    subtitle: str = Form(""),
    wake: str = Form(""),
    piper_voice: str = Form("en_US-ryan-medium"),
    model_url: str = Form(""),
    transpose: str = Form(""),
    speed: float = Form(1.02),
    style: str = Form("clean"),
    prompt: str = Form(""),
    accent: str = Form("#38bdf8"),
    mood: str = Form("neutral"),
    model_file: UploadFile | None = File(None),
    reference_file: UploadFile | None = File(None),
) -> dict[str, Any]:
    tmp_dir = tempfile.mkdtemp(prefix="ui_add_voice_")
    try:
        model_src: str | None = model_url.strip() or None
        if model_file is not None and model_file.filename:
            raw_model = await model_file.read()
            if raw_model:
                safe_name = os.path.basename(model_file.filename)
                local_model_path = os.path.join(tmp_dir, safe_name)
                with open(local_model_path, "wb") as f:
                    f.write(raw_model)
                model_src = local_model_path

        ref_path: str | None = None
        if reference_file is not None and reference_file.filename:
            raw_ref = await reference_file.read()
            if raw_ref:
                safe_ref = os.path.basename(reference_file.filename)
                ref_path = os.path.join(tmp_dir, safe_ref)
                with open(ref_path, "wb") as f:
                    f.write(raw_ref)

        tr_opt: int | None = int(transpose) if transpose.strip() != "" else None

        manifest = install_character_pack(
            char_id=char_id,
            name=name or None,
            title=title or None,
            emoji=emoji or "🎙️",
            subtitle=subtitle or None,
            wake=wake or None,
            piper_voice=piper_voice,
            model_source=model_src,
            reference_audio=ref_path,
            transpose=tr_opt,
            speed=speed,
            style=style,
            prompt=prompt or None,
            accent=accent or "#38bdf8",
            mood=mood or "neutral",
            notify_server=False,
        )
        cid = manifest["id"]
        REGISTRY.reload()
        threading.Thread(target=_prewarm_character_phrases, args=([cid],), daemon=True).start()
        return {
            "status": "added",
            "character_id": cid,
            "characters": REGISTRY.public_map(),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.delete("/api/characters/{char_id}")
def delete_character_api(char_id: str) -> dict[str, Any]:
    cid = char_id.strip().lower()
    if cid in ("rocky", "marvin", "bmo"):
        raise HTTPException(
            status_code=400,
            detail=f"'{cid}' is a built-in character pack (use CLI --remove if you really want to delete it).",
        )
    try:
        remove_character_pack(cid, notify_server=False)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    REGISTRY.reload()
    return {
        "status": "removed",
        "removed": cid,
        "default_character": REGISTRY.default_id,
        "characters": REGISTRY.public_map(),
    }


@app.post("/api/reset")
def reset_memory(character: str | None = None) -> dict[str, str]:
    brain.reset_history(character=character)
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


# -----------------------------------------------------------------------------
# Robot Vision & Motion Sentry Endpoints
# -----------------------------------------------------------------------------

@app.get("/api/vision/status")
def vision_status() -> dict[str, Any]:
    return {
        "status": robot_vision.GUARD_ENGINE.last_status,
        "config": robot_vision.load_camera_config(),
        "enrolled_faces": robot_vision.list_enrolled_faces(),
    }


@app.post("/api/vision/config")
def vision_config(payload: dict[str, Any]) -> dict[str, Any]:
    robot_vision.save_camera_config(payload)
    return {
        "status": "saved",
        "config": robot_vision.load_camera_config(),
    }


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
    return {"success": ok, "message": msg, "enrolled_faces": robot_vision.list_enrolled_faces()}


@app.delete("/api/vision/faces/{name}")
def vision_delete_face(name: str) -> dict[str, Any]:
    ok = robot_vision.delete_enrolled_face(name)
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
            print(f"[Echo Guard] Suppressed microphone acoustic feedback: '{transcript}'", flush=True)
            yield _ndjson(
                {
                    "type": "ignored",
                    "reason": "self_acoustic_echo",
                    "transcript": transcript,
                    "stt_ms": stt["latency_ms"],
                }
            )
            return

        active_spec = REGISTRY.get(character)
        parsed = parse_wake_utterance(
            transcript,
            active_character=active_spec.id,
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

        resp_spec = REGISTRY.get(parsed["character"])
        resp_char = resp_spec.id
        if resp_char != active_spec.id or not transpose:
            tr_val, sp_val = engine.get_defaults(resp_char)
        else:
            tr_val = int(transpose)
            sp_val = float(speed) if speed else engine.get_defaults(resp_char)[1]

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
            wav_bytes, tts_ms = engine.synthesize_character_wav(
                reply, character=resp_char, transpose=tr_val, speed=sp_val
            )
            record_spoken_sentence(reply)
            if play_host_speaker:
                queue_pc_speaker(wav_bytes)
                _speaker_queue.join()
            yield _ndjson(
                {
                    "type": "audio_chunk",
                    "character": resp_char,
                    "index": 0,
                    "sentence": reply,
                    "emotion": detect_utterance_emotion(reply, character=resp_char),
                    "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
                    "llm_ms": 0,
                    "voice_ms": tts_ms,
                }
            )
            yield _ndjson({"type": "done", "action": "wake_ack", "enter_followup": True})
            return

        if parsed["sleep"]:
            reply = resp_spec.sleep_reply
            wav_bytes, tts_ms = engine.synthesize_character_wav(
                reply, character=resp_char, transpose=tr_val, speed=sp_val
            )
            record_spoken_sentence(reply)
            if play_host_speaker:
                queue_pc_speaker(wav_bytes)
                _speaker_queue.join()
            yield _ndjson(
                {
                    "type": "audio_chunk",
                    "character": resp_char,
                    "index": 0,
                    "sentence": reply,
                    "emotion": "sad",
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
            wav_bytes, tts_ms = engine.synthesize_character_wav(
                reply, character=resp_char, transpose=tr_val, speed=sp_val
            )
            record_spoken_sentence(reply)
            if play_host_speaker:
                queue_pc_speaker(wav_bytes)
                _speaker_queue.join()
            yield _ndjson(
                {
                    "type": "audio_chunk",
                    "character": resp_char,
                    "index": 0,
                    "sentence": reply,
                    "emotion": detect_utterance_emotion(reply, character=resp_char),
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
    active_spec = REGISTRY.get(req.character)
    if req.say_exact:
        spec = active_spec
        char_id = spec.id
        query_text = text
        wake_only = False
    else:
        parsed = parse_wake_utterance(
            text, active_character=active_spec.id, require_wake_word=False
        )
        spec = REGISTRY.get(parsed["character"])
        char_id = spec.id
        query_text = parsed["query"] or text
        wake_only = bool(parsed["wake_only"])

    def_tr, def_sp = engine.get_defaults(char_id)
    if char_id != active_spec.id:
        tr_val, sp_val = def_tr, def_sp
    else:
        tr_val = def_tr if req.transpose is None else req.transpose
        sp_val = def_sp if req.speed is None else req.speed

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
            wav_bytes, tts_ms = engine.synthesize_character_wav(
                reply_text, character=char_id, transpose=tr_val, speed=sp_val
            )
            if req.play_host_speaker:
                queue_pc_speaker(wav_bytes)
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
            wav_bytes, tts_ms = engine.synthesize_character_wav(
                reply, character=char_id, transpose=tr_val, speed=sp_val
            )
            if req.play_host_speaker:
                queue_pc_speaker(wav_bytes)
            yield _ndjson(
                {
                    "type": "audio_chunk",
                    "character": char_id,
                    "index": 0,
                    "sentence": reply,
                    "emotion": detect_utterance_emotion(reply, character=char_id),
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


@app.post("/api/direct-s2s")
async def direct_s2s(
    audio: UploadFile = File(...),
    character: str = Form("rocky"),
    transpose: int | None = Form(None),
):
    raw_bytes = await audio.read()
    wav_bytes, voice_ms = engine.convert_uploaded_audio_to_character(
        raw_bytes, character=character, transpose=transpose
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


def _ask_ollama_vision(image_bytes: bytes, prompt: str, char_id: str) -> str:
    b64_img = base64.b64encode(image_bytes).decode("utf-8")
    spec = REGISTRY.get(char_id)

    raw_vision = ""
    try:
        with httpx.Client(timeout=httpx.Timeout(40.0, connect=6.0)) as client:
            r = client.get("http://127.0.0.1:11434/api/tags")
            if r.status_code == 200:
                model_names = [m.get("name", "") for m in r.json().get("models", [])]
                v_model = next((name for name in model_names if any(k in name for k in ["moondream", "llava", "llama3.2-vision", "bakllava"])), None)
                if v_model:
                    # Step 1: Use Moondream/vision model to extract factual visual observations
                    r_gen = client.post(
                        "http://127.0.0.1:11434/api/generate",
                        json={
                            "model": v_model,
                            "prompt": "Identify and describe what is directly in front of the camera, noting objects, colors, and items clearly.",
                            "images": [b64_img],
                            "stream": False,
                        },
                        timeout=35.0,
                    )
                    if r_gen.status_code == 200:
                        raw_vision = r_gen.json().get("response", "").strip()
    except Exception as exc:
        print(f"[Vision Ollama error] {exc}", flush=True)

    if not raw_vision:
        raw_vision = "Camera feed operational. Live frame acquired at 640x480."
    else:
        memory.remember_vision(raw_vision, character=char_id)

    # Step 2: Use persona-specific robotic vision instructions
    try:
        if char_id == "rocky":
            char_instruction = (
                f"You are Rocky, the Eridian alien engineer exploring on a mobile robot. Your robot optical camera sees: \"{raw_vision}\".\n"
                f"In 1 or 2 short enthusiastic sentences, voice what you see in telegraphic Rocky style (say 'Amaze!', drop 'the/a', say 'good good good', call human 'friend'). "
                f"Do NOT ask questions. Directly report the objects and colors you see. "
                f"Example: 'Amaze! Optical camera sees blue cup on counter! Good good good, friend!'"
            )
        elif char_id == "tars":
            char_instruction = (
                f"You are TARS from Interstellar (humor 75%, honesty 90%). Your robot optical sensors detect: \"{raw_vision}\".\n"
                f"In 1 or 2 spoken sentences, report what you see to Cooper. "
                f"Example: 'Optical scan confirms a blue plastic cup on the countertop, Cooper. Nothing tactical, but it holds liquids.'"
            )
        elif char_id == "marvin":
            char_instruction = (
                f"You are Marvin the Paranoid Android. Your robot optical camera detects: \"{raw_vision}\".\n"
                f"In 1 or 2 gloomy, cynical sentences, voice what you see. "
                f"Example: 'I rotated my optical sensors and observed a blue cup. How utterly depressing.'"
            )
        else:
            char_instruction = (
                f"You are BMO from Adventure Time. Your robot camera sees: \"{raw_vision}\".\n"
                f"In 1 or 2 cheerful sentences, voice what you see. "
                f"Example: 'Yay! My camera sees a cute blue cup! It looks so happy sitting there!'"
            )

        with httpx.Client(timeout=httpx.Timeout(30.0, connect=6.0)) as client:
            r_chat = client.post(
                "http://127.0.0.1:11434/api/generate",
                json={
                    "model": brain.model,
                    "prompt": char_instruction,
                    "stream": False,
                    "options": {"temperature": 0.4, "num_predict": 70},
                },
                timeout=25.0,
            )
            if r_chat.status_code == 200:
                reply = r_chat.json().get("response", "").strip()
                if reply:
                    return reply
    except Exception as exc:
        print(f"[Persona Transform error] {exc}", flush=True)

    # Fallback to direct styled phrasing
    if char_id == "tars":
        return f"Optical scan confirms: {raw_vision}, Cooper."
    elif char_id == "rocky":
        return f"Amaze! Camera sees {raw_vision}! Good good good, friend!"
    elif char_id == "marvin":
        return f"I looked at the camera feed. It shows {raw_vision}. Just as depressing as I expected."
    else:
        return f"Yay! Camera sees {raw_vision}!"


def _stream_speech_chunks(
    text: str,
    char_id: str,
    filter_intensity: str = "balanced",
    tr_val: int = 0,
    sp_val: float = 1.0,
    play_host_speaker: bool = False,
) -> Iterator[str]:
    filtered = apply_character_filter(text, character=char_id, intensity=filter_intensity)
    yield _ndjson({
        "type": "transcript",
        "character": char_id,
        "transcript": filtered,
        "stt_ms": 0,
    })

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", filtered) if s.strip()]
    if not sentences:
        sentences = [filtered]

    for idx, sentence in enumerate(sentences):
        emo = detect_utterance_emotion(sentence, character=char_id)
        wav_bytes, voice_ms = engine.synthesize_character_wav(
            sentence, character=char_id, transpose=tr_val, speed=sp_val
        )
        if play_host_speaker:
            queue_pc_speaker(wav_bytes)
        yield _ndjson({
            "type": "audio_chunk",
            "character": char_id,
            "index": idx,
            "sentence": sentence,
            "emotion": emo,
            "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
            "llm_ms": 0,
            "voice_ms": voice_ms,
        })
    yield _ndjson({"type": "done", "action": "reply", "enter_followup": True})


@app.get("/api/robot/status")
async def robot_status(robot_ip: str | None = None) -> dict[str, Any]:
    if robot_ip:
        try:
            async with httpx.AsyncClient(timeout=1.2) as client:
                resp = await client.get(f"http://{robot_ip}/status")
                if resp.status_code == 200:
                    return {"online": True, "robot_ip": robot_ip, "data": resp.json()}
        except Exception:
            pass

    ip = resolve_robot_ip()
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"http://{ip}/status")
            if resp.status_code == 200:
                return {"online": True, "robot_ip": ip, "data": resp.json()}
    except Exception as exc:
        return {"online": False, "robot_ip": ip, "error": str(exc)}
    return {"online": False, "robot_ip": ip, "error": "Non-200 response from robot"}


@app.get("/api/robot/capture")
async def robot_capture(robot_ip: str | None = None):
    ip = robot_ip
    if ip:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"http://{ip}/capture")
                if resp.status_code == 200:
                    return Response(
                        content=resp.content,
                        media_type="image/jpeg",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
                    )
        except Exception:
            pass

    ip = resolve_robot_ip()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"http://{ip}/capture")
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Robot returned HTTP {resp.status_code}")
            return Response(
                content=resp.content,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Robot camera capture failed: {exc}")


_LATEST_CAMERA_FRAME: bytes | None = None
_LAST_FRAME_TIME: float = 0.0


async def _fetch_cached_camera_frame(ip: str) -> bytes | None:
    global _LATEST_CAMERA_FRAME, _LAST_FRAME_TIME
    now = time.time()
    # Cache for 70ms (~14 FPS) to prevent DMA/PSRAM starvation on ESP32
    if _LATEST_CAMERA_FRAME is not None and (now - _LAST_FRAME_TIME) < 0.07:
        return _LATEST_CAMERA_FRAME

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"http://{ip}/capture")
            if resp.status_code == 200 and resp.content:
                _LATEST_CAMERA_FRAME = resp.content
                _LAST_FRAME_TIME = now
                return resp.content
    except Exception:
        pass
    return _LATEST_CAMERA_FRAME


@app.get("/api/robot/stream")
async def robot_stream(robot_ip: str | None = None):
    ip = robot_ip
    if not ip or not _probe_ip(ip):
        ip = resolve_robot_ip()

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
        }
    )


@app.post("/api/robot/save")
async def robot_save(req: RobotSaveRequest) -> dict[str, Any]:
    ip = req.robot_ip or resolve_robot_ip()
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(f"http://{ip}/save")
            return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to trigger SD card save: {exc}")


@app.post("/api/robot/inspect")
async def robot_inspect(req: RobotInspectRequest):
    ip = req.robot_ip or resolve_robot_ip()
    char_id = req.character.lower()
    def_tr, def_sp = engine.get_defaults(char_id)

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(f"http://{ip}/capture")
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Robot returned status {resp.status_code}")
            img_bytes = resp.content
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not connect to camera at {ip}: {exc}")

    reply_text = _ask_ollama_vision(img_bytes, req.prompt, char_id)

    return StreamingResponse(
        _stream_speech_chunks(
            reply_text,
            char_id,
            req.filter_intensity,
            def_tr,
            def_sp,
            play_host_speaker=req.play_host_speaker,
        ),
        media_type="application/x-ndjson",
    )

