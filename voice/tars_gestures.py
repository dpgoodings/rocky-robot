"""
TARS & GPTARS Kinematic Gesture Subsystem.
Implements the physical macro-gestures, servo routines, and humor cue-light state
mirroring the YouTube build (@gptars / Travis) and poboisvert/GPTARS_Interstellar.

Supports:
- Physical macro gestures: fist_bump, cue_light, bow, stand_tall, crouch, nod, shake, step locomotion.
- Asynchronous non-blocking dispatch to ESP32 hardware bridge (`/gesture` or `/motor`).
- Seamless local simulation fallback for MacBook Pro standalone operation.
- Real-time gesture intent parsing and tag extraction from AI stream (<gesture:NAME>).
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import logging
import os
import re
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger("tars_gestures")

@dataclass
class GestureDef:
    id: str
    name: str
    category: str  # "social", "posture", "locomotion", "hardware"
    description: str
    duration_ms: int
    cue_light: str  # "off", "pulse", "steady", "toggle"
    servo_angles: dict[str, Any] = field(default_factory=dict)
    icon: str = "🤖"


GESTURE_CATALOG: dict[str, GestureDef] = {
    "fist_bump": GestureDef(
        id="fist_bump",
        name="Fist Bump",
        category="social",
        description="Extends inner monolith panels forward for a robotic fist bump greeting.",
        duration_ms=2200,
        cue_light="pulse",
        servo_angles={"left_inner": 45, "right_inner": 45, "torso_tilt": 0},
        icon="👊",
    ),
    "cue_light": GestureDef(
        id="cue_light",
        name="Humor Cue Light",
        category="hardware",
        description="Flashes the green 85% humor indicator light to confirm tactical deadpan sarcasm.",
        duration_ms=1800,
        cue_light="toggle",
        servo_angles={},
        icon="💡",
    ),
    "bow": GestureDef(
        id="bow",
        name="Tactical Bow / Salute",
        category="posture",
        description="Dips upper chassis 25 degrees forward in USMC salute or deference.",
        duration_ms=2400,
        cue_light="steady",
        servo_angles={"torso_tilt": -25, "outer_left": 10, "outer_right": 10},
        icon="🙇",
    ),
    "stand_tall": GestureDef(
        id="stand_tall",
        name="Stand Tall",
        category="posture",
        description="Extends center column to maximum height (100% height attention posture).",
        duration_ms=2000,
        cue_light="steady",
        servo_angles={"center_column": 100, "torso_tilt": 0},
        icon="🫡",
    ),
    "crouch": GestureDef(
        id="crouch",
        name="Crouch / Rest",
        category="posture",
        description="Retracts center column into low-profile compact MacBook rest mode.",
        duration_ms=1800,
        cue_light="off",
        servo_angles={"center_column": 0, "torso_tilt": 15},
        icon="🧘",
    ),
    "nod": GestureDef(
        id="nod",
        name="Head Nod",
        category="social",
        description="Tactical vertical acknowledgment dip of the top panel.",
        duration_ms=1200,
        cue_light="pulse",
        servo_angles={"head_pitch": [15, -10, 0]},
        icon="🟢",
    ),
    "shake": GestureDef(
        id="shake",
        name="Head Shake",
        category="social",
        description="Side-to-side swivel expressing deadpan tactical disagreement.",
        duration_ms=1400,
        cue_light="pulse",
        servo_angles={"head_yaw": [-20, 20, -20, 20, 0]},
        icon="🔴",
    ),
    "step_forward": GestureDef(
        id="step_forward",
        name="Step Forward",
        category="locomotion",
        description="Four-panel planetary walking step forward.",
        duration_ms=2600,
        cue_light="pulse",
        servo_angles={"gait": "walk_fwd", "cycles": 1},
        icon="⬆️",
    ),
    "step_backward": GestureDef(
        id="step_backward",
        name="Step Backward",
        category="locomotion",
        description="Reverse locomotive stepping sequence.",
        duration_ms=2600,
        cue_light="pulse",
        servo_angles={"gait": "walk_rev", "cycles": 1},
        icon="⬇️",
    ),
    "turn_left": GestureDef(
        id="turn_left",
        name="Pivot Left",
        category="locomotion",
        description="Pivots chassis 30 degrees counter-clockwise.",
        duration_ms=1800,
        cue_light="pulse",
        servo_angles={"gait": "pivot_left", "angle": 30},
        icon="⬅️",
    ),
    "turn_right": GestureDef(
        id="turn_right",
        name="Pivot Right",
        category="locomotion",
        description="Pivots chassis 30 degrees clockwise.",
        duration_ms=1800,
        cue_light="pulse",
        servo_angles={"gait": "pivot_right", "angle": 30},
        icon="➡️",
    ),
    "sigh": GestureDef(
        id="sigh",
        name="Marvin Sigh",
        category="posture",
        description="Droops head and shoulders in classic melancholic Marvin resignation.",
        duration_ms=2200,
        cue_light="off",
        servo_angles={"head_tilt": 64, "left_shoulder": 74, "right_shoulder": 74},
        icon="😔",
    ),
    "wave": GestureDef(
        id="wave",
        name="Reluctant Wave",
        category="social",
        description="Raises right arm for a half-hearted greeting wave.",
        duration_ms=2200,
        cue_light="pulse",
        servo_angles={"right_shoulder_pitch": 140, "right_shoulder_roll": 115},
        icon="👋",
    ),
}

# Spoken Intent Regex Patterns
GESTURE_INTENT_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:sigh|depress(?:ed|ing)|gloomy|brood|miserable)\b", re.I), "sigh"),
    (re.compile(r"\b(?:wave|say\s+hi|say\s+hello|greet)\b", re.I), "wave"),
    (re.compile(r"\b(?:fist\s*bump|pound\s*it|knuckles|give\s+me\s+(?:a\s+)?fist\s*bump|bump\s+me)\b", re.I), "fist_bump"),
    (re.compile(r"\b(?:flash\s+(?:your\s+)?cue|cue\s*light(?:\s+(?:on|off|flash))?|indicator\s*light|are\s+you\s+kidding|is\s+that\s+a\s+joke)\b", re.I), "cue_light"),
    (re.compile(r"\b(?:take\s+a\s+bow|bow(?:\s+for\s+me)?|salute|curtsy)\b", re.I), "bow"),
    (re.compile(r"\b(?:stand\s+tall|attention|extend\s+height|stand\s+up|full\s+height)\b", re.I), "stand_tall"),
    (re.compile(r"\b(?:crouch|rest\s*mode|at\s*ease|stow|relax|low\s+profile)\b", re.I), "crouch"),
    (re.compile(r"\b(?:nod(?:\s+your\s+head)?|agree\s+with\s+me|affirmative)\b", re.I), "nod"),
    (re.compile(r"\b(?:shake\s+your\s+head|disagree|negative)\b", re.I), "shake"),
    (re.compile(r"\b(?:step\s+forward|walk\s+forward|move\s+forward|come\s+forward|walk)\b", re.I), "step_forward"),
    (re.compile(r"\b(?:step\s+back(?:ward)?|move\s+back|back\s+up|reverse)\b", re.I), "step_backward"),
    (re.compile(r"\b(?:turn\s+left|pivot\s+left)\b", re.I), "turn_left"),
    (re.compile(r"\b(?:turn\s+right|pivot\s+right)\b", re.I), "turn_right"),
]

GESTURE_TAG_RE = re.compile(r"<gesture:([a-z_]+)>", re.I)


class TARSGestureEngine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cue_light_active: bool = False
        self.cue_light_flashing: bool = False
        self.posture: str = "neutral"
        self.current_gesture: str | None = None
        self.current_gesture_until: float = 0.0
        self.history: list[dict[str, Any]] = []
        self._max_history = 20

    def detect_intent(self, text: str) -> str | None:
        """Parses user spoken text for explicit gesture commands."""
        for pattern, gid in GESTURE_INTENT_MAP:
            if pattern.search(text):
                return gid
        return None

    def extract_tags(self, text: str) -> tuple[str, list[str]]:
        """Extracts and removes <gesture:...> tags from response text."""
        found: list[str] = []
        for m in GESTURE_TAG_RE.finditer(text):
            found.append(m.group(1).lower())
        clean_text = GESTURE_TAG_RE.sub("", text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()
        return clean_text, found

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            is_busy = now < self.current_gesture_until
            active = self.current_gesture if is_busy else None
            return {
                "active_gesture": active,
                "is_executing": is_busy,
                "time_remaining_ms": max(0, int((self.current_gesture_until - now) * 1000)) if is_busy else 0,
                "cue_light": self.cue_light_active or self.cue_light_flashing,
                "cue_light_flashing": self.cue_light_flashing,
                "posture": self.posture,
                "recent_gestures": self.history[-5:],
            }

    def set_cue_light(self, state: bool) -> dict[str, Any]:
        with self._lock:
            self.cue_light_active = state
            self.cue_light_flashing = False
            return {"cue_light": self.cue_light_active}

    def execute_gesture(self, gesture_name: str, robot_ip: str | None = None) -> dict[str, Any]:
        """
        Executes a gesture: updates local kinematic state and dispatches
        command payload to ESP32 robot asynchronously if online.
        """
        gid = gesture_name.strip().lower()
        gdef = GESTURE_CATALOG.get(gid)
        if not gdef:
            # Fallback to closest match
            for k in GESTURE_CATALOG:
                if k in gid or gid in k:
                    gdef = GESTURE_CATALOG[k]
                    gid = k
                    break

        if not gdef:
            return {"status": "error", "message": f"Unknown gesture '{gesture_name}'"}

        now = time.time()
        duration_sec = gdef.duration_ms / 1000.0

        with self._lock:
            self.current_gesture = gdef.id
            self.current_gesture_until = now + duration_sec

            if gdef.id == "cue_light":
                # Toggle or pulse cue light
                self.cue_light_active = not self.cue_light_active
            elif gdef.cue_light == "pulse":
                self.cue_light_flashing = True
                threading.Timer(duration_sec, self._clear_cue_flash).start()
            elif gdef.cue_light == "steady":
                self.cue_light_active = True
            elif gdef.cue_light == "off":
                self.cue_light_active = False

            if gdef.id in ("stand_tall", "crouch", "bow"):
                self.posture = gdef.id
            elif "step" in gdef.id or "turn" in gdef.id:
                self.posture = "walking"
            else:
                self.posture = "neutral"

            entry = {
                "id": gdef.id,
                "name": gdef.name,
                "category": gdef.category,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "duration_ms": gdef.duration_ms,
            }
            self.history.append(entry)
            if len(self.history) > self._max_history:
                self.history.pop(0)

        # Dispatch async to ESP32 bridge in background thread
        threading.Thread(
            target=self._dispatch_hardware,
            args=(gdef, robot_ip),
            daemon=True,
        ).start()

        return {
            "status": "executing",
            "gesture": asdict(gdef),
            "cue_light": self.cue_light_active or self.cue_light_flashing,
            "posture": self.posture,
            "duration_ms": gdef.duration_ms,
        }

    def _clear_cue_flash(self) -> None:
        with self._lock:
            self.cue_light_flashing = False

    def _dispatch_hardware(self, gdef: GestureDef, robot_ip: str | None) -> None:
        """Best-effort async dispatch to ESP32-S3 robot endpoint."""
        target_ip = robot_ip or os.environ.get("ROBOT_HOST")
        if not target_ip:
            # Check config cache
            try:
                cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_config.json")
                if os.path.exists(cfg_path):
                    with open(cfg_path, "r") as f:
                        target_ip = json.load(f).get("esp32_ip")
            except Exception:
                pass

        if not target_ip:
            return

        urls = [
            f"http://{target_ip}/gesture?action={gdef.id}",
            f"http://{target_ip}/command?cmd={gdef.id}",
        ]
        payload = json.dumps({
            "action": gdef.id,
            "angles": gdef.servo_angles,
            "duration_ms": gdef.duration_ms,
        }).encode("utf-8")

        for u in urls:
            try:
                with httpx.Client(timeout=1.2) as client:
                    resp = client.post(u, content=payload, headers={"Content-Type": "application/json"})
                    if resp.status_code == 200:
                        break
            except Exception:
                pass


# Global Singleton Instance
GESTURE_ENGINE = TARSGestureEngine()
