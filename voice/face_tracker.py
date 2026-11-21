"""Real-time face tracking loop bridging ESP32-S3 camera -> YuNet -> Pan/Tilt servos + ST7789 gaze & expressions."""

from __future__ import annotations

import threading
import time
from typing import Any

import cv2
import httpx
import numpy as np

import robot_vision

EMOTION_TO_MOOD: dict[str, str] = {
    "happy": "happy",
    "sad": "sad",
    "surprised": "surprised",
    "fearful": "surprised",
    "angry": "angry",
    "disgust": "angry",
    "neutral": "default",
}


class MarvinFaceTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.enabled: bool = False
        self.robot_ip: str = ""
        self.pan_deg: float = 90.0
        self.tilt_deg: float = 92.0
        self.last_face: dict[str, Any] | None = None
        self.last_error: str = ""
        self.last_mood: str = "default"
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self, robot_ip: str) -> dict[str, Any]:
        with self._lock:
            self.robot_ip = robot_ip.strip()
            self.enabled = True
            self.last_error = ""
            if self._thread is None or not self._thread.is_alive():
                self._stop_event.clear()
                self._thread = threading.Thread(target=self._run_loop, daemon=True)
                self._thread.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self.enabled = False
            self._stop_event.set()
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "robot_ip": self.robot_ip,
                "pan_deg": round(self.pan_deg, 1),
                "tilt_deg": round(self.tilt_deg, 1),
                "last_face": self.last_face,
                "last_mood": self.last_mood,
                "last_error": self.last_error,
            }

    def _run_loop(self) -> None:
        limits = httpx.Limits(max_keepalive_connections=4, max_connections=8, keepalive_expiry=30.0)
        timeout = httpx.Timeout(connect=0.8, read=1.2, write=0.8, pool=0.8)

        # Persistent HTTP client with keep-alive connection reuse
        with httpx.Client(limits=limits, timeout=timeout) as client:
            last_mood_sent = ""
            last_mood_sent_time = 0.0

            while not self._stop_event.is_set():
                with self._lock:
                    active = self.enabled
                    ip = self.robot_ip
                if not active or not ip:
                    time.sleep(0.15)
                    continue

                try:
                    resp = client.get(f"http://{ip}/capture")
                    if resp.status_code != 200 or not resp.content:
                        time.sleep(0.08)
                        continue
                    arr = np.frombuffer(resp.content, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is None:
                        time.sleep(0.08)
                        continue

                    h, w = frame.shape[:2]
                    detector, _ = robot_vision._get_models(w, h)
                    _, faces = detector.detect(frame)

                    if faces is not None and len(faces) > 0:
                        best = max(faces, key=lambda f: float(f[2] * f[3]))
                        cx = float(best[0] + best[2] * 0.5)
                        cy = float(best[1] + best[3] * 0.5)
                        err_x = (cx - w * 0.5) / (w * 0.5)   # -1 left .. +1 right
                        err_y = (cy - h * 0.5) / (h * 0.5)   # -1 top  .. +1 bottom

                        # Fast facial expression classification from 5 landmarks (MobileFaceNet ~3ms)
                        expr_label, expr_prob, expr_desc = "neutral", 1.0, "attentive"
                        mapped_mood = "default"
                        try:
                            if len(best) >= 14:
                                landmarks = best[4:14].reshape((5, 2))
                                expr_label, expr_prob, expr_desc = robot_vision.classify_expression(frame, landmarks)
                                mapped_mood = EMOTION_TO_MOOD.get(expr_label, "default")
                        except Exception:
                            pass

                        with self._lock:
                            if abs(err_x) > 0.05:
                                self.pan_deg = max(15.0, min(165.0, self.pan_deg - err_x * 12.0))
                            if abs(err_y) > 0.05:
                                self.tilt_deg = max(58.0, min(132.0, self.tilt_deg - err_y * 9.0))
                            pan, tilt = self.pan_deg, self.tilt_deg
                            self.last_mood = mapped_mood
                            self.last_face = {
                                "cx": round(cx, 1),
                                "cy": round(cy, 1),
                                "err_x": round(err_x, 3),
                                "err_y": round(err_y, 3),
                                "expression": expr_label,
                                "expression_prob": round(float(expr_prob), 2),
                                "expression_desc": expr_desc,
                            }
                            self.last_error = ""

                        # Single unified POST payload: head servos + screen gaze + facial expression
                        payload: dict[str, Any] = {
                            "pan": round(pan, 1),
                            "tilt": round(tilt, 1),
                            "speed": 240,
                            "gx": round(float(max(-1.0, min(1.0, err_x * 1.4))), 3),
                            "gy": round(float(max(-1.0, min(1.0, -err_y * 1.4))), 3),
                        }

                        now = time.time()
                        # Update mood if changed or periodically every 3 seconds to keep sync
                        if mapped_mood != last_mood_sent or (now - last_mood_sent_time) > 3.0:
                            payload["mood"] = mapped_mood
                            last_mood_sent = mapped_mood
                            last_mood_sent_time = now

                        client.post(f"http://{ip}/face", json=payload)
                    else:
                        with self._lock:
                            self.last_face = None
                except Exception as exc:
                    with self._lock:
                        self.last_error = str(exc)

                time.sleep(0.02)  # High responsiveness (~30 Hz tracking cadence)


FACE_TRACKER = MarvinFaceTracker()
