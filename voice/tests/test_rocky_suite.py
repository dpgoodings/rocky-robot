"""Offline unit test suite for Rocky-S3 voice brain, tracking, body language, turn detection & Eridian filter.

Runs 100% offline with zero hardware, camera, Ollama, or network dependencies:
    /Users/dad/rocky-robot/voice/.venv/bin/python -m unittest discover -s /Users/dad/rocky-robot/voice/tests -v
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

VOICE_DIR = Path(__file__).resolve().parent.parent
if str(VOICE_DIR) not in sys.path:
    sys.path.insert(0, str(VOICE_DIR))

import brain
import body_language
import character_registry
import face_tracker
import rocky_filter
import turn_detector


# =============================================================================
# 1. Rocky-Only Character Registry
# =============================================================================
class TestCharacterRegistry(unittest.TestCase):
    def test_only_rocky_json_exists_on_disk(self):
        char_dir = VOICE_DIR / "characters"
        files = sorted(p.name for p in char_dir.iterdir() if p.is_file())
        self.assertEqual(files, ["rocky.json"])
        raw = json.loads((char_dir / "rocky.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["id"], "rocky")
        self.assertEqual(raw["ui"]["face_style"], "rocky_v2")

    def test_registry_is_rocky_only(self):
        reg = character_registry.REGISTRY
        self.assertEqual(reg.default_id, "rocky")
        self.assertEqual(reg.ids(), ["rocky"])
        self.assertTrue(reg.has("rocky"))
        self.assertTrue(reg.has("ROCKY"))
        for removed in ("marvin", "bmo", "tars", "antigravity"):
            with self.subTest(removed=removed):
                self.assertFalse(reg.has(removed))
                # Always falls back safely to canonical rocky
                self.assertEqual(reg.get(removed).id, "rocky")

    def test_public_map_has_rocky_v2_metadata(self):
        pub = character_registry.REGISTRY.public_map()
        self.assertEqual(list(pub.keys()), ["rocky"])
        rocky = pub["rocky"]
        self.assertEqual(rocky["name"], "Rocky")
        self.assertEqual(rocky["ui"]["face_style"], "rocky_v2")
        self.assertIn("rocky", [w.lower() for w in rocky["wake_words"]])


# =============================================================================
# 2. Brain Deterministic Routing
# =============================================================================
class TestBrainRouting(unittest.TestCase):
    def setUp(self):
        # Construct RockyBrain without launching Ollama or background threads
        self.b = brain.RockyBrain.__new__(brain.RockyBrain)
        self.b.histories = {"rocky": []}
        self.b._last_turn_was_vision = {"rocky": False}
        self.b._last_vision_query = {}

    def test_all_14_robot_face_expressions(self):
        cases = [
            ("show me your neutral face", "neutral"),
            ("show me your happy face", "happy"),
            ("show me your excited face", "excited"),
            ("show me your curious face", "curious"),
            ("show me your thinking face", "thinking"),
            ("show me your surprised face", "surprised"),
            ("show me your worried face", "worried"),
            ("show me your sad face", "sad"),
            ("show me your angry face", "angry"),
            ("show me your skeptical face", "skeptical"),
            ("show me your love face", "love"),
            ("show me your confused face", "confused"),
            ("show me your sleepy face", "sleepy"),
            ("show me your asleep face", "asleep"),
        ]
        for utterance, expected_mood in cases:
            with self.subTest(utterance=utterance, expected_mood=expected_mood):
                self.assertEqual(self.b.classify_intent(utterance), "robot_face_expr")
                parsed = brain.parse_robot_face_expression(utterance)
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed["hud_mood"], expected_mood)
                self.assertEqual(parsed["esp_mood"], expected_mood)

    def test_dance_intent_and_routines(self):
        dance_cases = [
            ("dance", "dance"),
            ("Rocky, dance!", "dance"),
            ("do a dance", "dance"),
            ("show me your moves", "dance"),
            ("boogie", "dance"),
            ("do a groove dance", "dance_groove"),
            ("do the robot", "dance_robot"),
            ("do the sprinkler", "dance_sprinkler"),
            ("do a disco dance", "dance_disco"),
        ]
        for utterance, expected_action in dance_cases:
            with self.subTest(utterance=utterance):
                self.assertEqual(self.b.classify_intent(utterance), "dance")
                self.assertEqual(brain.parse_dance_intent(utterance), expected_action)

        # Informational questions about dancing must NOT trigger physical dance
        for info_q in ("what is a dance?", "why do people dance?", "explain disco dance"):
            with self.subTest(info_q=info_q):
                self.assertIsNone(brain.parse_dance_intent(info_q))
                self.assertEqual(self.b.classify_intent(info_q), "chat")

    def test_head_look_intent_and_look_plus_vision(self):
        look_cases = [
            ("look left", "left", 135, 90),
            ("turn your head right", "right", 45, 90),
            ("look up", "up", 90, 115),
            ("look down", "down", 90, 72),
            ("center your head", "center", 90, 90),
            ("look straight ahead", "center", 90, 90),
        ]
        for utterance, direction, pan, tilt in look_cases:
            with self.subTest(utterance=utterance):
                self.assertEqual(self.b.classify_intent(utterance), "head_look")
                parsed = brain.parse_head_look_intent(utterance)
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed["direction"], direction)
                self.assertEqual((parsed["pan"], parsed["tilt"]), (pan, tilt))
                self.assertFalse(parsed["wants_vision"])

        combo = "look left and tell me what you see"
        self.assertEqual(self.b.classify_intent(combo), "head_look_vision")
        parsed_combo = brain.parse_head_look_intent(combo)
        self.assertIsNotNone(parsed_combo)
        self.assertEqual(parsed_combo["direction"], "left")
        self.assertEqual((parsed_combo["pan"], parsed_combo["tilt"]), (135, 90))
        self.assertTrue(parsed_combo["wants_vision"])

    def test_track_me_and_stop_tracking(self):
        for start_phrase in ("track me", "follow my face", "watch me", "start face tracking"):
            with self.subTest(start_phrase=start_phrase):
                self.assertIs(brain.parse_tracking_intent(start_phrase), True)
                self.assertEqual(self.b.classify_intent(start_phrase), "track_start")

        for stop_phrase in ("stop tracking", "stop following", "stop following my face", "stop watching me"):
            with self.subTest(stop_phrase=stop_phrase):
                self.assertIs(brain.parse_tracking_intent(stop_phrase), False)
                self.assertEqual(self.b.classify_intent(stop_phrase), "track_stop")

    def test_vision_queries_and_followups(self):
        for vq in (
            "what do you see?",
            "what am I holding?",
            "describe the room",
            "how many fingers am I holding up?",
            "what color is my shirt?",
            "Can you tell me what I'm doing with it?",
            "Do you know what this?",
            "Do you know what this is?",
            "What is this?",
            "Can you read the whole sign?",
            "Can you read the last four lines on the card?",
            "Can you read the last four sentences?",
            "What does this card say?",
        ):
            with self.subTest(vq=vq):
                self.assertEqual(self.b.classify_intent(vq), "vision")

        # Follow-ups only route to vision_followup when the prior turn was vision
        self.b._last_turn_was_vision["rocky"] = False
        self.assertEqual(self.b.classify_intent("how about now?"), "chat")
        self.assertEqual(self.b.classify_intent("look again"), "chat")

        self.b._last_turn_was_vision["rocky"] = True
        self.assertEqual(self.b.classify_intent("how about now?"), "vision_followup")
        self.assertEqual(self.b.classify_intent("look again"), "vision_followup")
        self.assertEqual(self.b.classify_intent("Can you finish it?"), "vision_followup")
        self.assertEqual(self.b.classify_intent("And then what?"), "vision_followup")

    def test_negative_non_collisions(self):
        # User asking about their OWN smile must go to face_expr, NOT robot_face_expr
        self.assertIsNone(brain.parse_robot_face_expression("am I smiling?"))
        self.assertEqual(self.b.classify_intent("am I smiling?"), "face_expr")
        self.assertEqual(self.b.classify_intent("do I look happy?"), "face_expr")

        # General knowledge / chat must stay chat
        for q in (
            "why is the sky blue?",
            "tell me a joke about space",
            "what is the distance to Tau Ceti?",
            "how does gravity work?",
        ):
            with self.subTest(q=q):
                self.assertEqual(self.b.classify_intent(q), "chat")

        # Time & date routing + deterministic calendar accuracy
        for tq in (
            "what time is it?",
            "what day is it?",
            "what day of the week is it?",
            "I'm at what day a week it is.",
            "is today Sunday?",
            "what's today's date?",
        ):
            with self.subTest(tq=tq):
                self.assertEqual(self.b.classify_intent(tq), "time")
        from datetime import datetime as _dt
        sat = _dt(2026, 11, 21, 13, 25)
        self.assertIn("Saturday", brain._format_time_date_reply("what day is it?", now=sat))
        self.assertIn("Not Sunday", brain._format_time_date_reply("is today Sunday?", now=sat))
        self.assertIn("1:25 PM", brain._format_time_date_reply("what time is it?", now=sat))
        self.assertEqual(self.b.classify_intent("who am I?"), "face_recog")


# =============================================================================
# 3. Face Tracker SlewModel & Predictive FOV Calculation
# =============================================================================
class TestFaceTrackerPredictive(unittest.TestCase):
    def test_fov_offset_math(self):
        self.assertAlmostEqual(face_tracker.offset_deg_from_err(0.0, 58.0), 0.0, places=6)
        self.assertAlmostEqual(face_tracker.offset_deg_from_err(1.0, 58.0), 29.0, places=5)
        self.assertAlmostEqual(face_tracker.offset_deg_from_err(-1.0, 58.0), -29.0, places=5)
        self.assertAlmostEqual(face_tracker.offset_deg_from_err(1.0, 44.0), 22.0, places=5)
        # Symmetry & monotonicity
        a = face_tracker.offset_deg_from_err(0.3, 58.0)
        b = face_tracker.offset_deg_from_err(0.7, 58.0)
        self.assertGreater(b, a)
        self.assertAlmostEqual(face_tracker.offset_deg_from_err(-0.3, 58.0), -a, places=6)

    def test_slew_model_motion_history_and_clamping(self):
        t0 = 1000.0
        m = face_tracker.SlewModel(initial_deg=90.0, slew_deg_per_sec=200.0, min_deg=15.0, max_deg=165.0, now=t0)
        self.assertEqual(m.arrival_in(t0), 0.0)

        m.set_target(130.0, now=t0)
        self.assertAlmostEqual(m.arrival_in(t0), 0.2, places=3)

        # Midpoint at +0.10s should be ~110 deg
        mid = m.advance(t0 + 0.10)
        self.assertAlmostEqual(mid, 110.0, delta=1.0)

        # Arrives at +0.22s
        end = m.advance(t0 + 0.22)
        self.assertAlmostEqual(end, 130.0, places=4)

        # Historical interpolation via pose_history
        self.assertAlmostEqual(m.at(t0), 90.0, delta=0.5)
        self.assertAlmostEqual(m.at(t0 + 0.10), 110.0, delta=1.5)

        # Clamping limits
        m.set_target(999.0, now=t0 + 0.30)
        self.assertEqual(m.target, 165.0)
        m.set_target(-50.0, now=t0 + 0.40)
        self.assertEqual(m.target, 15.0)

    def test_latency_compensation_reduces_overshoot_vs_naive(self):
        """Simulate a stationary face 22° off-center with 0.25s camera MJPEG lag."""
        t0 = 2000.0
        true_face_pan = 68.0  # 22 deg to the right of 90 deg center
        lag_s = face_tracker.TRACK_LATENCY_S
        dt = 0.06  # ~16 Hz update cadence
        hfov = face_tracker.TRACK_HFOV_DEG
        tan_half = math.tan(math.radians(hfov * 0.5))

        # --- Predictive tracker ---
        ft = face_tracker.MarvinFaceTracker()
        ft.pan_deg = 90.0
        ft.tilt_deg = 92.0
        ft.pan_model.snap(90.0, now=t0)
        ft.tilt_model.snap(92.0, now=t0)

        plant_pred = face_tracker.SlewModel(initial_deg=90.0, slew_deg_per_sec=240.0, now=t0)
        pred_min_pan = 90.0
        for step in range(30):
            t = t0 + (step + 1) * dt
            plant_pred.advance(t)
            cam_pan = plant_pred.at(t - lag_s)
            # True world angle - camera angle -> pixel error (pan_sign = -1)
            ang_err = cam_pan - true_face_pan
            err_x = math.tan(math.radians(ang_err)) / tan_half
            pan_cmd, _, moved = ft.compute_target_pose(err_x, 0.0, now=t, apply=True)
            if moved:
                plant_pred.set_target(pan_cmd, now=t)
            pred_min_pan = min(pred_min_pan, plant_pred.pos)
        pred_overshoot = max(0.0, true_face_pan - pred_min_pan)

        # --- Naive proportional stepping (old `pan -= err_x * 12`) ---
        plant_naive = face_tracker.SlewModel(initial_deg=90.0, slew_deg_per_sec=240.0, now=t0)
        naive_pan = 90.0
        naive_min_pan = 90.0
        for step in range(30):
            t = t0 + (step + 1) * dt
            plant_naive.advance(t)
            cam_pan = plant_naive.at(t - lag_s)
            ang_err = cam_pan - true_face_pan
            err_x = math.tan(math.radians(ang_err)) / tan_half
            if abs(err_x) > 0.08:
                naive_pan = max(15.0, min(165.0, naive_pan - err_x * 12.0))
                plant_naive.set_target(naive_pan, now=t)
            naive_min_pan = min(naive_min_pan, plant_naive.pos)
        naive_overshoot = max(0.0, true_face_pan - naive_min_pan)

        self.assertLess(pred_overshoot, 2.0, f"Predictive overshoot too high: {pred_overshoot:.2f}°")
        self.assertGreater(naive_overshoot, pred_overshoot + 10.0)
        self.assertAlmostEqual(plant_pred.pos, true_face_pan, delta=4.0)

    def test_deadzone_and_limits_and_manual_sync(self):
        t0 = 3000.0
        ft = face_tracker.MarvinFaceTracker()
        ft.pan_deg = 90.0
        ft.tilt_deg = 92.0
        ft.pan_model.snap(90.0, now=t0)
        ft.tilt_model.snap(92.0, now=t0)

        # Tiny error inside 3.5 deg deadzone should NOT move
        p, ti, moved = ft.compute_target_pose(0.03, 0.03, now=t0 + 0.1)
        self.assertFalse(moved)
        self.assertEqual((p, ti), (90.0, 92.0))

        # Extreme manual poses clamp to physical limits
        ft.set_manual_pose(pan=200.0, tilt=20.0, now=t0 + 0.2)
        self.assertEqual(ft.pan_deg, face_tracker.PAN_MAX_DEG)
        self.assertEqual(ft.tilt_deg, face_tracker.TILT_MIN_DEG)
        self.assertEqual(ft.pan_model.target, face_tracker.PAN_MAX_DEG)
        self.assertEqual(ft.tilt_model.target, face_tracker.TILT_MIN_DEG)

        # Extreme target calculation still stays within limits
        ft.pan_model.snap(face_tracker.PAN_MIN_DEG, now=t0 + 0.5)
        ft.tilt_model.snap(face_tracker.TILT_MAX_DEG, now=t0 + 0.5)
        ft.pan_deg = face_tracker.PAN_MIN_DEG
        ft.tilt_deg = face_tracker.TILT_MAX_DEG
        p2, ti2, _ = ft.compute_target_pose(1.5, -1.5, now=t0 + 0.6)
        self.assertGreaterEqual(p2, face_tracker.PAN_MIN_DEG)
        self.assertLessEqual(p2, face_tracker.PAN_MAX_DEG)
        self.assertGreaterEqual(ti2, face_tracker.TILT_MIN_DEG)
        self.assertLessEqual(ti2, face_tracker.TILT_MAX_DEG)


# =============================================================================
# 4. Adaptive Turn Detector & Body Language Suppression
# =============================================================================
class TestTurnDetectorAndBodyLanguage(unittest.TestCase):
    @staticmethod
    def _synth_voice(dur_s: float, f0_start: float, f0_end: float, amp_start: float, amp_end: float) -> np.ndarray:
        sr = turn_detector.SAMPLE_RATE
        n = int(dur_s * sr)
        t = np.linspace(0.0, dur_s, n, endpoint=False, dtype=np.float64)
        # Instantaneous phase for linear chirp f0_start -> f0_end
        phase = 2.0 * np.pi * (f0_start * t + 0.5 * (f0_end - f0_start) * t * t / max(dur_s, 1e-6))
        env = np.linspace(amp_start, amp_end, n, dtype=np.float64)
        sig = env * (0.7 * np.sin(phase) + 0.3 * np.sin(2.0 * phase))
        return sig.astype(np.float32)

    def test_turn_detector_complete_vs_mid_thought_and_bounds(self):
        td = turn_detector.AdaptiveTurnDetector(use_onnx=False)
        st = td.status()
        self.assertEqual(st["method"], "acoustic_prosody")
        self.assertIn("thresholds", st)

        # Completed utterance: 1.2s, falling pitch (180 -> 115 Hz), tapering energy
        done_audio = self._synth_voice(1.2, 180.0, 115.0, 0.25, 0.06)
        res_done = td.analyze_utterance(done_audio, silence_ms=420.0)
        self.assertTrue(res_done["complete"], f"Expected complete=True, got {res_done}")
        self.assertEqual(res_done["recommended_wait_ms"], 0)
        self.assertEqual(res_done["method"], "acoustic_prosody")

        # Mid-thought pause: short 0.32s sustained vowel ("uhhh..."), rising pitch (140 -> 165 Hz), flat energy
        mid_audio = self._synth_voice(0.32, 140.0, 165.0, 0.20, 0.20)
        res_mid = td.analyze_utterance(mid_audio, silence_ms=350.0)
        self.assertFalse(res_mid["complete"], f"Expected complete=False, got {res_mid}")
        self.assertGreater(res_mid["recommended_wait_ms"], 0)

        # Silence shorter than min_silence_ms is never complete
        res_short_sil = td.analyze_utterance(done_audio, silence_ms=100.0)
        self.assertFalse(res_short_sil["complete"])
        self.assertGreater(res_short_sil["recommended_wait_ms"], 0)

        # Silence hitting max_silence_ms ceiling always completes immediately
        res_ceil = td.analyze_utterance(mid_audio, silence_ms=float(td.max_silence_ms))
        self.assertTrue(res_ceil["complete"])
        self.assertEqual(res_ceil["recommended_wait_ms"], 0)

    def test_body_language_mappings_and_suppression_during_tracking(self):
        sent: list[tuple[str, dict]] = []
        evt = threading.Event()

        def fake_post(url: str, payload: dict) -> None:
            sent.append((url, payload))
            evt.set()

        bl = body_language.RockyBodyLanguage(post_fn=fake_post)
        self.assertGreaterEqual(body_language.SWAY_MIN_INTERVAL_S, 2.0)

        # Emotion gesture mapping
        self.assertEqual(bl.gesture_for_emotion("happy"), "happy_bounce")
        self.assertEqual(bl.gesture_for_emotion("excited"), "happy_bounce")
        self.assertEqual(bl.gesture_for_emotion("sad"), "sad_droop")
        self.assertEqual(bl.gesture_for_emotion("surprised"), "surprised_pop")
        self.assertEqual(bl.gesture_for_emotion("curious"), "curious_tilt")
        self.assertEqual(bl.gesture_for_emotion("angry"), "shake")
        self.assertEqual(bl.gesture_for_emotion("neutral"), "nod")
        self.assertEqual(bl.gesture_for_emotion("love"), "nod")
        self.assertIsNone(bl.gesture_for_emotion("asleep"))

        # Suppressed when FACE_TRACKER.enabled is True
        with patch.object(face_tracker.FACE_TRACKER, "enabled", True):
            self.assertTrue(bl.is_suppressed())
            self.assertFalse(bl.on_thinking("192.168.4.40"))
            self.assertFalse(bl.on_speak_start("excited", "192.168.4.40"))
            time.sleep(0.05)
            self.assertEqual(sent, [])

        # Active when FACE_TRACKER.enabled is False
        with patch.object(face_tracker.FACE_TRACKER, "enabled", False):
            self.assertFalse(bl.is_suppressed())
            evt.clear()
            self.assertTrue(bl.on_thinking("192.168.4.40"))
            self.assertTrue(evt.wait(timeout=1.0))
            self.assertTrue(sent[-1][0].endswith("/servo"))

            evt.clear()
            self.assertTrue(bl.on_speak_start("excited", "192.168.4.40"))
            self.assertTrue(evt.wait(timeout=1.0))
            self.assertTrue(sent[-1][0].endswith("/action"))
            self.assertEqual(sent[-1][1], {"action": "happy_bounce"})

            evt.clear()
            self.assertTrue(bl.on_speak_stop())
            self.assertTrue(evt.wait(timeout=1.0))
            self.assertTrue(sent[-1][0].endswith("/servo"))

            # Disabled via toggle sends nothing
            sent.clear()
            bl.set_enabled(False)
            self.assertTrue(bl.is_suppressed())
            self.assertFalse(bl.on_speak_start("happy", "192.168.4.40"))
            time.sleep(0.05)
            self.assertEqual(sent, [])


# =============================================================================
# 5. Rocky Filter: Eridian Tone, RLHF Stripping & Wake-Word Parsing
# =============================================================================
class TestRockyFilter(unittest.TestCase):
    def test_eridian_tone_transformations(self):
        out = rocky_filter.apply_rocky_filter("That is amazing! Are you ready?", intensity="balanced")
        self.assertIn("amaze amaze amaze", out.lower())
        self.assertTrue(out.endswith(", question?"), f"Expected ', question?' suffix, got: {out}")

        rewritten = rocky_filter.apply_rocky_filter("I don't understand why it is terrible.", intensity="balanced")
        self.assertIn("rocky no understand", rewritten.lower())
        self.assertIn("bad bad bad", rewritten.lower())

        # Off preserves clean text without Eridian emphasis/question suffix
        off = rocky_filter.apply_rocky_filter("That is amazing! Are you ready?", intensity="off")
        self.assertNotIn("amaze amaze amaze", off.lower())
        self.assertFalse(off.endswith(", question?"))

        # Strict drops copula verbs ("is"/"are")
        strict = rocky_filter.apply_rocky_filter("The sky is blue.", intensity="strict")
        self.assertNotIn(" is ", f" {strict.lower()} ")
        self.assertIn("blue", strict.lower())

        # Hardware jargon rewrite & mid-sentence continuation casing
        jargon = rocky_filter.apply_rocky_filter("My ESP32 camera eyes and ESP32 camera ears see clear.")
        self.assertNotIn("esp32", jargon.lower())
        self.assertIn("Rocky eyes", jargon)
        self.assertIn("Rocky ears", jargon)
        cont = rocky_filter.apply_rocky_filter("my eyes see clearly", capitalize_start=False)
        self.assertTrue(cont.startswith("my "))

    def test_rlhf_capability_disclaimer_stripping(self):
        noisy = (
            "As an AI language model, I cannot physically make faces like humans do. "
            "My responses are based on programmed text instructions. "
            "Rocky loves talking with David!"
        )
        cleaned = rocky_filter.apply_rocky_filter(noisy, intensity="balanced")
        self.assertNotIn("language model", cleaned.lower())
        self.assertNotIn("programmed text", cleaned.lower())
        self.assertNotIn("physically make faces", cleaned.lower())
        self.assertIn("rocky", cleaned.lower())
        self.assertIn("david", cleaned.lower())

    def test_wake_word_parsing(self):
        # Wake-only
        w = rocky_filter.parse_wake_utterance("Hey Rocky!")
        self.assertTrue(w["triggered"])
        self.assertTrue(w["wake_only"])
        self.assertEqual(w["character"], "rocky")

        # Wake + query
        q = rocky_filter.parse_wake_utterance("Hey Rocky, why is the sky blue?")
        self.assertTrue(q["triggered"])
        self.assertFalse(q["wake_only"])
        self.assertEqual(q["query"], "why is the sky blue?")

        # Sleep & reset commands
        slp = rocky_filter.parse_wake_utterance("Hey Rocky, go to sleep")
        self.assertTrue(slp["triggered"])
        self.assertTrue(slp["sleep"])

        rst = rocky_filter.parse_wake_utterance("Hey Rocky, reset conversation")
        self.assertTrue(rst["triggered"])
        self.assertTrue(rst["reset"])

        # Missing wake word ignored when required & outside follow-up window
        miss = rocky_filter.parse_wake_utterance("what is the weather today", require_wake_word=True, in_followup_window=False)
        self.assertFalse(miss["triggered"])

        # Follow-up window accepts valid singletons like "dance" and rejects random noise
        fu_ok = rocky_filter.parse_wake_utterance("dance", require_wake_word=True, in_followup_window=True)
        self.assertTrue(fu_ok["triggered"])
        self.assertEqual(fu_ok["query"], "dance")

        fu_noise = rocky_filter.parse_wake_utterance("um", require_wake_word=True, in_followup_window=True)
        self.assertFalse(fu_noise["triggered"])

        # Whisper hallucinations always ignored
        for h in ("Thank you.", "thanks for watching.", "...", "a"):
            with self.subTest(h=h):
                self.assertFalse(rocky_filter.parse_wake_utterance(h, require_wake_word=False)["triggered"])

    def test_utterance_emotion_detection(self):
        self.assertEqual(rocky_filter.detect_utterance_emotion("[love] Happy to help!"), "love")
        self.assertEqual(rocky_filter.detect_utterance_emotion("Danger danger danger!"), "worried")
        self.assertEqual(rocky_filter.detect_utterance_emotion("Happy happy happy!"), "excited")
        self.assertEqual(rocky_filter.detect_utterance_emotion("What mean, question?"), "curious")


# =============================================================================
# 6. Teach Rocky (Spoken Rules, Feedback Flags) & Cloud Brain Config
# =============================================================================
class TestTeachAndCloudBrain(unittest.TestCase):
    def setUp(self):
        self.b = brain.RockyBrain.__new__(brain.RockyBrain)
        self.b.histories = {"rocky": []}
        self.b._last_turn_was_vision = {"rocky": False}
        self.b._last_vision_query = {}
        self.b._last_vision_ts = {}

    def tearDown(self):
        brain.set_llm_runtime_config(provider="ollama", cloud_model="gemini-2.5-flash", cloud_api_key="")

    def test_spoken_teach_rule_detection(self):
        cases = [
            ("From now on, always call your camera your eyes", "Always call your camera your eyes"),
            ("Remember to read cards slowly line by line.", "Read cards slowly line by line"),
            ("Never say ESP32 out loud!", "Never say ESP32 out loud"),
            ("Always call David captain", "Always call David captain"),
            ("Can you refer to the dog as Buddy?", "Refer to the dog as Buddy"),
        ]
        for utterance, expected_sub in cases:
            with self.subTest(utterance=utterance):
                rule = brain.parse_teach_rule_intent(utterance)
                self.assertIsNotNone(rule)
                self.assertIn(expected_sub.lower(), rule.lower())
                self.assertEqual(self.b.classify_intent(utterance), "teach_rule")

        # Must NOT collide with stop tracking or normal chat
        self.assertIsNone(brain.parse_teach_rule_intent("stop tracking"))
        self.assertEqual(self.b.classify_intent("stop tracking"), "track_stop")
        self.assertIsNone(brain.parse_teach_rule_intent("why is the sky blue?"))

    def test_memory_rules_and_feedback_flags_crud(self):
        import tempfile
        import memory

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_rocky_memory.db")
            with patch.object(memory, "DB_PATH", db_path):
                memory.init_db()
                r1 = memory.add_rule("Always speak slowly when reading cards")
                self.assertGreater(r1["id"], 0)
                rules = memory.list_rules()
                self.assertEqual(len(rules), 1)
                self.assertEqual(rules[0]["rule"], "Always speak slowly when reading cards")
                block = memory.get_rules_prompt_block()
                self.assertIn("Always speak slowly when reading cards", block)
                self.assertTrue(memory.delete_rule(r1["id"]))
                self.assertEqual(memory.list_rules(), [])

                flag = memory.add_feedback_flag(
                    user_text="What day is it?",
                    rocky_reply="Good good good!",
                    note="Tell me the actual day of the week",
                )
                self.assertEqual(flag["status"], "saved")
                fetched = memory.get_feedback_flag(flag["id"])
                self.assertIsNotNone(fetched)
                self.assertEqual(fetched["note"], "Tell me the actual day of the week")

                updated = memory.update_feedback_flag(flag["id"], status="done", fix_output="Fixed and tests pass")
                self.assertEqual(updated["status"], "done")
                self.assertIn("tests pass", updated["fix_output"])
                self.assertEqual(len(memory.list_feedback_flags()), 1)
                self.assertTrue(memory.delete_feedback_flag(flag["id"]))
                self.assertEqual(memory.list_feedback_flags(), [])

    def test_cloud_brain_config_and_openai_message_conversion(self):
        st0 = brain.set_llm_runtime_config(provider="ollama", cloud_model="gemini-2.5-flash", cloud_api_key="")
        self.assertEqual(st0["active_provider"], "ollama")
        self.assertFalse(st0["cloud_api_key_set"])

        # Cloud selected WITHOUT key safely falls back to local Ollama
        st1 = brain.set_llm_runtime_config(provider="gemini", cloud_model="gemini-2.5-flash", cloud_api_key="")
        self.assertEqual(st1["provider"], "gemini")
        self.assertEqual(st1["active_provider"], "ollama")

        # Cloud selected WITH key activates cloud provider
        st2 = brain.set_llm_runtime_config(provider="gemini", cloud_model="gemini-2.5-flash", cloud_api_key="test-key-123")
        self.assertEqual(st2["active_provider"], "gemini")
        self.assertEqual(st2["active_model"], "gemini-2.5-flash")
        self.assertTrue(st2["cloud_api_key_set"])

        # Ollama image messages convert to OpenAI image_url content blocks
        oai = brain._to_openai_messages([
            {"role": "system", "content": "Be Rocky"},
            {"role": "user", "content": "What do you see?", "images": ["AAAA"]},
        ])
        self.assertEqual(oai[0], {"role": "system", "content": "Be Rocky"})
        self.assertIsInstance(oai[1]["content"], list)
        self.assertEqual(oai[1]["content"][0], {"type": "text", "text": "What do you see?"})
        self.assertEqual(oai[1]["content"][1]["type"], "image_url")
        self.assertTrue(oai[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,AAAA"))


if __name__ == "__main__":
    unittest.main()
