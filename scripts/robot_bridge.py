#!/usr/bin/env python3
"""
Rocky-Robot AI Bridge & Persona Engine
Connects the ESP32-S3 Camera & SD Card robot to the local AI brain (rocky-voice / Ollama).

Features:
- Live frame capture from ESP32-S3 OV2640 camera (640x480 VGA)
- Telemetry monitoring (heap, PSRAM, uptime, SD status, RSSI)
- Multimodal Vision query via Ollama (e.g. moondream, llava, or llama3.2-vision)
- Synthesized voice playback through local rocky-voice server (TARS, Rocky, Marvin, BMO)
- Automatic audio playback via macOS afplay
- Remote trigger for saving snapshots to onboard SD card
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

ROBOT_DEFAULT_IP = "192.168.4.147"
BRAIN_DEFAULT_URL = "http://127.0.0.1:7860"
OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"

def get_robot_status(robot_ip: str) -> dict:
    url = f"http://{robot_ip}/status"
    req = urllib.request.Request(url, headers={"User-Agent": "RockyBridge/1.0"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))

def capture_robot_frame(robot_ip: str) -> bytes:
    url = f"http://{robot_ip}/capture"
    req = urllib.request.Request(url, headers={"User-Agent": "RockyBridge/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.read()

def trigger_sd_save(robot_ip: str) -> dict:
    url = f"http://{robot_ip}/save"
    req = urllib.request.Request(url, data=b"", headers={"User-Agent": "RockyBridge/1.0"}, method="POST")
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))

def query_vision_brain(image_bytes: bytes, prompt: str, character: str = "tars", ollama_url: str = OLLAMA_DEFAULT_URL) -> str:
    """Sends image frame to Ollama vision model (moondream) and translates into character persona."""
    b64_img = base64.b64encode(image_bytes).decode("utf-8")
    
    # 1. Vision Extraction via Moondream
    raw_vision = ""
    try:
        v_payload = {
            "model": "moondream:latest",
            "prompt": "Describe the main objects, environment, and visual details visible in this image concisely in 1 or 2 sentences.",
            "images": [b64_img],
            "stream": False
        }
        req_v = urllib.request.Request(
            f"{ollama_url}/api/generate",
            data=json.dumps(v_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_v, timeout=35) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_vision = data.get("response", "").strip()
    except Exception as err:
        print(f"[Vision Perception Notice] {err}")

    if not raw_vision:
        raw_vision = f"Camera feed operational. Live frame acquired at 640x480 ({len(image_bytes)} bytes)."

    # 2. Persona-Specific Robotic Vision Voice
    try:
        c_low = character.lower()
        if c_low == "rocky":
            chat_prompt = (
                f"You are Rocky, the Eridian alien engineer exploring on a mobile robot. Your robot optical camera sees: \"{raw_vision}\".\n"
                f"In 1 or 2 short enthusiastic sentences, voice what you see in telegraphic Rocky style (say 'Amaze!', drop 'the/a', say 'good good good', call human 'friend'). "
                f"Do NOT ask questions. Directly report the objects and colors you see. "
                f"Example: 'Amaze! Optical camera sees blue cup on counter! Good good good, friend!'"
            )
        elif c_low == "tars":
            chat_prompt = (
                f"You are TARS from Interstellar (humor 75%, honesty 90%). Your robot optical sensors detect: \"{raw_vision}\".\n"
                f"In 1 or 2 spoken sentences, report what you see to Cooper. "
                f"Example: 'Optical scan confirms a blue plastic cup on the countertop, Cooper. Nothing tactical, but it holds liquids.'"
            )
        elif c_low == "marvin":
            chat_prompt = (
                f"You are Marvin the Paranoid Android. Your robot optical camera detects: \"{raw_vision}\".\n"
                f"In 1 or 2 gloomy, cynical sentences, voice what you see. "
                f"Example: 'I rotated my optical sensors and observed a blue cup. How utterly depressing.'"
            )
        else:
            chat_prompt = (
                f"You are BMO from Adventure Time. Your robot camera sees: \"{raw_vision}\".\n"
                f"In 1 or 2 cheerful sentences, voice what you see. "
                f"Example: 'Yay! My camera sees a cute blue cup! It looks so happy sitting there!'"
            )

        c_payload = {
            "model": "qwen2.5:1.5b",
            "prompt": chat_prompt,
            "stream": False,
            "options": {"temperature": 0.4, "num_predict": 70}
        }
        req_c = urllib.request.Request(
            f"{ollama_url}/api/generate",
            data=json.dumps(c_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req_c, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            reply = data.get("response", "").strip()
            if reply:
                return reply
    except Exception as err:
        print(f"[Persona Transform Notice] {err}")

    # Fallback
    if character.lower() == "tars":
        return f"Optical scan confirms: {raw_vision}. All systems nominal, Cooper."
    elif character.lower() == "rocky":
        return f"Amaze! Camera sees: {raw_vision}! Good good good!"
    elif character.lower() == "marvin":
        return f"I looked through the camera. It shows {raw_vision}. Just as depressing as I expected."
    else:
        return f"Yay! Camera snap sees: {raw_vision}!"

def speak_character(text: str, character: str = "tars", brain_url: str = BRAIN_DEFAULT_URL, play_audio: bool = True) -> bool:
    """Synthesizes text into character voice using local rocky-voice server and plays via afplay."""
    payload = {
        "text": text,
        "character": character.lower(),
        "say_exact": True,
        "filter_intensity": "balanced"
    }

    req = urllib.request.Request(
        f"{brain_url}/api/text-turn-stream",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            for line in resp:
                if not line.strip():
                    continue
                chunk = json.loads(line.decode("utf-8"))
                if chunk.get("type") == "audio_chunk" and "audio_b64" in chunk:
                    wav_data = base64.b64decode(chunk["audio_b64"])
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                        tf.write(wav_data)
                        tmp_path = tf.name

                    if play_audio and sys.platform == "darwin":
                        subprocess.run(["afplay", tmp_path], check=False)
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                    return True
    except Exception as exc:
        print(f"[TTS Synthesis Error] Could not synthesize speech: {exc}")
        return False
    return False

def main():
    parser = argparse.ArgumentParser(description="Rocky-Robot AI Edge Bridge")
    parser.add_argument("--robot", default=ROBOT_DEFAULT_IP, help=f"IP address of ESP32-S3 robot (default: {ROBOT_DEFAULT_IP})")
    parser.add_argument("--character", default="tars", choices=["tars", "rocky", "marvin", "bmo"], help="AI persona")
    parser.add_argument("--prompt", default="Describe what you see right now.", help="Observation prompt")
    parser.add_argument("--snap-sd", action="store_true", help="Trigger SD card photo snapshot")
    parser.add_argument("--status", action="store_true", help="Fetch robot telemetry")
    parser.add_argument("--save-local", help="Save the captured frame to a local file path (e.g. /tmp/frame.jpg)")
    parser.add_argument("--no-speak", action="store_true", help="Disable voice audio playback")
    parser.add_argument("--loop", type=int, default=0, help="Run in a loop every N seconds")
    args = parser.parse_args()

    if args.status:
        try:
            status = get_robot_status(args.robot)
            print("\n========== Rocky-S3 Robot Telemetry ==========")
            print(f"Robot Name:   {status.get('robot')}")
            print(f"Uptime:       {status.get('uptime_sec')} seconds")
            print(f"Free Heap:    {status.get('free_heap', 0) // 1024} KB")
            print(f"Free PSRAM:   {status.get('free_psram', 0) / (1024*1024):.2f} MB")
            print(f"SD Card:      {'Mounted (' + str(status.get('sd_total_mb')) + ' MB)' if status.get('sd_mounted') else 'Not Mounted (Insert FAT32 SD card)'}")
            print(f"Wi-Fi RSSI:   {status.get('wifi_rssi')} dBm")
            print("==============================================\n")
            return
        except Exception as exc:
            print(f"Failed to fetch status from {args.robot}: {exc}")
            sys.exit(1)

    if args.snap_sd:
        try:
            res = trigger_sd_save(args.robot)
            print(f"Snapshot saved to SD Card: {res.get('file')} ({res.get('bytes')} bytes)")
            return
        except Exception as exc:
            print(f"Failed to trigger SD save: {exc}")
            sys.exit(1)

    # Capture frame & execute AI bridge
    iteration = 0
    while True:
        iteration += 1
        try:
            print(f"[{iteration}] Acquiring frame from http://{args.robot}/capture ...")
            t0 = time.time()
            frame = capture_robot_frame(args.robot)
            elapsed = (time.time() - t0) * 1000
            print(f"Frame received: {len(frame)} bytes in {elapsed:.1f}ms")

            if args.save_local:
                with open(args.save_local, "wb") as f:
                    f.write(frame)
                print(f"Frame written to {args.save_local}")

            print(f"Querying AI Vision Brain ({args.character.upper()})...")
            reply = query_vision_brain(frame, args.prompt, character=args.character)
            print(f"\n💬 [{args.character.upper()}]: \"{reply}\"\n")

            if not args.no_speak:
                print(f"Synthesizing {args.character.upper()} voice via rocky-voice...")
                speak_character(reply, character=args.character, play_audio=True)

        except Exception as exc:
            print(f"Bridge error: {exc}")

        if args.loop <= 0:
            break
        time.sleep(args.loop)

if __name__ == "__main__":
    main()
