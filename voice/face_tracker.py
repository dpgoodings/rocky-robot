"""Real-time predictive face tracking loop bridging ESP32-S3 camera -> YuNet -> Pan/Tilt servos + ST7789 gaze & expressions.

Uses a latency-compensated SlewModel mirroring firmware `marvin_motion`'s slew rate
and a timestamped `pose_history` deque (ported from desk-robot-2.1) so Wi-Fi MJPEG lag
never causes proportional-stepping overshoot or oscillation.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
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

# Optical & mechanical defaults for Rocky's ESP32-S3 OV2640/OV5640 + PCA9685 neck
TRACK_LATENCY_S: float = 0.25          # Camera exposure + JPEG + Wi-Fi + servo command lag
TRACK_HFOV_DEG: float = 58.0           # Horizontal field of view (degrees)
TRACK_VFOV_DEG: float = 44.0           # Vertical field of view (degrees)
TRACK_DEADZONE_DEG: float = 3.5        # Ignore tiny angular errors to eliminate micro-jitter
TRACK_GAIN: float = 0.65               # Fractional step toward estimated true face angle
TRACK_SEND_INTERVAL_S: float = 0.06    # Minimum interval between servo commands (~16 Hz)
TRACK_SLEW_DEG_PER_SEC: float = 240.0  # Matches marvin_motion tracking speed payload

PAN_MIN_DEG: float = 15.0
PAN_MAX_DEG: float = 165.0
TILT_MIN_DEG: float = 58.0
TILT_MAX_DEG: float = 132.0
PAN_CENTER_DEG: float = 90.0
TILT_CENTER_DEG: float = 92.0


def offset_deg_from_err(err: float, fov_deg: float) -> float:
    """Exact pinhole-camera angular offset in degrees from normalized frame error (-1..+1)."""
    clamped = max(-1.5, min(1.5, float(err)))
    half_fov_rad = math.radians(max(1.0, float(fov_deg)) * 0.5)
    return math.degrees(math.atan(clamped * math.tan(half_fov_rad)))


class SlewModel:
    """Single-axis servo slew-rate model matching firmware `marvin_motion::tick` (50 Hz).

    Maintains a timestamped `pose_history` deque so the tracker can query where the
    head was actually pointing at `t_frame = now - TRACK_LATENCY_S`.
    """

    DT: float = 0.02  # 50 Hz firmware tick

    def __init__(
        self,
        initial_deg: float = 90.0,
        slew_deg_per_sec: float = TRACK_SLEW_DEG_PER_SEC,
        min_deg: float = PAN_MIN_DEG,
        max_deg: float = PAN_MAX_DEG,
        now: float | None = None,
        history_len: int = 150,
    ) -> None:
        self.min_deg = float(min_deg)
        self.max_deg = float(max_deg)
        self.slew_deg_per_sec = max(1.0, float(slew_deg_per_sec))
        clamped = self._clamp(initial_deg)
        self.pos: float = clamped
        self.target: float = clamped
        t0 = time.time() if now is None else float(now)
        self.t: float = t0
        self.pose_history: deque[tuple[float, float]] = deque([(t0, clamped)], maxlen=history_len)

    def _clamp(self, deg: float) -> float:
        return max(self.min_deg, min(self.max_deg, float(deg)))

    def snap(self, deg: float, now: float | None = None) -> None:
        """Immediately synchronize both current position and target (e.g. reset/init)."""
        t = time.time() if now is None else float(now)
        clamped = self._clamp(deg)
        self.pos = clamped
        self.target = clamped
        self.t = t
        self.pose_history.clear()
        self.pose_history.append((t, clamped))

    def set_target(
        self,
        deg: float,
        now: float | None = None,
        slew_deg_per_sec: float | None = None,
    ) -> None:
        """Advance to `now`, then assign a new clamped servo target."""
        t = time.time() if now is None else float(now)
        if t < self.t:
            # Handle wall-clock jump or synthetic clock in offline tests cleanly
            self.t = t
            self.pose_history.clear()
            self.pose_history.append((t, self.pos))
        else:
            self.advance(t)
        if slew_deg_per_sec is not None and slew_deg_per_sec > 0:
            self.slew_deg_per_sec = float(slew_deg_per_sec)
        self.target = self._clamp(deg)

    def advance(self, now: float) -> float:
        """Integrate the servo position forward to `now` at fixed 50 Hz steps."""
        now = float(now)
        if now <= self.t:
            return self.pos

        if now - self.t > 3.0:
            # Long idle: servo has long since arrived at target
            self.pos = self.target
            self.t = now - self.DT
            self.pose_history.append((self.t, self.pos))

        max_step = self.slew_deg_per_sec * self.DT
        while self.t + self.DT <= now + 1e-9:
            self.t += self.DT
            diff = self.target - self.pos
            if abs(diff) <= max_step:
                self.pos = self.target
            else:
                self.pos += max_step if diff > 0.0 else -max_step
            self.pose_history.append((self.t, self.pos))

        return self.pos

    def at(self, when: float) -> float:
        """Estimate where the axis was pointing at timestamp `when` (linearly interpolated)."""
        when = float(when)
        if when > self.t:
            self.advance(when)

        if not self.pose_history:
            return self.pos
        if when <= self.pose_history[0][0]:
            return self.pose_history[0][1]
        if when >= self.pose_history[-1][0]:
            return self.pose_history[-1][1]

        # Walk backward (queries are almost always within the last ~0.25-0.5s)
        prev_t, prev_p = self.pose_history[-1]
        for t, p in reversed(self.pose_history):
            if t <= when:
                span = prev_t - t
                if span <= 1e-9:
                    return p
                frac = (when - t) / span
                return p + (prev_p - p) * frac
            prev_t, prev_p = t, p
        return self.pose_history[0][1]

    def arrival_in(self, now: float | None = None) -> float:
        """Seconds remaining until this axis reaches its current target."""
        t = time.time() if now is None else float(now)
        self.advance(t)
        return abs(self.target - self.pos) / max(1.0, self.slew_deg_per_sec)


class MarvinFaceTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.enabled: bool = False
        self.robot_ip: str = ""
        self.pan_deg: float = PAN_CENTER_DEG
        self.tilt_deg: float = TILT_CENTER_DEG
        self.last_face: dict[str, Any] | None = None
        self.last_error: str = ""
        self.last_mood: str = "default"

        # Predictive tracking parameters
        self.latency_s: float = TRACK_LATENCY_S
        self.hfov_deg: float = TRACK_HFOV_DEG
        self.vfov_deg: float = TRACK_VFOV_DEG
        self.deadzone_deg: float = TRACK_DEADZONE_DEG
        self.gain: float = TRACK_GAIN
        # Sign convention matching Rocky's pan/tilt servo wiring:
        # face right (+err_x) -> pan decreases toward 15 deg; face up (-err_y) -> tilt increases toward 132 deg
        self.pan_sign: float = -1.0
        self.tilt_sign: float = -1.0

        now = time.time()
        self.pan_model = SlewModel(
            initial_deg=self.pan_deg,
            slew_deg_per_sec=TRACK_SLEW_DEG_PER_SEC,
            min_deg=PAN_MIN_DEG,
            max_deg=PAN_MAX_DEG,
            now=now,
        )
        self.tilt_model = SlewModel(
            initial_deg=self.tilt_deg,
            slew_deg_per_sec=TRACK_SLEW_DEG_PER_SEC,
            min_deg=TILT_MIN_DEG,
            max_deg=TILT_MAX_DEG,
            now=now,
        )

        # Smoothed on-screen eye gaze (-1..+1)
        self._gaze_x: float = 0.0
        self._gaze_y: float = 0.0
        self._gaze_alpha: float = 0.35

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self, robot_ip: str) -> dict[str, Any]:
        with self._lock:
            self.robot_ip = robot_ip.strip()
            self.enabled = True
            self.last_error = ""
            now = time.time()
            self.pan_model.advance(now)
            self.tilt_model.advance(now)
            if self._thread is None or not self._thread.is_alive():
                self._stop_event.clear()
                self._thread = threading.Thread(target=self._run_loop, daemon=True, name="face_tracker")
                self._thread.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self.enabled = False
            self.last_face = None
            self._gaze_x = 0.0
            self._gaze_y = 0.0
            self._stop_event.set()
        return self.status()

    def set_manual_pose(
        self,
        pan: float | None = None,
        tilt: float | None = None,
        speed: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Synchronize the tracker's pose and SlewModels after an external/manual servo move."""
        t = time.time() if now is None else float(now)
        with self._lock:
            if pan is not None:
                clamped_pan = max(PAN_MIN_DEG, min(PAN_MAX_DEG, float(pan)))
                self.pan_deg = clamped_pan
                self.pan_model.set_target(clamped_pan, now=t, slew_deg_per_sec=speed)
            if tilt is not None:
                clamped_tilt = max(TILT_MIN_DEG, min(TILT_MAX_DEG, float(tilt)))
                self.tilt_deg = clamped_tilt
                self.tilt_model.set_target(clamped_tilt, now=t, slew_deg_per_sec=speed)
        return self.status()

    def arrival_in(self, now: float | None = None) -> float:
        """Seconds until both pan and tilt axes finish their current move."""
        t = time.time() if now is None else float(now)
        with self._lock:
            return max(self.pan_model.arrival_in(t), self.tilt_model.arrival_in(t))

    def compute_target_pose(
        self,
        err_x: float,
        err_y: float,
        now: float | None = None,
        apply: bool = True,
        slew_deg_per_sec: float = TRACK_SLEW_DEG_PER_SEC,
    ) -> tuple[float, float, bool]:
        """Compute latency-compensated pan/tilt targets from normalized face offsets (-1..+1).

        Returns `(new_pan, new_tilt, moved)`.
        """
        t = time.time() if now is None else float(now)
        t_frame = t - self.latency_s

        off_pan = self.pan_sign * offset_deg_from_err(err_x, self.hfov_deg)
        off_tilt = self.tilt_sign * offset_deg_from_err(err_y, self.vfov_deg)

        with self._lock:
            self.pan_model.advance(t)
            self.tilt_model.advance(t)

            pan_at_frame = self.pan_model.at(t_frame)
            tilt_at_frame = self.tilt_model.at(t_frame)

            # Estimated true world angle of the face in servo coordinates
            face_pan = pan_at_frame + off_pan
            face_tilt = tilt_at_frame + off_tilt

            delta_pan = face_pan - self.pan_deg
            delta_tilt = face_tilt - self.tilt_deg

            new_pan = self.pan_deg
            new_tilt = self.tilt_deg
            moved = False

            if abs(delta_pan) > self.deadzone_deg:
                # Smoothly step past deadzone without snapping
                step_pan = math.copysign(abs(delta_pan) - self.deadzone_deg * 0.35, delta_pan) * self.gain
                cand_pan = max(PAN_MIN_DEG, min(PAN_MAX_DEG, self.pan_deg + step_pan))
                if abs(cand_pan - self.pan_deg) >= 0.4:
                    new_pan = cand_pan
                    moved = True

            if abs(delta_tilt) > self.deadzone_deg:
                step_tilt = math.copysign(abs(delta_tilt) - self.deadzone_deg * 0.35, delta_tilt) * self.gain
                cand_tilt = max(TILT_MIN_DEG, min(TILT_MAX_DEG, self.tilt_deg + step_tilt))
                if abs(cand_tilt - self.tilt_deg) >= 0.4:
                    new_tilt = cand_tilt
                    moved = True

            if apply and moved:
                self.pan_deg = new_pan
                self.tilt_deg = new_tilt
                self.pan_model.set_target(new_pan, now=t, slew_deg_per_sec=slew_deg_per_sec)
                self.tilt_model.set_target(new_tilt, now=t, slew_deg_per_sec=slew_deg_per_sec)

            return new_pan, new_tilt, moved

    def _smooth_gaze(self, err_x: float, err_y: float) -> tuple[float, float]:
        """Exponentially smooth eye gaze (gx, gy) with a tiny center deadband."""
        target_gx = 0.0 if abs(err_x) < 0.04 else max(-1.0, min(1.0, err_x * 1.35))
        target_gy = 0.0 if abs(err_y) < 0.04 else max(-1.0, min(1.0, -err_y * 1.35))
        a = self._gaze_alpha
        self._gaze_x += (target_gx - self._gaze_x) * a
        self._gaze_y += (target_gy - self._gaze_y) * a
        return round(self._gaze_x, 3), round(self._gaze_y, 3)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "robot_ip": self.robot_ip,
                "pan_deg": round(self.pan_deg, 1),
                "tilt_deg": round(self.tilt_deg, 1),
                "gaze_x": round(self._gaze_x, 3),
                "gaze_y": round(self._gaze_y, 3),
                "latency_s": self.latency_s,
                "hfov_deg": self.hfov_deg,
                "vfov_deg": self.vfov_deg,
                "deadzone_deg": self.deadzone_deg,
                "last_face": self.last_face,
                "last_mood": self.last_mood,
                "last_error": self.last_error,
            }

    def _run_loop(self) -> None:
        limits = httpx.Limits(max_keepalive_connections=4, max_connections=8, keepalive_expiry=30.0)
        timeout = httpx.Timeout(connect=0.8, read=1.2, write=0.8, pool=0.8)

        with httpx.Client(limits=limits, timeout=timeout) as client:
            last_mood_sent = ""
            last_mood_sent_time = 0.0
            last_face_seen_time = 0.0
            last_servo_sent_time = 0.0

            while not self._stop_event.is_set():
                with self._lock:
                    active = self.enabled
                    ip = self.robot_ip
                if not active or not ip:
                    time.sleep(0.15)
                    continue

                try:
                    cfg = robot_vision.load_camera_config()
                    source_type = cfg.get("source_type", "esp32")
                    raw_bytes = robot_vision.fetch_active_frame_bytes(ip, max_age=0.05)
                    if not raw_bytes:
                        time.sleep(0.08)
                        continue
                    arr = np.frombuffer(raw_bytes, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is None:
                        time.sleep(0.08)
                        continue

                    now = time.time()
                    h, w = frame.shape[:2]
                    detector, _ = robot_vision._get_models(w, h)
                    _, faces = detector.detect(frame)

                    if faces is not None and len(faces) > 0:
                        last_face_seen_time = now
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

                        moved = False
                        pan = self.pan_deg
                        tilt = self.tilt_deg
                        if source_type == "esp32":
                            can_send_servo = (now - last_servo_sent_time) >= TRACK_SEND_INTERVAL_S
                            pan, tilt, moved = self.compute_target_pose(
                                err_x,
                                err_y,
                                now=now,
                                apply=can_send_servo,
                                slew_deg_per_sec=TRACK_SLEW_DEG_PER_SEC,
                            )

                        with self._lock:
                            gx, gy = self._smooth_gaze(err_x, err_y)
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

                        payload: dict[str, Any] = {"gx": gx, "gy": gy}
                        if source_type == "esp32" and moved:
                            payload["pan"] = round(pan, 1)
                            payload["tilt"] = round(tilt, 1)
                            payload["speed"] = int(TRACK_SLEW_DEG_PER_SEC)
                            last_servo_sent_time = now

                        if mapped_mood != last_mood_sent or (now - last_mood_sent_time) > 3.0:
                            payload["mood"] = mapped_mood
                            last_mood_sent = mapped_mood
                            last_mood_sent_time = now

                        client.post(f"http://{ip}/face", json=payload)
                    else:
                        with self._lock:
                            self.last_face = None
                            self._gaze_x *= 0.7
                            self._gaze_y *= 0.7
                            if abs(self._gaze_x) < 0.02:
                                self._gaze_x = 0.0
                            if abs(self._gaze_y) < 0.02:
                                self._gaze_y = 0.0
                        if last_face_seen_time > 0 and (now - last_face_seen_time) > 3.5 and last_mood_sent != "default":
                            last_mood_sent = "default"
                            last_mood_sent_time = now
                            with self._lock:
                                self.last_mood = "default"
                                self._gaze_x = 0.0
                                self._gaze_y = 0.0
                            client.post(f"http://{ip}/face", json={"mood": "default", "gx": 0.0, "gy": 0.0})
                except Exception as exc:
                    with self._lock:
                        self.last_error = str(exc)

                time.sleep(0.04)  # Smooth ~20 Hz tracking cadence without starving ESP32 stream


FACE_TRACKER = MarvinFaceTracker()
