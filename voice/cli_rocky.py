#!/usr/bin/env python3
"""
cli_rocky.py — Generic Multi-Character Streaming Terminal Voice Assistant.
Automatically discovers all voices in `characters/*.json`!
"""

from __future__ import annotations

import argparse
from collections import deque
import io
import os
import queue
import random
import subprocess
import tempfile
import threading
import time

import numpy as np
import sounddevice as sd
import soundfile as sf

from brain import RockyBrain
from character_registry import REGISTRY
from rocky_filter import parse_wake_utterance
from voice_engine import RockyVoiceEngine


def play_wav_bytes(wav_bytes: bytes) -> None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        path = f.name
    try:
        subprocess.run(["afplay", path], check=False)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def speak_streaming_turn(engine: RockyVoiceEngine, brain: RockyBrain, query: str, char_id: str) -> bool:
    spec = REGISTRY.get(char_id)
    sent_q: queue.Queue[str | None] = queue.Queue()
    audio_q: queue.Queue[bytes | None] = queue.Queue()
    t0 = time.perf_counter()
    ended_state = {"ended": False}

    def _llm_worker() -> None:
        for item in brain.ask_stream(query, character=spec.id):
            if item.get("end_conversation"):
                ended_state["ended"] = True
            if not item.get("sentence"):
                continue
            print(f"{spec.name} > {item['sentence']}", flush=True)
            sent_q.put(item["sentence"])
        sent_q.put(None)

    def _tts_worker() -> None:
        first = True
        while True:
            s = sent_q.get()
            if s is None:
                audio_q.put(None)
                break
            wav_bytes, _ = engine.synthesize_character_wav(s, character=spec.id)
            if first:
                print(f"         [First audio ready in {(time.perf_counter() - t0):.2f}s]", flush=True)
                first = False
            audio_q.put(wav_bytes)

    threading.Thread(target=_llm_worker, daemon=True).start()
    threading.Thread(target=_tts_worker, daemon=True).start()

    while True:
        wav = audio_q.get()
        if wav is None:
            break
        play_wav_bytes(wav)
    return ended_state["ended"]


def main() -> None:
    choices = REGISTRY.ids()
    parser = argparse.ArgumentParser(description="Multi-Character Streaming Voice Assistant")
    parser.add_argument("--character", choices=choices, default=REGISTRY.default_id)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--vad", type=float, default=0.015)
    args = parser.parse_args()

    engine = RockyVoiceEngine()
    brain = RockyBrain()

    print(f"=== Initializing Characters: {', '.join(choices)} ===", flush=True)
    engine.load_all()
    brain.warmup()
    for spec in REGISTRY.all_specs():
        for p in spec.wake_replies:
            engine.synthesize_character_wav(p, character=spec.id)

    active_char = args.character
    sr = 16000
    block_size = 1024
    pre_roll: deque[np.ndarray] = deque(maxlen=7)
    speech_blocks: list[np.ndarray] = []
    followup_until = 0.0

    wake_hints = " / ".join(f"'{s.primary_wake_phrase}'" for s in REGISTRY.all_specs())
    print(f"\n[READY] Say {wake_hints} | Ctrl+C to quit.\n", flush=True)

    try:
        while True:
            speech_blocks.clear()
            pre_roll.clear()
            recording = False
            silence_blocks = 0

            with sd.InputStream(samplerate=sr, channels=1, dtype="float32", blocksize=block_size) as stream:
                while True:
                    block, _ = stream.read(block_size)
                    mono = block[:, 0].copy()
                    rms = float(np.sqrt(np.mean(mono * mono)))

                    if not recording:
                        pre_roll.append(mono)
                        if rms >= args.vad:
                            recording = True
                            speech_blocks.extend(pre_roll)
                            pre_roll.clear()
                            silence_blocks = 0
                    else:
                        speech_blocks.append(mono)
                        if rms < args.vad * 0.72:
                            silence_blocks += 1
                        else:
                            silence_blocks = 0
                        if silence_blocks >= 8 or len(speech_blocks) >= 200:
                            break

            if len(speech_blocks) < 6:
                continue

            audio = np.concatenate(speech_blocks)
            buf = io.BytesIO()
            sf.write(buf, audio, sr, subtype="PCM_16", format="WAV")
            stt = engine.transcribe_audio_bytes(buf.getvalue())
            if not stt["text"]:
                continue

            parsed = parse_wake_utterance(
                stt["text"],
                active_character=active_char,
                require_wake_word=(not args.continuous),
                in_followup_window=(time.time() < followup_until),
            )
            if not parsed["triggered"]:
                continue

            active_char = parsed["character"]
            spec = REGISTRY.get(active_char)
            print(f"\nYou ({stt['latency_ms']}ms) > {stt['text']}", flush=True)

            if parsed["wake_only"]:
                reply = random.choice(spec.wake_replies)
                print(f"{spec.name} > {reply}", flush=True)
                wav_bytes, _ = engine.synthesize_character_wav(reply, character=spec.id)
                play_wav_bytes(wav_bytes)
                followup_until = time.time() + 30.0
                continue

            if parsed["sleep"]:
                wav_bytes, _ = engine.synthesize_character_wav(spec.sleep_reply, character=spec.id)
                play_wav_bytes(wav_bytes)
                followup_until = 0.0
                continue

            if parsed["reset"]:
                brain.reset_history(character=spec.id)
                wav_bytes, _ = engine.synthesize_character_wav(spec.reset_reply, character=spec.id)
                play_wav_bytes(wav_bytes)
                followup_until = time.time() + 30.0
                continue

            ended = speak_streaming_turn(engine, brain, parsed["query"], spec.id)
            followup_until = 0.0 if ended else (time.time() + 30.0)

    except KeyboardInterrupt:
        print("\nGoodbye!", flush=True)


if __name__ == "__main__":
    main()
