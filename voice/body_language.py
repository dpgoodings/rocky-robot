"""Emotional speaking sway and subtle head body language for Rocky.

Ported cleanly from desk-robot-2.1's `body.py` onto Rocky's HTTP ESP32-S3 API:
- `on_thinking(robot_ip)`: subtle upward-sideways pondering glance.
- `on_speak_start(emotion, robot_ip)`: opening emotion micro-gesture via `POST /action`,
  followed by gentle speaking sway via `POST /servo` spaced >= 2.0s apart (> 1.8s so
  PCA9685 servos can auto-relax cleanly between moves).
- `on_speak_stop()`: stops sway immediately and returns smoothly to resting pose.

Automatically suppresses itself whenever `FACE_TRACKER.enabled` is True or
`BODY_LANGUAGE.enabled` is False. All HTTP dispatches run on non-blocking daemon
threads with short 0.6s timeouts so an unreachable ESP32 never delays voice turns.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable

import httpx

HTTP_TIMEOUT_S: float = 0.6
SWAY_MIN_INTERVAL_S: float = 2.0   # Strictly > 1.8s so PCA9685 auto-relax (1.5s) engages cleanly
SWAY_BASE_INTERVAL_S: float = 2.2  # Default speaking sway cadence
SWAY_MAX_DURATION_S: float = 45.0  # Safety ceiling if on_speak_stop() is missed

PAN_MIN_DEG: float = 15.0
PAN_MAX_DEG: float = 165.0
TILT_MIN_DEG: float = 76.0   # Keep sway tilt within firmware auto-relaxation band (75..100 deg)
TILT_MAX_DEG: float = 99.0

EMOTION_TO_GESTURE: dict[str, str] = {
    "happy": "happy_bounce",
    "excited": "happy_bounce",
    "sad": "sad_droop",
    "sleepy": "sad_droop",
    "worried": "sad_droop",
    "surprised": "surprised_pop",
    "alarmed": "surprised_pop",
    "curious": "curious_tilt",
    "skeptical": "curious_tilt",
    "thinking": "curious_tilt",
    "angry": "shake",
    "confused": "shake",
    "neutral": "nod",
    "default": "nod",
    "love": "nod",
}

# Speaking sway profile: (pan_amp_deg, tilt_amp_deg, interval_s) or None for stillness
EMOTION_SWAY_STYLE: dict[str, tuple[float, float, float] | None] = {
    "excited": (6.5, 3.5, 2.0),
    "happy": (5.5, 3.0, 2.2),
    "love": (5.0, 2.5, 2.5),
    "curious": (5.5, 2.5, 2.3),
    "skeptical": (4.0, 2.0, 2.5),
    "confused": (5.0, 2.0, 2.2),
    "angry": (4.5, 2.5, 2.1),
    "surprised": (4.5, 3.0, 2.2),
    "alarmed": (4.5, 3.0, 2.1),
    "sad": (2.5, 2.0, 2.8),
    "worried": (3.0, 2.0, 2.6),
    "neutral": (4.0, 2.5, 2.2),
    "default": (4.0, 2.5, 2.2),
    "sleepy": None,
    "asleep": None,
}


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(val)))


def _face_tracking_active() -> bool:
    """Safely check whether FACE_TRACKER is currently active without circular imports."""
    try:
        import face_tracker
        return bool(getattr(face_tracker.FACE_TRACKER, "enabled", False))
    except Exception:
        return False


def _get_tracker_rest_pose() -> tuple[float, float]:
    """Read current resting head pose from FACE_TRACKER, falling back to center (90, 90)."""
    try:
        import face_tracker
        ft = face_tracker.FACE_TRACKER
        return float(getattr(ft, "pan_deg", 90.0)), float(getattr(ft, "tilt_deg", 90.0))
    except Exception:
        return 90.0, 90.0


def _sync_tracker_pose(pan: float, tilt: float, speed: float = 160.0) -> None:
    """Keep FACE_TRACKER's SlewModel informed when body language moves the head."""
    try:
        import face_tracker
        ft = face_tracker.FACE_TRACKER
        if not ft.enabled and hasattr(ft, "set_manual_pose"):
            ft.set_manual_pose(pan=pan, tilt=tilt, speed=speed)
    except Exception:
        pass


