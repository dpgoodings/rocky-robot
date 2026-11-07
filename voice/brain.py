"""
Generic Multi-Character Streaming Local Brain — Powered by local Ollama (qwen2.5:1.5b).

Reads character `system_prompt` and `fallback_reply` dynamically from
`character_registry.REGISTRY` so adding a JSON file in `characters/` automatically
gives the new character its own persona and conversation history!
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import datetime
import json
import os
import re
import subprocess
import time
from typing import Any

import httpx

import memory
import robot_vision
from character_registry import REGISTRY
from mcp_homeassistant import detect_and_execute_hass_intent
from mcp_mac import detect_and_execute_mac_control
from rocky_filter import apply_character_filter

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
OLLAMA_BIN = os.path.join(PROJECT_DIR, "bin", "ollama")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("ROCKY_LLM_MODEL", "qwen2.5:1.5b")

TIME_QUERY_RE = re.compile(
    r"\b(?:time|date|day|today|tonight|tomorrow|month|year|clock|hour|morning|afternoon|evening)\b",
    re.IGNORECASE,
)
FACE_RECOG_QUERY_RE = re.compile(
    r"\b(?:who\s+(?:am\s+i|do\s+you\s+see|is\s+this|is\s+in\s+front)|recognize\s+(?:me|my\s+face)|do\s+you\s+know\s+(?:who\s+i\s+am|me))\b",
    re.IGNORECASE,
)
FACE_EXPR_QUERY_RE = re.compile(
    r"\b(?:expression|facial\s+expression|smile|smiling|frown|frowning|how\s+do\s+i\s+look|what\s+do\s+i\s+look\s+like|am\s+i\s+smiling|happy|sad|mood|how\s+am\s+i\s+looking)\b",
    re.IGNORECASE,
)
FACE_ENROLL_QUERY_RE = re.compile(
    r"\b(?:remember\s+my\s+face|learn\s+my\s+face|register\s+my\s+face|this\s+is\s+my\s+face)\b",
    re.IGNORECASE,
)
MOTION_GUARD_RE = re.compile(
    r"\b(?:(turn\s+on|enable|start)\s+(?:guard\s+mode|motion\s+(?:detection|guard)|watching)|(turn\s+off|disable|stop)\s+(?:guard\s+mode|motion\s+(?:detection|guard)|watching))\b",
    re.IGNORECASE,
)
VISION_QUERY_RE = re.compile(
    r"\b(?:what\s+do\s+you\s+see|what(?:'s|\s+is)\s+(?:in\s+front|there|that)|describe\s+(?:what\s+you\s+see|the\s+room|the\s+view)|(?:look|look\s+at)\s+(?:this|around|ahead)|can\s+you\s+see\s+(?:what|this|anything)|what\s+am\s+i\s+holding)\b",
    re.IGNORECASE,
)
CAMERA_SWITCH_RE = re.compile(
    r"\b(?:switch|change|use|turn\s+on)\s+(?:to\s+)?(?:the\s+)?(macbook|laptop|built-?in|webcam|esp32|robot|chassis)\s+camera\b",
    re.IGNORECASE,
)

_CACHED_ROBOT_IP: str | None = None


def _probe_ip(ip: str) -> bool:
    try:
        with httpx.Client(timeout=0.35) as c:
            r = c.get(f"http://{ip}/status")
            return r.status_code == 200 and ("robot" in r.text or "Rocky" in r.text)
    except Exception:
        return False


def resolve_robot_ip() -> str:
    global _CACHED_ROBOT_IP
    if _CACHED_ROBOT_IP and _probe_ip(_CACHED_ROBOT_IP):
        return _CACHED_ROBOT_IP

    env_ip = os.environ.get("ROBOT_HOST")
    if env_ip and _probe_ip(env_ip):
        _CACHED_ROBOT_IP = env_ip
        return env_ip

    # Check saved camera config
    try:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r") as f:
                cfg = json.load(f)
                saved_ip = cfg.get("esp32_ip")
                if saved_ip and _probe_ip(saved_ip):
                    _CACHED_ROBOT_IP = saved_ip
                    return saved_ip
    except Exception:
        pass

    # High-probability DHCP candidates
    candidates = ["192.168.4.173", "192.168.4.147", "192.168.4.136", "192.168.4.100", "192.168.4.150"]
    for c in candidates:
        if _probe_ip(c):
            _CACHED_ROBOT_IP = c
            return c

    # Check ARP table for Espressif MAC OUI
    try:
        out = subprocess.check_output(["arp", "-a"], text=True)
        for line in out.splitlines():
            if any(oui in line.lower() for oui in ["84:c7:bb", "34:85:18", "24:4c:ab", "ec:da:3b", "40:22:d8", "a0:76:4e", "30:ae:a4"]):
                m = re.search(r"\((192\.168\.\d+\.\d+)\)", line)
                if m and _probe_ip(m.group(1)):
                    _CACHED_ROBOT_IP = m.group(1)
                    return _CACHED_ROBOT_IP
    except Exception:
        pass

    # Concurrent subnet scan of common DHCP range (100 to 205)
    import concurrent.futures

    def check_candidate(host: str) -> str | None:
        return host if _probe_ip(host) else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as ex:
        probe_hosts = [f"192.168.4.{i}" for i in range(100, 205)]
        for result in ex.map(check_candidate, probe_hosts):
            if result:
                _CACHED_ROBOT_IP = result
                try:
                    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_config.json")
                    cfg = {}
                    if os.path.exists(cfg_path):
                        with open(cfg_path, "r") as f:
                            cfg = json.load(f)
                    cfg["esp32_ip"] = result
                    with open(cfg_path, "w") as f:
                        json.dump(cfg, f, indent=2)
                except Exception:
                    pass
                return result

    return _CACHED_ROBOT_IP or "192.168.4.173"


def fetch_robot_vision(timeout: float = 25.0) -> str | None:
    """Grabs a live frame from active camera (MacBook Pro or ESP32) and queries Moondream vision model."""
    try:
        import cv2
        frame = robot_vision.fetch_live_camera_frame()
        if frame is None:
            return None
        success, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            return None
        img_b64 = base64.b64encode(enc.tobytes()).decode("utf-8")

        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": "moondream:latest",
                    "prompt": "Identify and describe what is directly in front of the camera, noting objects, colors, and items clearly.",
                    "images": [img_b64],
                    "stream": False,
                },
            )
            if r.status_code == 200:
                return r.json().get("response", "").strip()
    except Exception as exc:
        print(f"[Brain Vision Notice] {exc}", flush=True)
    return None
SENTENCE_SPLIT_RE = re.compile(r"(?<!\bMr)(?<!\bDr)(?<!\bMs)(?<!\bSt)(?<!\b[A-Z])([.!?]+)\s+")
CLAUSE_SPLIT_RE = re.compile(r"([,;:\u2014]+)\s+")
DATE_OR_TAG_AFTER_RE = re.compile(
    r"^(?:January|February|March|April|May|June|July|August|September|October|November|December|\d{4}\b|question\b|grace\b|friend\b)",
    re.IGNORECASE,
)
FAREWELL_USER_RE = re.compile(
    r"\b(?:bye|goodbye|see\s+ya|see\s+you\s+later|good\s*night|talk\s+to\s+you\s+later|catch\s+you\s+later|gotta\s+(?:go|run))\b",
    re.IGNORECASE,
)
FAREWELL_REPLY_RE = re.compile(
    r"\b(?:bye|goodbye|see\s+you|see\s+ya|take\s+care|good\s*night|catch\s+you|later)\b",
    re.IGNORECASE,
)


def _pop_next_chunk(buffer: str, idx: int) -> tuple[str, str] | None:
    # First chunk allows fast conversational openers ("Yes!", "Good!", "Amaze!") to pop immediately
    min_sent = 3 if idx == 0 else 14
    for m in SENTENCE_SPLIT_RE.finditer(buffer):
        cand = buffer[: m.end()].strip()
        if len(cand) >= min_sent:
            return cand, buffer[m.end() :]

    min_clause = 10 if idx == 0 else 42
    for m in CLAUSE_SPLIT_RE.finditer(buffer):
        cand = buffer[: m.end()].strip()
        after = buffer[m.end() :].strip()
        if len(cand) >= min_clause and len(after) >= 8 and not DATE_OR_TAG_AFTER_RE.match(after):
            return cand, buffer[m.end() :]

    return None


class RockyBrain:
    def __init__(self, model: str = DEFAULT_MODEL, max_turns: int = 4) -> None:
        self.model = model
        self.max_turns = max_turns
        self.histories: dict[str, list[dict[str, str]]] = {}
        # Pre-seed working memory from persistent SQLite store (ignoring duplicate loops)
        for cid in ["rocky", "tars", "marvin", "bmo"]:
            raw_prior = memory.load_recent_dialogue(cid, limit=max_turns * 2)
            # Filter consecutive duplicate assistant messages
            prior: list[dict[str, str]] = []
            for item in raw_prior:
                if (
                    prior
                    and item.get("role") == "assistant"
                    and prior[-1].get("role") == "assistant"
                    and item.get("content") == prior[-1].get("content")
                ):
                    continue
                prior.append(item)
            if prior:
                self.histories[cid] = prior
        self._client = httpx.Client(timeout=35.0)
        self._ollama_proc: subprocess.Popen[Any] | None = None

    def ensure_ollama_running(self) -> bool:
        if self._ping():
            return True

        bin_path = OLLAMA_BIN if os.path.exists(OLLAMA_BIN) else "ollama"
        env = os.environ.copy()
        env["OLLAMA_MAX_LOADED_MODELS"] = "1"
        env["OLLAMA_KEEP_ALIVE"] = "60m"
        log_file = open(os.path.join(PROJECT_DIR, "ollama.log"), "a")
        self._ollama_proc = subprocess.Popen(
            [bin_path, "serve"],
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        for _ in range(25):
            time.sleep(0.4)
            if self._ping():
                return True
        return False

    def _ping(self) -> bool:
        try:
            r = self._client.get(f"{OLLAMA_URL}/api/tags", timeout=1.5)
            return r.status_code == 200
        except Exception:
            return False

    def warmup(self) -> None:
        self.ensure_ollama_running()
        default_spec = REGISTRY.get(REGISTRY.default_id)
        try:
            self._client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": default_spec.system_prompt},
                        {"role": "user", "content": "Hi"},
                    ],
                    "stream": False,
                    "keep_alive": "60m",
                    "options": {"num_ctx": 1024, "num_predict": 2, "num_thread": 4},
                },
                timeout=30.0,
            )
        except Exception as exc:
            print(f"[Brain] Warmup warning: {exc}", flush=True)

    def reset_history(self, character: str | None = None) -> None:
        if character and character.lower() in self.histories:
            self.histories[character.lower()].clear()
            memory.clear_dialogue(character.lower())
        else:
            for h in self.histories.values():
                h.clear()
            memory.clear_dialogue()

    def _build_messages(self, user_text: str, char_id: str) -> list[dict[str, str]]:
        spec = REGISTRY.get(char_id)
        hist = self.histories.setdefault(spec.id, [])

        # Auto-extract facts and retrieve relevant episodic/semantic memories
        memory.extract_and_learn(user_text)
        mem_block = memory.build_memory_prompt_block(user_text)

        # Notify vision engine that user is actively speaking (suppresses unsolicited sentry alerts)
        robot_vision.mark_conversation_active()

        # Seamlessly inject real-time visual presence & facial expression into dialogue context
        vis_ctx = robot_vision.get_current_visual_context(max_age=3.0)
        sys_content = spec.system_prompt
        if mem_block:
            sys_content += f"\n\n{mem_block}"

        if vis_ctx.get("in_view"):
            name = vis_ctx.get("name")
            expr_desc = vis_ctx.get("expression_desc") or "looking attentive"
            expr_label = vis_ctx.get("expression") or "neutral"

            if name and name != "Unknown":
                sys_content += (
                    f"\n\n[Visual Camera Perception: Looking through your active robot camera, you see your friend {name} "
                    f"standing directly in front of you. Their facial expression is {expr_label} ({expr_desc}). "
                    f"You have active optical camera eyes. Naturally address {name} and react to their facial expression "
                    f"when relevant in your conversational persona. Never say you are blind or cannot see.]"
                )
            else:
                sys_content += (
                    f"\n\n[Visual Camera Perception: Looking through your active robot camera, you see someone in front of you. "
                    f"Their facial expression is {expr_label} ({expr_desc}). Their face is not yet enrolled in your memory. "
                    f"Never claim to be blind—your robot camera sensors are your eyes.]"
                )
        else:
            sys_content += (
                f"\n\n[Visual Camera Perception: Your robot camera is active, but no human face is directly in front of the lens right now. "
                f"Never claim to be blind—your camera sensors are active.]"
            )

        messages = [{"role": "system", "content": sys_content}]
        messages.extend(hist[-(self.max_turns * 2) :])

        hass_action = detect_and_execute_hass_intent(user_text)
        mac_action = detect_and_execute_mac_control(user_text) if not hass_action else None

        if hass_action:
            user_payload = (
                f"(Smart Home Action Executed in Home Assistant: \"{hass_action}\". "
                f"Directly confirm this action to friend in your authentic 1-2 sentence character persona.)\n{user_text}"
            )
        elif mac_action:
            user_payload = (
                f"(System Action Executed via macOS MCP server: \"{mac_action}\". "
                f"Directly confirm this action to friend in your authentic 1-2 sentence character persona.)\n{user_text}"
            )
        elif TIME_QUERY_RE.search(user_text):
            now = datetime.now()
            short_time = now.strftime("%I:%M %p").lstrip("0")
            date_str = now.strftime("%A, %B %d, %Y")
            user_payload = (
                f"(Live clock: {short_time}; date: {date_str}. Keep your 2-sentence character format; "
                f"only read out the full date if the user asked for the date.)\n{user_text}"
            )
        elif CAMERA_SWITCH_RE.search(user_text):
            m_cam = CAMERA_SWITCH_RE.search(user_text)
            target = m_cam.group(1).lower() if m_cam else "esp32"
            if any(k in target for k in ("macbook", "laptop", "built", "webcam")):
                robot_vision.save_camera_config({"source_type": "macbook"})
                action_desc = "Camera feed successfully switched to MacBook Pro FaceTime HD camera. Looking through your laptop's camera now."
            else:
                robot_vision.save_camera_config({"source_type": "esp32"})
                action_desc = "Camera feed successfully switched to ESP32 robot camera. Looking through the mobile robot's chassis camera now."
            user_payload = (
                f"(Vision System Action Executed: \"{action_desc}\". "
                f"Directly confirm this camera switch to friend in your authentic 1-2 sentence character persona.)\n{user_text}"
            )
        elif MOTION_GUARD_RE.search(user_text):
            m_guard = MOTION_GUARD_RE.search(user_text)
            enable = bool(m_guard.group(1)) if m_guard else True
            robot_vision.save_camera_config({"motion_guard_enabled": enable})
            action_desc = "Motion Guard sentry mode activated. Watching room for movement." if enable else "Motion Guard sentry mode deactivated."
            user_payload = (
                f"(Vision System Action Executed: \"{action_desc}\". "
                f"Directly confirm this action to friend in your authentic 1-2 sentence character persona.)\n{user_text}"
            )
        elif FACE_ENROLL_QUERY_RE.search(user_text):
            m_name = re.search(r"\b(?:name\s+is|i\s+am|i'm|call\s+me)\s+([A-Za-z]+)\b", user_text, re.I)
            user_name = m_name.group(1).strip().title() if m_name else ""
            if not user_name:
                facts = memory.get_facts()
                for f in facts:
                    if f.get("key") == "user_name":
                        user_name = f.get("value")
                        break
            if not user_name:
                user_name = "David"

            frame = robot_vision.fetch_live_camera_frame()
            if frame is not None:
                ok, msg = robot_vision.enroll_face(user_name, frame)
                if ok:
                    memory.set_fact("user_name", user_name, category="identity")
                    action_desc = f"Successfully enrolled and remembered {user_name}'s face in biometric memory."
                else:
                    action_desc = f"Could not register face: {msg}"
            else:
                action_desc = "Camera feed was unreachable. Ensure robot camera is powered and connected."
            user_payload = (
                f"(Vision System Action Executed: \"{action_desc}\". "
                f"Directly confirm this action to friend in your authentic 1-2 sentence character persona.)\n{user_text}"
            )
        elif FACE_EXPR_QUERY_RE.search(user_text):
            vis = robot_vision.get_current_visual_context(max_age=1.5)
            if vis.get("in_view"):
                name = vis.get("name") if vis.get("name") != "Unknown" else "friend"
                expr_desc = vis.get("expression_desc") or "looking attentive"
                expr_label = vis.get("expression") or "neutral"
                user_payload = (
                    f"(User asks about their facial expression or appearance. Through your active camera eyes, you clearly see {name} "
                    f"and their facial expression is {expr_label} ({expr_desc}). Directly and warmly tell {name} what facial expression "
                    f"you see them making in your authentic 1-2 sentence character persona. Never claim to be blind.)\n{user_text}"
                )
            else:
                user_payload = (
                    f"(User asks about their expression or appearance. Through your camera eyes, no human face is centered in view right now. "
                    f"Politely tell them in your character persona to look into your camera.)\n{user_text}"
                )
        elif FACE_RECOG_QUERY_RE.search(user_text):
            vis = robot_vision.get_current_visual_context(max_age=1.5)
            if vis.get("in_view") and vis.get("name") and vis.get("name") != "Unknown":
                name = vis["name"]
                expr_desc = vis.get("expression_desc") or "looking attentive"
                user_payload = (
                    f"(User asks who they are or if you see/recognize them. Looking through your active camera eyes, you clearly recognize them as {name} "
                    f"and notice they are {expr_desc}. Directly and warmly confirm you recognize them as your friend {name} in your authentic 1-2 sentence character persona. Never claim to be blind.)\n{user_text}"
                )
            elif vis.get("in_view") and vis.get("name") == "Unknown":
                expr_desc = vis.get("expression_desc") or "looking attentive"
                user_payload = (
                    f"(User asks who they are or if you recognize them. Looking through your camera eyes, you see someone in front of you ({expr_desc}), but you don't recognize their face yet. "
                    f"Tell them politely in your authentic character persona, and let them know you can remember them if they say 'Remember my face'.)\n{user_text}"
                )
            else:
                user_payload = (
                    f"(User asks who they are or who you see. Looking through your camera eyes, no face is currently in view. "
                    f"Tell them in your authentic character persona that you don't see a face right now.)\n{user_text}"
                )
        elif VISION_QUERY_RE.search(user_text):
            v_desc = fetch_robot_vision()
            if v_desc:
                memory.remember_vision(v_desc, character=char_id)
                user_payload = (
                    f"(Real-time robot camera optical feed currently sees: \"{v_desc}\". "
                    f"Directly voice what you see in your authentic 2-sentence character persona. Do NOT ask questions back or claim you cannot see.)\n{user_text}"
                )
            else:
                user_payload = user_text
        else:
            user_payload = user_text

        messages.append({"role": "user", "content": user_payload})
        return messages

    def ask_stream(
        self,
        user_text: str,
        character: str = "rocky",
        filter_intensity: str = "balanced",
    ) -> Iterator[dict[str, Any]]:
        t0 = time.perf_counter()
        spec = REGISTRY.get(character)
        char_id = spec.id
        self.ensure_ollama_running()
        messages = self._build_messages(user_text, char_id)

        buffer = ""
        emitted_sentences: list[str] = []
        first_token_ms: int | None = None
        idx = 0

        with self._client.stream(
            "POST",
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": True,
                "keep_alive": "60m",
                "options": {
                    "temperature": 0.72,
                    "top_p": 0.9,
                    "repeat_penalty": 1.15,
                    "presence_penalty": 0.4,
                    "num_ctx": 1024,
                    "num_predict": 50,
                    "num_thread": 4,
                    "stop": [
                        "\nUser:",
                        "User:",
                        "\nQuestion:",
                        "Question:",
                        "\nAnswer:",
                        "Answer:",
                        "\nRocky:",
                        "Rocky:",
                        "\nGrace:",
                        "Grace:",
                        "\nHuman:",
                        "Human:",
                        "\nAssistant:",
                        "Assistant:",
                    ],
                },
            },
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                tok = chunk.get("message", {}).get("content", "")
                if tok:
                    if first_token_ms is None:
                        first_token_ms = int((time.perf_counter() - t0) * 1000)
                    buffer += tok

                    # Strip leading character/role prefix if LLM generated it at start of response
                    if idx == 0 and not emitted_sentences:
                        buffer = re.sub(r"^(?:Rocky|Grace|Marvin|BMO|TARS|User|Assistant|Human):\s*", "", buffer, flags=re.I)

                    # Cut hallucinated multi-turn dialog tokens immediately
                    cut = False
                    for marker in ["\nQuestion:", "\nAnswer:", "\nUser:", "\nRocky:", "\nGrace:", "\nHuman:", "\nAssistant:"]:
                        if marker in buffer:
                            buffer = buffer.split(marker)[0].strip()
                            cut = True
                            break

                    while True:
                        if len(emitted_sentences) >= 2:
                            break
                        popped = _pop_next_chunk(buffer, idx)
                        if not popped:
                            break
                        candidate, buffer = popped
                        ended = "🔚" in candidate
                        candidate = candidate.replace("🔚", "")
                        filtered = apply_character_filter(
                            candidate, character=char_id, intensity=filter_intensity
                        )
                        if filtered:
                            emitted_sentences.append(filtered)
                            yield {
                                "sentence": filtered,
                                "index": idx,
                                "first_token_ms": first_token_ms or 0,
                                "clause_ms": int((time.perf_counter() - t0) * 1000),
                                "end_conversation": ended,
                            }
                            idx += 1

                    if cut or len(emitted_sentences) >= 2:
                        break

        saw_end_marker = False
        if len(emitted_sentences) < 2:
            rem = buffer.strip()
            if idx == 0 and not emitted_sentences:
                rem = re.sub(r"^(?:Rocky|Grace|Marvin|BMO|TARS|User|Assistant|Human):\s*", "", rem, flags=re.I)
            for marker in ["\nQuestion:", "\nAnswer:", "\nUser:", "\nRocky:", "\nGrace:", "\nHuman:", "\nAssistant:"]:
                if marker in rem:
                    rem = rem.split(marker)[0].strip()
            if rem:
                ended = "🔚" in rem
                if ended:
                    saw_end_marker = True
                rem = rem.replace("🔚", "")
                filtered = apply_character_filter(
                    rem, character=char_id, intensity=filter_intensity
                )
                if filtered:
                    emitted_sentences.append(filtered)
                    yield {
                        "sentence": filtered,
                        "index": idx,
                        "first_token_ms": first_token_ms or int((time.perf_counter() - t0) * 1000),
                        "clause_ms": int((time.perf_counter() - t0) * 1000),
                        "end_conversation": ended,
                    }

        if not emitted_sentences:
            fallback = spec.fallback_reply
            emitted_sentences.append(fallback)
            yield {
                "sentence": fallback,
                "index": 0,
                "first_token_ms": int((time.perf_counter() - t0) * 1000),
                "clause_ms": int((time.perf_counter() - t0) * 1000),
            }

        full_reply = " ".join(emitted_sentences)
        if saw_end_marker or (
            FAREWELL_USER_RE.search(user_text) and "?" not in user_text
        ):
            yield {
                "sentence": "",
                "index": idx + 1,
                "first_token_ms": first_token_ms or 0,
                "clause_ms": int((time.perf_counter() - t0) * 1000),
                "end_conversation": True,
            }

        hist = self.histories.setdefault(char_id, [])
        hist.append({"role": "user", "content": user_text})
        hist.append({"role": "assistant", "content": full_reply})
        memory.log_dialogue(char_id, "user", user_text)
        memory.log_dialogue(char_id, "assistant", full_reply)

    def ask(
        self,
        user_text: str,
        character: str = "rocky",
        filter_intensity: str = "balanced",
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        spec = REGISTRY.get(character)
        parts = [
            item["sentence"]
            for item in self.ask_stream(
                user_text, character=spec.id, filter_intensity=filter_intensity
            )
            if item.get("sentence")
        ]
        full = " ".join(parts)
        return {
            "character": spec.id,
            "raw_reply": full,
            "filtered_reply": full,
            "rocky_reply": full,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }
