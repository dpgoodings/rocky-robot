"""
Robot Vision Engine: Real-Time Motion Detection & Biometric Facial Recognition
Powered by OpenCV 5 YuNet (Face Detection) + SFace (Face Recognition) ONNX models.

Integrates with ESP32-CAM (OV2640/OV5640) and external RTSP/HTTP cameras (e.g., Wyze).
Persists registered facial biometrics to SQLite `memory.db`.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading
import time
from typing import Any

import cv2
import numpy as np

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(PROJECT_DIR, "models", "vision")
DB_PATH = os.path.join(PROJECT_DIR, "memory.db")
CONFIG_PATH = os.path.join(PROJECT_DIR, "camera_config.json")

YUNET_MODEL = os.path.join(MODELS_DIR, "face_detection_yunet_2023mar.onnx")
SFACE_MODEL = os.path.join(MODELS_DIR, "face_recognition_sface_2021dec.onnx")
FER_MODEL = os.path.join(MODELS_DIR, "facial_expression_recognition_mobilefacenet_2022july.onnx")

_LOCK = threading.Lock()
_DETECTOR: cv2.FaceDetectorYN | None = None
_RECOGNIZER: cv2.FaceRecognizerSF | None = None
_FER_NET: cv2.dnn.Net | None = None

COSINE_THRESHOLD = 0.363  # OpenCV SFace official threshold for identity verification

_STD_5_LANDMARKS = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041]
], dtype=np.float32)

EXPRESSION_LABELS = ["angry", "disgust", "fearful", "happy", "neutral", "sad", "surprised"]
EXPRESSION_DESCRIPTIONS = {
    "happy": "smiling cheerfully",
    "neutral": "looking calm and attentive",
    "surprised": "looking surprised with eyes wide",
    "sad": "frowning or looking downcast",
    "angry": "looking stern or furrowed",
    "fearful": "looking startled",
    "disgust": "grimacing or looking skeptical",
}


# -----------------------------------------------------------------------------
# Database Management
# -----------------------------------------------------------------------------

def _init_face_db() -> None:
    with _LOCK:
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS known_faces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    embedding BLOB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP,
                    sample_count INTEGER DEFAULT 1
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


_init_face_db()


# -----------------------------------------------------------------------------
# OpenCV YuNet & SFace Lazy Initialization
# -----------------------------------------------------------------------------

def _ensure_models() -> None:
    os.makedirs(MODELS_DIR, exist_ok=True)
    urls = {
        YUNET_MODEL: "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        SFACE_MODEL: "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        FER_MODEL: "https://github.com/opencv/opencv_zoo/raw/main/models/facial_expression_recognition/facial_expression_recognition_mobilefacenet_2022july.onnx",
    }
    for path, url in urls.items():
        if not os.path.exists(path) or os.path.getsize(path) < 1000:
            import urllib.request
            try:
                urllib.request.urlretrieve(url, path)
            except Exception as e:
                print(f"[Vision] Error downloading model {path}: {e}")


def _get_fer_net() -> cv2.dnn.Net | None:
    global _FER_NET
    with _LOCK:
        _ensure_models()
        if _FER_NET is None and os.path.exists(FER_MODEL):
            try:
                _FER_NET = cv2.dnn.readNet(FER_MODEL)
            except Exception as e:
                print(f"[Vision] Could not load FER model: {e}")
        return _FER_NET


def classify_expression(frame: np.ndarray, landmarks: np.ndarray) -> tuple[str, float, str]:
    """
    Given an image frame and 5 facial landmarks (5, 2), aligns the face
    and classifies the facial expression into one of 7 categories.
    Returns: (label, probability, descriptive_phrase)
    """
    net = _get_fer_net()
    if net is None:
        return "neutral", 1.0, EXPRESSION_DESCRIPTIONS["neutral"]

    try:
        tfm, _ = cv2.estimateAffinePartial2D(landmarks.astype(np.float32), _STD_5_LANDMARKS)
        if tfm is None:
            return "neutral", 1.0, EXPRESSION_DESCRIPTIONS["neutral"]
        aligned = cv2.warpAffine(frame, tfm, (112, 112))

        blob = cv2.dnn.blobFromImage(
            cv2.cvtColor(aligned, cv2.COLOR_BGR2RGB),
            scalefactor=1.0 / (255.0 * 0.5),
            size=(112, 112),
            mean=(0.5 * 255.0, 0.5 * 255.0, 0.5 * 255.0),
            swapRB=False,
        )
        net.setInput(blob, "data")
        out = net.forward("label")[0]
        exp_vals = np.exp(out - np.max(out))
        probs = exp_vals / np.sum(exp_vals)
        top_idx = int(np.argmax(probs))
        label = EXPRESSION_LABELS[top_idx]
        prob = float(probs[top_idx])
        desc = EXPRESSION_DESCRIPTIONS.get(label, "looking at you")
        return label, round(prob, 2), desc
    except Exception:
        return "neutral", 1.0, EXPRESSION_DESCRIPTIONS["neutral"]

def _get_models(width: int = 320, height: int = 240) -> tuple[cv2.FaceDetectorYN, cv2.FaceRecognizerSF]:
    global _DETECTOR, _RECOGNIZER
    with _LOCK:
        _ensure_models()
        if _DETECTOR is None:
            if not os.path.exists(YUNET_MODEL):
                raise FileNotFoundError(f"YuNet model not found at {YUNET_MODEL}")
            _DETECTOR = cv2.FaceDetectorYN.create(
                model=YUNET_MODEL,
                config="",
                input_size=(width, height),
                score_threshold=0.65,
                nms_threshold=0.3,
                top_k=5000,
            )
        else:
            _DETECTOR.setInputSize((width, height))

        if _RECOGNIZER is None:
            if not os.path.exists(SFACE_MODEL):
                raise FileNotFoundError(f"SFace model not found at {SFACE_MODEL}")
            _RECOGNIZER = cv2.FaceRecognizerSF.create(model=SFACE_MODEL, config="")

        return _DETECTOR, _RECOGNIZER


# -----------------------------------------------------------------------------
# Facial Recognition API
# -----------------------------------------------------------------------------

def enroll_face_from_bytes(name: str, img_bytes: bytes) -> tuple[bool, str]:
    """Extracts face embedding from image bytes and registers it in SQLite."""
    clean_name = name.strip().title()
    if not clean_name:
        return False, "Name cannot be empty."

    nparr = np.frombuffer(img_bytes, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        return False, "Failed to decode image frame."

    return enroll_face(clean_name, frame)


def enroll_face(name: str, frame_bgr: np.ndarray) -> tuple[bool, str]:
    """Extracts face embedding from BGR frame and registers it in SQLite."""
    clean_name = name.strip().title()
    h, w = frame_bgr.shape[:2]
    detector, recognizer = _get_models(w, h)

    _, faces = detector.detect(frame_bgr)
    if faces is None or len(faces) == 0:
        return False, "No face detected in frame. Ensure good lighting and face camera directly."

    # Select the largest face detected
    faces_sorted = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    best_face = faces_sorted[0]

    # Align and extract 128-d feature embedding
    aligned = recognizer.alignCrop(frame_bgr, best_face)
    feat = recognizer.feature(aligned)
    feat_bytes = feat.astype(np.float32).tobytes()

    with _LOCK:
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO known_faces (name, embedding, created_at, last_seen, sample_count)
                VALUES (?, ?, datetime('now'), datetime('now'), 1)
                ON CONFLICT(name) DO UPDATE SET
                    embedding = excluded.embedding,
                    last_seen = datetime('now'),
                    sample_count = sample_count + 1
                """,
                (clean_name, feat_bytes),
            )
            conn.commit()
            return True, f"Successfully registered face for '{clean_name}'!"
        finally:
            conn.close()


def recognize_faces(frame_bgr: np.ndarray) -> list[dict[str, Any]]:
    """Detects and matches all faces in the given frame against the known database."""
    h, w = frame_bgr.shape[:2]
    detector, recognizer = _get_models(w, h)

    _, faces = detector.detect(frame_bgr)
    if faces is None or len(faces) == 0:
        return []

    # Load enrolled faces
    with _LOCK:
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name, embedding FROM known_faces")
            db_faces = cur.fetchall()
        finally:
            conn.close()

    known = []
    for name, emb_bytes in db_faces:
        feat = np.frombuffer(emb_bytes, dtype=np.float32).reshape(1, 128)
        known.append((name, feat))

    results = []
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")

    for f in faces:
        bbox = [int(f[0]), int(f[1]), int(f[2]), int(f[3])]
        score = float(f[14])
        aligned = recognizer.alignCrop(frame_bgr, f)
        target_feat = recognizer.feature(aligned)

        # Facial Expression Recognition (MobileFaceNet)
        landmarks = f[4:14].reshape(5, 2)
        expr_label, expr_prob, expr_desc = classify_expression(frame_bgr, landmarks)

        best_name = "Unknown"
        best_sim = 0.0

        for name, k_feat in known:
            sim = float(recognizer.match(k_feat, target_feat, cv2.FaceRecognizerSF_FR_COSINE))
            if sim > best_sim:
                best_sim = sim
                if sim >= COSINE_THRESHOLD:
                    best_name = name

        results.append({
            "name": best_name,
            "confidence": round(best_sim, 3),
            "detection_score": round(score, 3),
            "bbox": bbox,
            "expression": expr_label,
            "expression_prob": expr_prob,
            "expression_desc": expr_desc,
        })

        if best_name != "Unknown":
            # Update last_seen
            with _LOCK:
                conn = sqlite3.connect(DB_PATH)
                try:
                    cur = conn.cursor()
                    cur.execute("UPDATE known_faces SET last_seen = ? WHERE name = ?", (now_str, best_name))
                    conn.commit()
                except Exception:
                    pass
                finally:
                    conn.close()

    return results


def list_enrolled_faces() -> list[dict[str, Any]]:
    """Lists all registered identities in memory."""
    with _LOCK:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, name, created_at, last_seen, sample_count FROM known_faces ORDER BY name ASC")
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()


def delete_enrolled_face(name: str) -> bool:
    """Removes a registered identity from memory."""
    with _LOCK:
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM known_faces WHERE lower(name) = lower(?)", (name.strip(),))
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        finally:
            conn.close()


# -----------------------------------------------------------------------------
# Camera Configuration & Frame Acquisition
# -----------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "source_type": "esp32",  # "esp32" | "rtsp" | "http" | "webcam"
    "custom_url": "",        # Custom RTSP URL (e.g., rtsp://wyze-cam:8554/live) or snapshot URL
    "motion_guard_enabled": False,
    "motion_sensitivity": 0.04,  # Ratio of pixels changed (0.01 = high, 0.10 = low)
    "voice_announcements": False,  # Default to False: do not interrupt room/conversations with canned speech
    "cooldown_seconds": 60,
}


_LAST_CONVERSATION_TIME: float = 0.0
_CACHED_IDENTITY: str | None = None
_IDENTITY_TIMESTAMP: float = 0.0


def mark_conversation_active() -> None:
    """Marks that the user is actively talking with the robot, suppressing canned alerts."""
    global _LAST_CONVERSATION_TIME
    _LAST_CONVERSATION_TIME = time.time()


def is_conversation_active(window_sec: float = 90.0) -> bool:
    """Returns True if the user has spoken with the robot recently."""
    return (time.time() - _LAST_CONVERSATION_TIME) < window_sec


_CACHED_VIS_CONTEXT: dict[str, Any] | None = None
_VIS_CONTEXT_TIMESTAMP: float = 0.0


def get_current_visual_context(max_age: float = 2.0) -> dict[str, Any]:
    """
    Returns rich optical perception of who and what is visible:
    {
        "in_view": bool,
        "name": str | None,            # e.g. "David", "Unknown", None
        "expression": str | None,      # e.g. "happy", "neutral", "surprised", "sad", "angry"
        "expression_desc": str | None, # e.g. "smiling cheerfully", "looking calm and attentive"
        "confidence": float,
    }
    """
    global _CACHED_VIS_CONTEXT, _VIS_CONTEXT_TIMESTAMP
    now = time.time()
    if _CACHED_VIS_CONTEXT is not None and (now - _VIS_CONTEXT_TIMESTAMP) < max_age:
        return _CACHED_VIS_CONTEXT

    # 1. Check background sentry recent results
    try:
        status = GUARD_ENGINE.last_status
        recent_faces = status.get("recognized_faces", [])
        if recent_faces and (now - getattr(GUARD_ENGINE, "last_scan_time", 0)) < 4.0:
            top = recent_faces[0]
            name = top.get("name")
            res = {
                "in_view": True,
                "name": name if name != "Unknown" else "Unknown",
                "expression": top.get("expression", "neutral"),
                "expression_desc": top.get("expression_desc", "looking attentive"),
                "confidence": top.get("confidence", 0.0),
            }
            _CACHED_VIS_CONTEXT = res
            _VIS_CONTEXT_TIMESTAMP = now
            return res
    except Exception:
        pass

    # 2. Check live frame
    try:
        frame = fetch_live_camera_frame()
        if frame is not None:
            faces = recognize_faces(frame)
            if faces:
                top = faces[0]
                name = top.get("name")
                res = {
                    "in_view": True,
                    "name": name if name != "Unknown" else "Unknown",
                    "expression": top.get("expression", "neutral"),
                    "expression_desc": top.get("expression_desc", "looking attentive"),
                    "confidence": top.get("confidence", 0.0),
                }
                _CACHED_VIS_CONTEXT = res
                _VIS_CONTEXT_TIMESTAMP = now
                return res
    except Exception:
        pass

    res = {
        "in_view": False,
        "name": None,
        "expression": None,
        "expression_desc": None,
        "confidence": 0.0,
    }
    _CACHED_VIS_CONTEXT = res
    _VIS_CONTEXT_TIMESTAMP = now
    return res


def get_current_visual_identity(max_age: float = 2.0) -> str | None:
    ctx = get_current_visual_context(max_age=max_age)
    if ctx.get("in_view"):
        return ctx.get("name")
    return None


def load_camera_config() -> dict[str, Any]:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
                return {**DEFAULT_CONFIG, **data}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_camera_config(cfg: dict[str, Any]) -> None:
    merged = {**load_camera_config(), **cfg}
    with open(CONFIG_PATH, "w") as f:
        json.dump(merged, f, indent=2)


_UPLOADED_FRAME_BYTES: bytes | None = None
_UPLOADED_FRAME_TIME: float = 0.0


def store_uploaded_frame(jpeg_bytes: bytes) -> None:
    """Stores a live camera frame uploaded from browser or client (e.g. MacBook Pro FaceTime HD)."""
    global _UPLOADED_FRAME_BYTES, _UPLOADED_FRAME_TIME
    _UPLOADED_FRAME_BYTES = jpeg_bytes
    _UPLOADED_FRAME_TIME = time.time()


def get_latest_uploaded_frame_bytes(max_age: float = 3.5) -> bytes | None:
    global _UPLOADED_FRAME_BYTES, _UPLOADED_FRAME_TIME
    if _UPLOADED_FRAME_BYTES and (time.time() - _UPLOADED_FRAME_TIME) <= max_age:
        return _UPLOADED_FRAME_BYTES
    return None


def fetch_live_camera_frame() -> np.ndarray | None:
    """Fetches a current BGR frame from the configured camera (MacBook Pro, ESP32, RTSP Wyze, or HTTP)."""
    cfg = load_camera_config()
    source_type = cfg.get("source_type", "esp32")

    # 1. MacBook Pro FaceTime HD / Local USB Camera
    if source_type in ("macbook", "webcam", "local"):
        # Check uploaded frame from browser HUD first (fastest, full hardware permission in browser)
        up_bytes = get_latest_uploaded_frame_bytes(max_age=4.0)
        if up_bytes:
            try:
                nparr = np.frombuffer(up_bytes, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if frame is not None:
                    return frame
            except Exception:
                pass

        # Fallback to local OpenCV capture device 0
        try:
            device_idx = int(cfg.get("device_index", 0))
            cap = cv2.VideoCapture(device_idx)
            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()
                if ret and frame is not None:
                    return frame
        except Exception:
            pass

    # 2. Custom RTSP / Video Stream (e.g., Wyze camera or IP stream)
    if source_type == "rtsp" and cfg.get("custom_url"):
        try:
            cap = cv2.VideoCapture(cfg["custom_url"])
            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()
                if ret and frame is not None:
                    return frame
        except Exception:
            pass

    # 3. Custom HTTP Snapshot URL
    if source_type == "http" and cfg.get("custom_url"):
        try:
            import httpx
            with httpx.Client(timeout=3.0) as client:
                res = client.get(cfg["custom_url"])
                if res.status_code == 200:
                    nparr = np.frombuffer(res.content, np.uint8)
                    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        except Exception:
            pass

    # 4. Physical ESP32-CAM
    try:
        from brain import resolve_robot_ip
        import httpx
        robot_ip = resolve_robot_ip()
        with httpx.Client(timeout=2.5) as client:
            resp = client.get(f"http://{robot_ip}/capture")
            if resp.status_code == 200:
                nparr = np.frombuffer(resp.content, np.uint8)
                return cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    except Exception:
        pass

    return None


# -----------------------------------------------------------------------------
# Background Motion Detection & Sentry Engine
# -----------------------------------------------------------------------------

class MotionGuardEngine:
    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._bg_avg: np.ndarray | None = None
        self._last_alert_time = 0.0
        self.last_scan_time = 0.0
        self.last_status: dict[str, Any] = {
            "motion_detected": False,
            "motion_score": 0.0,
            "recognized_faces": [],
            "last_event_time": None,
            "alert_active": False,
        }

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="motion_guard_thread")
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            cfg = load_camera_config()
            if not cfg.get("motion_guard_enabled", False):
                time.sleep(1.0)
                continue

            try:
                frame = fetch_live_camera_frame()
                if frame is None:
                    time.sleep(1.5)
                    continue

                # Downscale for fast low-power frame differencing
                small = cv2.resize(frame, (320, 240))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                blur = cv2.GaussianBlur(gray, (21, 21), 0)

                if self._bg_avg is None:
                    self._bg_avg = blur.astype("float")
                    time.sleep(0.5)
                    continue

                # Accumulate weighted running average background
                cv2.accumulateWeighted(blur, self._bg_avg, 0.15)
                frame_delta = cv2.absdiff(blur, cv2.convertScaleAbs(self._bg_avg))
                thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)[1]
                thresh = cv2.dilate(thresh, None, iterations=2)

                non_zero = np.count_nonzero(thresh)
                total_pixels = 320 * 240
                motion_score = round(non_zero / total_pixels, 4)
                sensitivity = float(cfg.get("motion_sensitivity", 0.04))

                is_motion = motion_score >= sensitivity
                self.last_status["motion_score"] = motion_score
                self.last_status["motion_detected"] = is_motion

                if is_motion:
                    now = time.time()
                    cooldown = float(cfg.get("cooldown_seconds", 60))

                    # Run facial recognition on the high-res frame
                    faces = recognize_faces(frame)
                    self.last_scan_time = now
                    self.last_status["recognized_faces"] = faces
                    self.last_status["last_event_time"] = time.strftime("%Y-%m-%d %H:%M:%S")

                    if (now - self._last_alert_time) >= cooldown:
                        self._last_alert_time = now
                        self._trigger_motion_alert(faces, motion_score, cfg)

                time.sleep(0.6)
            except Exception as exc:
                time.sleep(1.5)

    def _trigger_motion_alert(self, faces: list[dict[str, Any]], score: float, cfg: dict[str, Any]) -> None:
        """Dispatches proactive speech announcement ONLY when Guard mode is active and user is not conversing."""
        if not cfg.get("motion_guard_enabled", False):
            return
        if not cfg.get("voice_announcements", False):
            return
        # Suppress canned alerts if the user was recently in a conversation
        if is_conversation_active(90.0):
            return

        known_names = [f["name"] for f in faces if f["name"] != "Unknown"]
        greeting_text = ""

        if known_names:
            primary_name = known_names[0]
            greeting_text = f"Notice: {primary_name} has entered the room."
        elif faces:
            greeting_text = "Notice: Unrecognized person detected."
        else:
            greeting_text = "Notice: Movement detected in room."

        # Trigger speech through local server if available
        try:
            import httpx
            with httpx.Client(timeout=4.0) as client:
                client.post(
                    "http://127.0.0.1:7860/api/text-turn-stream",
                    json={
                        "text": f"(Sentry Alert: {greeting_text} Announce this in your 1-sentence character persona.)",
                        "character": "rocky",
                        "say_exact": False,
                        "play_host_speaker": True,
                    },
                )
        except Exception:
            pass


GUARD_ENGINE = MotionGuardEngine()
GUARD_ENGINE.start()
