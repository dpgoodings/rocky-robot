"""Local adaptive end-of-turn detector for Rocky's voice pipeline.

Determines whether a paused utterance sounds finished or merely mid-thought so
Rocky waits slightly longer during thinking pauses ("um...", "I was wondering...")
and responds crisply when the user finishes a sentence or question.

Runtime strategy (zero mandatory downloads):
1. If a cached Smart Turn v3.2 ONNX model (`smart-turn-v3.2-cpu.onnx`) is present
   in `~/.cache/desk-robot/` or `voice/models/turn/` and `onnxruntime` is available,
   runs local Whisper log-mel + ONNX inference (`method="smart_turn_onnx"`).
2. Else if a cached Silero VAD ONNX model (`silero_vad.onnx`) is present locally,
   combines Silero trailing speech probability with local prosody (`method="silero_vad_onnx"`).
3. Otherwise runs a fast pure-NumPy acoustic prosody + RMS/ZCR/pitch-tail analyzer
   (`method="acoustic_prosody"`, <2 ms per call).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

SAMPLE_RATE: int = 16_000
SMART_TURN_WINDOW: int = 8 * SAMPLE_RATE  # 128,000 samples (8s)
SILERO_CHUNK: int = 512                   # 32 ms @ 16 kHz
SILERO_CONTEXT: int = 64

DEFAULT_MIN_SILENCE_MS: int = 280
DEFAULT_MID_THOUGHT_WAIT_MS: int = 850
DEFAULT_MAX_SILENCE_MS: int = 2000
DEFAULT_COMPLETE_THRESHOLD: float = 0.55

# Whisper-style 80-mel constants (matches desk-robot-2.1/server/brain/whisper_features.py)
_N_FFT = 400
_HOP_LENGTH = 160
_N_MELS = 80
_MEL_FLOOR = 1e-10
_NORM_EPS = 1e-7


def _candidate_model_dirs() -> list[Path]:
    dirs: list[Path] = []
    env_dir = os.environ.get("DESK_ROBOT_CACHE")
    if env_dir:
        dirs.append(Path(env_dir))
    dirs.append(Path.home() / ".cache" / "desk-robot")
    dirs.append(Path(__file__).resolve().parent / "models" / "turn")
    return dirs


def _find_cached_model(filename: str) -> Path | None:
    for d in _candidate_model_dirs():
        p = d / filename
        if p.is_file() and p.stat().st_size > 10_000:
            return p
    return None


def _build_mel_filterbank() -> np.ndarray:
    min_log_hz = 1000.0
    min_log_mel = 15.0
    logstep = 27.0 / np.log(6.4)

    def hz_to_mel(f: np.ndarray) -> np.ndarray:
        m = 3.0 * f / 200.0
        mask = f >= min_log_hz
        m[mask] = min_log_mel + np.log(f[mask] / min_log_hz) * logstep
        return m

    def mel_to_hz(m: np.ndarray) -> np.ndarray:
        f = 200.0 * m / 3.0
        mask = m >= min_log_mel
        f[mask] = min_log_hz * np.exp((m[mask] - min_log_mel) / logstep)
        return f

    mel_pts = np.linspace(
        float(hz_to_mel(np.array([0.0]))[0]),
        float(hz_to_mel(np.array([SAMPLE_RATE / 2.0]))[0]),
        _N_MELS + 2,
    )
    filter_freqs = mel_to_hz(mel_pts)
    fft_freqs = np.linspace(0.0, SAMPLE_RATE / 2.0, _N_FFT // 2 + 1)
    fdiff = np.diff(filter_freqs)
    slopes = filter_freqs[None, :] - fft_freqs[:, None]
    down = -slopes[:, :-2] / fdiff[:-1]
    up = slopes[:, 2:] / fdiff[1:]
    fb = np.maximum(0.0, np.minimum(down, up))
    enorm = 2.0 / (filter_freqs[2 : _N_MELS + 2] - filter_freqs[:_N_MELS])
    fb *= enorm[None, :]
    return fb


_HANN_WIN: np.ndarray | None = None
_MEL_FB: np.ndarray | None = None


def _compute_whisper_log_mel(audio: np.ndarray) -> np.ndarray:
    global _HANN_WIN, _MEL_FB
    if _HANN_WIN is None:
        _HANN_WIN = np.hanning(_N_FFT + 1)[:-1].astype(np.float64)
        _MEL_FB = _build_mel_filterbank()

    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    if x.size > SMART_TURN_WINDOW:
        x = x[-SMART_TURN_WINDOW:]
    elif x.size < SMART_TURN_WINDOW:
        x = np.pad(x, (SMART_TURN_WINDOW - x.size, 0), mode="constant")

    x = (x - x.mean()) / np.sqrt(x.var() + _NORM_EPS)
    pad = _N_FFT // 2
    padded = np.pad(x.astype(np.float64), (pad, pad), mode="reflect")
    windows = sliding_window_view(padded, _N_FFT)[::_HOP_LENGTH]
    spec = np.fft.rfft(windows * _HANN_WIN, axis=-1)
    power = (np.abs(spec) ** 2).T
    mel = np.maximum(_MEL_FLOOR, _MEL_FB.T @ power)
    log_spec = np.log10(mel)[:, :-1]
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    return ((log_spec + 4.0) / 4.0).astype(np.float32)


def _estimate_frame_f0_hz(frame: np.ndarray, sr: int = SAMPLE_RATE) -> float | None:
    """Fast autocorrelation F0 estimator for a voiced speech frame (75..380 Hz)."""
    n = frame.size
    if n < 256:
        return None
    f = frame.astype(np.float64) - float(np.mean(frame))
    energy = float(np.dot(f, f))
    if energy < 1e-6:
        return None

    # Normalized autocorrelation via FFT (O(N log N))
    fft_len = 1 << (2 * n - 1).bit_length()
    spec = np.fft.rfft(f, n=fft_len)
    acf = np.fft.irfft(spec * np.conj(spec), n=fft_len)[:n]
    acf0 = acf[0]
    if acf0 <= 1e-12:
        return None

    min_lag = max(1, int(sr / 380.0))  # ~42 samples
    max_lag = min(n - 1, int(sr / 75.0))  # ~213 samples
    if max_lag <= min_lag + 2:
        return None

    seg = acf[min_lag:max_lag] / acf0
    peak_idx = int(np.argmax(seg))
    peak_val = float(seg[peak_idx])
    if peak_val < 0.35:
        return None  # Unvoiced / noisy frame
    lag = min_lag + peak_idx
    return float(sr) / float(lag)


class AdaptiveTurnDetector:
    """Adaptive local end-of-turn detector using cached ONNX models or fast acoustic prosody."""

    def __init__(
        self,
        min_silence_ms: int = DEFAULT_MIN_SILENCE_MS,
        mid_thought_wait_ms: int = DEFAULT_MID_THOUGHT_WAIT_MS,
        max_silence_ms: int = DEFAULT_MAX_SILENCE_MS,
        complete_threshold: float = DEFAULT_COMPLETE_THRESHOLD,
        use_onnx: bool = True,
    ) -> None:
        self._lock = threading.Lock()
        self.sample_rate: int = SAMPLE_RATE
        self.min_silence_ms: int = int(min_silence_ms)
        self.mid_thought_wait_ms: int = int(mid_thought_wait_ms)
        self.max_silence_ms: int = int(max_silence_ms)
        self.complete_threshold: float = float(complete_threshold)

        self._smart_turn_session: Any = None
        self._silero_session: Any = None
        self._smart_turn_path: Path | None = None
        self._silero_path: Path | None = None
        self.method: str = "acoustic_prosody"
        self.last_result: dict[str, Any] | None = None

        if use_onnx:
            self._try_load_cached_onnx()

    def _try_load_cached_onnx(self) -> None:
        st_path = _find_cached_model("smart-turn-v3.2-cpu.onnx")
        vad_path = _find_cached_model("silero_vad.onnx")
        if st_path is None and vad_path is None:
            return
        try:
            import onnxruntime as ort
        except Exception:
            return

        def _make_sess(path: Path, threads: int = 1) -> Any:
            so = ort.SessionOptions()
            so.inter_op_num_threads = 1
            so.intra_op_num_threads = threads
            so.log_severity_level = 3
            return ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])

        if st_path is not None:
            try:
                self._smart_turn_session = _make_sess(st_path, threads=2)
                self._smart_turn_path = st_path
                self.method = "smart_turn_onnx"
            except Exception:
                self._smart_turn_session = None

        if vad_path is not None:
            try:
                self._silero_session = _make_sess(vad_path, threads=1)
                self._silero_path = vad_path
                if self._smart_turn_session is None:
                    self.method = "silero_vad_onnx"
            except Exception:
                self._silero_session = None

    def _run_silero_tail_speech_prob(self, audio: np.ndarray) -> float:
        """Estimate speech probability on the final ~256 ms using cached Silero VAD ONNX."""
        if self._silero_session is None or audio.size < SILERO_CHUNK:
            return 0.0
        tail = audio[-min(audio.size, SILERO_CHUNK * 12) :]
        state = np.zeros((2, 1, 128), dtype=np.float32)
        ctx = np.zeros((1, SILERO_CONTEXT), dtype=np.float32)
        sr_arr = np.array(SAMPLE_RATE, dtype=np.int64)
        probs: list[float] = []
        for off in range(0, tail.size - SILERO_CHUNK + 1, SILERO_CHUNK):
            chunk = tail[off : off + SILERO_CHUNK].reshape(1, -1).astype(np.float32)
            x = np.concatenate([ctx, chunk], axis=1)
            out, state = self._silero_session.run(None, {"input": x, "state": state, "sr": sr_arr})
            ctx = x[:, -SILERO_CONTEXT:]
            probs.append(float(out[0, 0]))
        return float(np.mean(probs[-3:])) if probs else 0.0

    def _prosody_score(self, audio: np.ndarray, silence_ms: float) -> tuple[float, dict[str, float]]:
        """Fast pure-NumPy prosody + RMS/ZCR/pitch-tail completion score in [0.0, 1.0]."""
        frame_len = 320  # 20 ms @ 16 kHz
        if audio.size < frame_len:
            return 0.25, {"speech_ms": 0.0}

        # Frame RMS & ZCR
        n_frames = audio.size // frame_len
        frames = audio[: n_frames * frame_len].reshape(n_frames, frame_len)
        rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
        peak_rms = float(np.max(rms))
        if peak_rms < 0.004:
            # Effectively silence / background hiss
            return 0.2, {"speech_ms": 0.0, "peak_rms": peak_rms}

        speech_thresh = max(0.006, peak_rms * 0.14)
        active_idx = np.flatnonzero(rms >= speech_thresh)
        if active_idx.size == 0:
            return 0.2, {"speech_ms": 0.0, "peak_rms": peak_rms}

        first_f = int(active_idx[0])
        last_f = int(active_idx[-1]) + 1
        speech_frames = frames[first_f:last_f]
        speech_rms = rms[first_f:last_f]
        speech_ms = float(speech_frames.shape[0] * 20.0)

        # Zero-crossing rate on the trailing ~200 ms (last 10 speech frames)
        tail_n = min(10, speech_frames.shape[0])
        tail_frames = speech_frames[-tail_n:]
        signs = np.signbit(tail_frames)
        tail_zcr = float(np.mean(signs[:, 1:] != signs[:, :-1]))

        # Energy taper: does energy decay toward the end, or stay held flat like "uhhh..."?
        tail_rms = float(np.mean(speech_rms[-min(6, speech_rms.size) :]))
        body_rms = float(np.mean(speech_rms)) + 1e-9
        tail_rms_ratio = tail_rms / body_rms
        tail_rms_cv = float(np.std(speech_rms[-tail_n:]) / (np.mean(speech_rms[-tail_n:]) + 1e-9))

        # Pitch-tail slope over the last ~360 ms of active speech (30 ms / 480-sample windows)
        active_audio = audio[first_f * frame_len : last_f * frame_len]
        pitch_win = 480
        pitch_hop = 240
        tail_audio = active_audio[-min(active_audio.size, int(0.45 * SAMPLE_RATE)) :]
        f0_pts: list[tuple[float, float]] = []
        if tail_audio.size >= pitch_win:
            for idx in range(0, tail_audio.size - pitch_win + 1, pitch_hop):
                f0 = _estimate_frame_f0_hz(tail_audio[idx : idx + pitch_win])
                if f0 is not None:
                    t_sec = idx / float(SAMPLE_RATE)
                    f0_pts.append((t_sec, f0))

        pitch_slope = 0.0
        if len(f0_pts) >= 3:
            ts = np.array([p[0] for p in f0_pts], dtype=np.float64)
            fs = np.array([p[1] for p in f0_pts], dtype=np.float64)
            # Linear slope (Hz/s)
            ts_c = ts - np.mean(ts)
            denom = float(np.dot(ts_c, ts_c))
            if denom > 1e-6:
                pitch_slope = float(np.dot(ts_c, fs - np.mean(fs)) / denom)

        # Combine cues into completion confidence
        score = 0.50

        # 1. Utterance duration cue (very short fragments like "um", "wait", "so" are rarely done)
        if speech_ms < 420.0:
            score -= 0.22
        elif speech_ms < 650.0:
            score -= 0.08
        elif speech_ms >= 900.0:
            score += 0.12

        # 2. Energy taper / held-vowel filler cue
        if tail_rms_ratio < 0.72:
            score += 0.16  # Clean trailing decay -> finished phrase
        elif tail_rms_ratio > 0.92 and tail_rms_cv < 0.22 and tail_zcr < 0.09:
            score -= 0.22  # Sustained monotone vowel ("uhhhh", "andddd") -> mid-thought

        # 3. Pitch contour cue
        if pitch_slope <= -25.0:
            score += 0.18  # Clear falling declarative cadence -> complete
        elif 10.0 <= pitch_slope <= 90.0:
            score -= 0.15  # Slight continuation rise ("I wanted to...") -> likely continuing
        elif pitch_slope > 90.0 and speech_ms >= 650.0:
            score += 0.06  # Crisp final rising question intonation ("are you ready?")

        # 4. Elapsed silence credit
        if silence_ms < self.min_silence_ms:
            score -= 0.25
        else:
            sil_frac = min(1.0, (silence_ms - self.min_silence_ms) / max(1.0, self.max_silence_ms - self.min_silence_ms))
            score += 0.30 * sil_frac

        score = float(max(0.0, min(1.0, score)))
        features = {
            "speech_ms": round(speech_ms, 1),
            "tail_rms_ratio": round(tail_rms_ratio, 3),
            "tail_zcr": round(tail_zcr, 3),
            "pitch_slope_hz_s": round(pitch_slope, 1),
        }
        return score, features

    def analyze_utterance(
        self,
        audio_f32_16k: np.ndarray,
        silence_ms: float,
    ) -> dict[str, Any]:
        """Analyze a 16 kHz float32 utterance and trailing silence duration.

        Returns:
            {
                "complete": bool,
                "confidence": float,
                "recommended_wait_ms": int,
                "method": str,
            }
        """
        audio = np.asarray(audio_f32_16k, dtype=np.float32).reshape(-1)
        sil = max(0.0, float(silence_ms))

        # Hard ceiling: never stall beyond max_silence_ms
        if sil >= self.max_silence_ms:
            res = {
                "complete": True,
                "confidence": 0.98,
                "recommended_wait_ms": 0,
                "method": self.method,
            }
            with self._lock:
                self.last_result = res
            return res

        # Too short a silence to interrupt natural word boundaries
        if sil < self.min_silence_ms:
            conf, _ = self._prosody_score(audio, sil)
            wait_ms = int(max(120, self.min_silence_ms - sil + (250 if conf < 0.45 else 80)))
            res = {
                "complete": False,
                "confidence": round(conf, 3),
                "recommended_wait_ms": wait_ms,
                "method": self.method,
            }
            with self._lock:
                self.last_result = res
            return res

        # 1. Cached Smart Turn v3.2 ONNX inference
        if self._smart_turn_session is not None:
            try:
                feats = _compute_whisper_log_mel(audio)
                out = self._smart_turn_session.run(None, {"input_features": feats[np.newaxis]})
                prob = float(out[0][0, 0])
                complete = prob >= self.complete_threshold
                wait_ms = 0 if complete else int(
                    min(
                        max(150.0, self.max_silence_ms - sil),
                        self.mid_thought_wait_ms * (1.0 - prob * 0.6),
                    )
                )
                res = {
                    "complete": bool(complete),
                    "confidence": round(max(0.0, min(1.0, prob)), 3),
                    "recommended_wait_ms": wait_ms,
                    "method": "smart_turn_onnx",
                }
                with self._lock:
                    self.last_result = res
                return res
            except Exception:
                pass

        # 2. Local acoustic prosody (+ optional Silero VAD ONNX tail refinement)
        conf, _features = self._prosody_score(audio, sil)
        method_used = "acoustic_prosody"
        if self._silero_session is not None:
            try:
                tail_speech_prob = self._run_silero_tail_speech_prob(audio)
                # If Silero still hears trailing voice activity, dampen completion confidence
                conf = float(max(0.0, min(1.0, conf * (1.0 - 0.35 * tail_speech_prob))))
                method_used = "silero_vad_onnx"
            except Exception:
                pass

        complete = conf >= self.complete_threshold
        if complete:
            wait_ms = 0
        else:
            remaining = max(120.0, self.max_silence_ms - sil)
            desired = self.mid_thought_wait_ms * max(0.35, 1.0 - conf)
            wait_ms = int(min(remaining, max(150.0, desired)))

        res = {
            "complete": bool(complete),
            "confidence": round(conf, 3),
            "recommended_wait_ms": wait_ms,
            "method": method_used,
        }
        with self._lock:
            self.last_result = res
        return res

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "method": self.method,
                "sample_rate": self.sample_rate,
                "smart_turn_onnx_loaded": self._smart_turn_session is not None,
                "silero_vad_onnx_loaded": self._silero_session is not None,
                "thresholds": {
                    "min_silence_ms": self.min_silence_ms,
                    "mid_thought_wait_ms": self.mid_thought_wait_ms,
                    "max_silence_ms": self.max_silence_ms,
                    "complete_threshold": self.complete_threshold,
                },
                "last_result": self.last_result,
            }


TURN_DETECTOR = AdaptiveTurnDetector()