class RockyBodyLanguage:
    """Non-blocking emotional body language coordinator for Rocky's pan/tilt neck."""

    def __init__(self, post_fn: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self._lock = threading.Lock()
        self.enabled: bool = True
        self._post_fn = post_fn  # Optional injectable transport for offline unit tests

        self._speaking: bool = False
        self._thinking: bool = False
        self._emotion: str = "neutral"
        self._robot_ip: str = ""
        self._rest_pose: tuple[float, float] = (90.0, 90.0)
        self._off_rest: bool = False

        self._session_id: int = 0
        self._stop_event = threading.Event()
        self._sway_thread: threading.Thread | None = None

        self.last_action: str = ""
        self.last_action_at: float = 0.0
        self.last_error: str = ""

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        flag = bool(enabled)
        with self._lock:
            self.enabled = flag
        if not flag:
            self.on_speak_stop()
        return self.status()

    def is_suppressed(self) -> bool:
        """True when body language must hold still (disabled or face tracking owns the neck)."""
        return (not self.enabled) or _face_tracking_active()

    def _default_post(self, url: str, payload: dict[str, Any]) -> None:
        timeout = httpx.Timeout(
            connect=HTTP_TIMEOUT_S,
            read=HTTP_TIMEOUT_S,
            write=HTTP_TIMEOUT_S,
            pool=HTTP_TIMEOUT_S,
        )
        with httpx.Client(timeout=timeout) as client:
            client.post(url, json=payload)

    def _dispatch_async(self, path: str, payload: dict[str, Any], robot_ip: str) -> None:
        ip = (robot_ip or "").strip()
        if not ip:
            return
        url = f"http://{ip}{path}"
        fn = self._post_fn or self._default_post

        def _worker() -> None:
            try:
                fn(url, payload)
                with self._lock:
                    self.last_error = ""
            except Exception as exc:
                with self._lock:
                    self.last_error = str(exc)

        threading.Thread(target=_worker, daemon=True, name="body_lang_http").start()

    def gesture_for_emotion(self, emotion: str) -> str | None:
        norm = (emotion or "neutral").strip().lower()
        if norm == "asleep":
            return None
        return EMOTION_TO_GESTURE.get(norm, "nod")

    def on_thinking(self, robot_ip: str = "") -> bool:
        """Subtle upward-sideways pondering head glance while generating a reply."""
        if self.is_suppressed():
            return False

        ip = (robot_ip or self._robot_ip).strip()
        if not ip:
            return False

        with self._lock:
            if self._speaking:
                return False
            self._robot_ip = ip
            self._thinking = True
            self._rest_pose = _get_tracker_rest_pose()
            rp, rt = self._rest_pose
            side = random.choice((-1.0, 1.0))
            pan = round(_clamp(rp + side * 6.5, PAN_MIN_DEG, PAN_MAX_DEG), 1)
            tilt = round(_clamp(rt + 5.5, 62.0, 118.0), 1)
            self._off_rest = True
            self.last_action = "thinking_glance"
            self.last_action_at = time.time()

        _sync_tracker_pose(pan, tilt, speed=140.0)
        self._dispatch_async("/servo", {"pan": pan, "tilt": tilt, "speed": 140}, ip)
        return True

    def on_speak_start(self, emotion: str = "neutral", robot_ip: str = "") -> bool:
        """Play opening emotion micro-gesture, then gently sway every ~2.2s until on_speak_stop()."""
        if self.is_suppressed():
            return False

        ip = (robot_ip or self._robot_ip).strip()
        norm_emotion = (emotion or "neutral").strip().lower()
        gesture = self.gesture_for_emotion(norm_emotion)

        with self._lock:
            self._session_id += 1
            session_id = self._session_id
            self._stop_event.set()
            self._stop_event = threading.Event()
            stop_ev = self._stop_event

            if not self._off_rest:
                self._rest_pose = _get_tracker_rest_pose()
            self._robot_ip = ip
            self._emotion = norm_emotion
            self._thinking = False
            self._speaking = True
            self._off_rest = True
            self.last_action = gesture or "still"
            self.last_action_at = time.time()

        if not ip or gesture is None:
            return False

        # Opening micro-gesture via firmware /action
        self._dispatch_async("/action", {"action": gesture}, ip)

        # Gentle speaking sway loop (spaced > 1.8s so servos relax cleanly between moves)
        style = EMOTION_SWAY_STYLE.get(norm_emotion, EMOTION_SWAY_STYLE["neutral"])
        if style is not None:
            t = threading.Thread(
                target=self._sway_loop,
                args=(session_id, stop_ev, ip, norm_emotion, style),
                daemon=True,
                name="body_lang_sway",
            )
            with self._lock:
                self._sway_thread = t
            t.start()

        return True

    def on_speak_stop(self) -> bool:
        """Stop speaking sway immediately and return smoothly to resting pose."""
        with self._lock:
            was_active = self._speaking or self._thinking or self._off_rest
            self._session_id += 1
            self._speaking = False
            self._thinking = False
            self._stop_event.set()
            ip = self._robot_ip
            rp, rt = self._rest_pose
            should_rest = self._off_rest and bool(ip) and (not _face_tracking_active())
            self._off_rest = False

        if should_rest:
            pan = round(_clamp(rp, PAN_MIN_DEG, PAN_MAX_DEG), 1)
            tilt = round(_clamp(rt, TILT_MIN_DEG, TILT_MAX_DEG), 1)
            _sync_tracker_pose(pan, tilt, speed=150.0)
            self._dispatch_async("/servo", {"pan": pan, "tilt": tilt, "speed": 150}, ip)
            with self._lock:
                self.last_action = "rest"
                self.last_action_at = time.time()
        return was_active

    def _sway_loop(
        self,
        session_id: int,
        stop_ev: threading.Event,
        robot_ip: str,
        emotion: str,
        style: tuple[float, float, float],
    ) -> None:
        pan_amp, tilt_amp, base_interval = style
        interval = max(SWAY_MIN_INTERVAL_S, float(base_interval))
        started = time.time()
        side = 1.0

        # Wait for the opening micro-gesture to complete & servos to settle before first sway
        while not stop_ev.is_set():
            jitter = random.uniform(-0.15, 0.45)
            wait_s = max(SWAY_MIN_INTERVAL_S, interval + jitter)
            if stop_ev.wait(timeout=wait_s):
                break
            if (time.time() - started) > SWAY_MAX_DURATION_S:
                break
            if self.is_suppressed():
                break

            with self._lock:
                if not self._speaking or self._session_id != session_id:
                    break
                rp, rt = self._rest_pose

            # Alternate gentle side drift or subtle nod inside the servo relax band
            side = -side
            dpan = side * pan_amp * random.uniform(0.65, 1.0)
            dtilt = random.uniform(-tilt_amp, tilt_amp * 0.6)
            pan = round(_clamp(rp + dpan, PAN_MIN_DEG, PAN_MAX_DEG), 1)
            tilt = round(_clamp(rt + dtilt, TILT_MIN_DEG, TILT_MAX_DEG), 1)

            _sync_tracker_pose(pan, tilt, speed=140.0)
            self._dispatch_async("/servo", {"pan": pan, "tilt": tilt, "speed": 140}, robot_ip)
            with self._lock:
                self.last_action = f"sway:{emotion}"
                self.last_action_at = time.time()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "suppressed": self.is_suppressed(),
                "face_tracking_active": _face_tracking_active(),
                "speaking": self._speaking,
                "thinking": self._thinking,
                "emotion": self._emotion,
                "robot_ip": self._robot_ip,
                "sway_interval_s": SWAY_BASE_INTERVAL_S,
                "sway_min_interval_s": SWAY_MIN_INTERVAL_S,
                "last_action": self.last_action,
                "last_action_at": round(self.last_action_at, 3) if self.last_action_at else 0.0,
                "last_error": self.last_error,
            }


BODY_LANGUAGE = RockyBodyLanguage()
