"""
Generic Zero-PyTorch Multi-Character Voice Engine.

Features:
  - Reads character voice configs dynamically from `character_registry.REGISTRY`
  - Deduplicates shared Piper base TTS voices by file path (0 MB extra RAM when shared!)
  - LRU-caps loaded RVC INT8 ONNX sessions (`ROCKY_MAX_LOADED_VOICES=3` default) so adding
    dozens of voices on disk never increases runtime RAM on 8 GB Apple Silicon
  - Supports both RVC v2 INT8 ONNX voice clones AND pure Piper-only voices (`rvc_onnx: null`)
"""

from __future__ import annotations

from collections import OrderedDict
import hashlib
import io
import os
import subprocess
import tempfile
import threading
import time
from typing import Any
import wave

import numpy as np
import onnxruntime as ort
import parselmouth
import soundfile as sf
import soxr

from character_registry import REGISTRY

try:
    import static_ffmpeg

    static_ffmpeg.add_paths()
except Exception:
    pass

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(PROJECT_DIR, "models")
CACHE_DIR = os.path.join(PROJECT_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

CONTENTVEC_ONNX = os.path.join(MODELS_DIR, "contentvec.int8.onnx")

P_CORES = 4
NOISE_SCALE = 0.66667
MAX_LOADED_RVC = int(os.environ.get("ROCKY_MAX_LOADED_VOICES", "3"))
MAX_LOADED_PIPER = int(os.environ.get("ROCKY_MAX_LOADED_PIPER", "3"))


def _make_ort_session(onnx_path: str) -> ort.InferenceSession:
    so = ort.SessionOptions()
    so.intra_op_num_threads = P_CORES
    so.inter_op_num_threads = 1
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.enable_cpu_mem_arena = True
    so.enable_mem_pattern = True
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(onnx_path, so, providers=["CPUExecutionProvider"])


class RockyVoiceEngine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._whisper = None
        self._cv_sess: ort.InferenceSession | None = None
        self._pipers_by_path: OrderedDict[str, Any] = OrderedDict()
        self._rvc_by_path: OrderedDict[str, ort.InferenceSession] = OrderedDict()
        self._mem_cache: OrderedDict[str, bytes] = OrderedDict()
        self._mem_cache_max = 256
        self._cache_write_counter = 0

    def load_all(self) -> None:
        t0 = time.perf_counter()
        print("[VoiceEngine] Loading Faster-Whisper (tiny.en, int8, 4 P-cores)...", flush=True)
        self._get_whisper()

        print("[VoiceEngine] Loading ContentVec INT8 ONNX...", flush=True)
        cv = self._get_cv()
        _ = cv.run(None, {"source": np.zeros((1, 4000), dtype=np.float32)})

        # Pre-load up to MAX_LOADED_RVC primary characters so startup stays bounded
        for spec in REGISTRY.all_specs()[:MAX_LOADED_RVC]:
            print(f"[VoiceEngine] Warming '{spec.id}' voice session...", flush=True)
            if not spec.piper_onnx.startswith("macos:"):
                self._get_piper_by_path(spec.piper_onnx)
            if spec.rvc_onnx and os.path.exists(spec.rvc_onnx):
                self._get_rvc_by_path(spec.rvc_onnx)

        print(
            f"[VoiceEngine] Generic Zero-PyTorch audio stack ready in {time.perf_counter() - t0:.2f}s!",
            flush=True,
        )

    def get_defaults(self, character: str) -> tuple[int, float]:
        spec = REGISTRY.get(character)
        return int(spec.default_transpose), float(spec.default_speed)

    def _get_whisper(self):
        if self._whisper is None:
            from faster_whisper import WhisperModel

            self._whisper = WhisperModel(
                "tiny.en",
                device="cpu",
                compute_type="int8",
                cpu_threads=P_CORES,
            )
        return self._whisper

    def _get_cv(self) -> ort.InferenceSession:
        if self._cv_sess is None:
            self._cv_sess = _make_ort_session(CONTENTVEC_ONNX)
        return self._cv_sess

    def _get_piper_by_path(self, onnx_path: str):
        key = os.path.abspath(onnx_path)
        if key in self._pipers_by_path:
            self._pipers_by_path.move_to_end(key)
            return self._pipers_by_path[key]

        from piper import PiperVoice

        voice = PiperVoice.load(key)
        self._pipers_by_path[key] = voice
        while len(self._pipers_by_path) > MAX_LOADED_PIPER:
            self._pipers_by_path.popitem(last=False)
        return voice

    def _get_rvc_by_path(self, onnx_path: str) -> ort.InferenceSession:
        key = os.path.abspath(onnx_path)
        if key in self._rvc_by_path:
            self._rvc_by_path.move_to_end(key)
            return self._rvc_by_path[key]

        sess = _make_ort_session(key)
        self._rvc_by_path[key] = sess
        while len(self._rvc_by_path) > MAX_LOADED_RVC:
            self._rvc_by_path.popitem(last=False)
        return sess

    # ------------------------------------------------------------------
    # 1. Fast Speech-to-Text (`tiny.en` INT8)
    # ------------------------------------------------------------------
    def transcribe_audio_bytes(self, audio_bytes: bytes) -> dict[str, Any]:
        t0 = time.perf_counter()
        audio16k = self._decode_to_16k(audio_bytes)
        duration_s = len(audio16k) / 16000.0
        if duration_s < 0.22:
            return {"text": "", "duration_s": duration_s, "rms": 0.0, "latency_ms": 0}

        rms = float(np.sqrt(np.mean(np.square(audio16k))))
        if rms < 0.003:
            return {"text": "", "duration_s": duration_s, "rms": rms, "latency_ms": 0}

        whisper = self._get_whisper()
        segments, _ = whisper.transcribe(
            audio16k,
            language="en",
            beam_size=1,
            best_of=1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 250},
            condition_on_previous_text=False,
        )

        texts = [seg.text.strip() for seg in segments if seg.no_speech_prob < 0.75]
        transcript = " ".join(texts).strip()
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "text": transcript,
            "duration_s": round(duration_s, 2),
            "rms": round(rms, 4),
            "latency_ms": latency_ms,
        }

    def _decode_to_16k(self, audio_bytes: bytes) -> np.ndarray:
        try:
            y, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=False)
            if y.ndim > 1:
                y = y.mean(axis=1)
            if sr != 16000:
                y = soxr.resample(y, sr, 16000, quality="LQ")
            return np.ascontiguousarray(y, dtype=np.float32)
        except Exception:
            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f_in:
                f_in.write(audio_bytes)
                in_path = f_in.name
            out_path = in_path + ".16k.wav"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", in_path, "-ac", "1", "-ar", "16000", "-f", "wav", out_path],
                    capture_output=True,
                    check=True,
                )
                y, _ = sf.read(out_path, dtype="float32", always_2d=False)
                return np.ascontiguousarray(y, dtype=np.float32)
            finally:
                for p in (in_path, out_path):
                    if os.path.exists(p):
                        os.unlink(p)

    # ------------------------------------------------------------------
    # 2. Base Neural TTS (Piper ONNX, shared by path)
    # ------------------------------------------------------------------
    def _synthesize_base_piper(
        self,
        text: str,
        character: str = "rocky",
        speed: float | None = None,
    ) -> tuple[np.ndarray, int]:
        spec = REGISTRY.get(character)
        if speed is None:
            speed = spec.default_speed

        spoken_text = text
        for pat, rep in spec.tts_pronunciations:
            spoken_text = pat.sub(rep, spoken_text)

        if spec.piper_onnx.startswith("macos:"):
            mac_voice = spec.piper_onnx.split(":", 1)[1].strip()
            wpm = max(110, min(320, int(round(188.0 * speed))))
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_f:
                tmp_wav = tmp_f.name
            try:
                cmd = ["/usr/bin/say"]
                if mac_voice and mac_voice.lower() not in ("system", "default"):
                    cmd.extend(["-v", mac_voice])
                cmd.extend(["-r", str(wpm), "--data-format=LEI16@22050", "-o", tmp_wav, spoken_text])
                subprocess.run(cmd, capture_output=True, check=True)
                y, sr = sf.read(tmp_wav, dtype="float32", always_2d=False)
            finally:
                if os.path.exists(tmp_wav):
                    os.unlink(tmp_wav)
        else:
            voice = self._get_piper_by_path(spec.piper_onnx)
            length_scale = max(0.55, min(1.65, 1.0 / max(0.5, speed)))
            try:
                from piper.config import SynthesisConfig

                syn_cfg = SynthesisConfig(length_scale=length_scale)
                chunks = [c.audio_float_array for c in voice.synthesize(spoken_text, syn_config=syn_cfg)]
                y = np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)
                sr = int(voice.config.sample_rate)
            except Exception:
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wav_file:
                    try:
                        from piper.config import SynthesisConfig

                        syn_cfg = SynthesisConfig(length_scale=length_scale)
                        voice.synthesize_wav(spoken_text, wav_file, syn_config=syn_cfg)
                    except (ImportError, AttributeError, TypeError):
                        voice.synthesize(spoken_text, wav_file, length_scale=length_scale)
                buf.seek(0)
                y, sr = sf.read(buf, dtype="float32", always_2d=False)
                sr = int(sr)
        if y.ndim > 1:
            y = y.mean(axis=1)
        active = np.flatnonzero(np.abs(y) > 0.004)
        if len(active) > 0:
            head = max(0, int(active[0]) - int(0.025 * sr))
            tail = min(len(y), int(active[-1]) + int(0.075 * sr))
            y = y[head:tail]
        return np.ascontiguousarray(y, dtype=np.float32), int(sr)

    # ------------------------------------------------------------------
    # 3. Fast C++ ONNX + Praat Parselmouth RVC v2 (or Praat PSOLA fallback)
    # ------------------------------------------------------------------
    def convert_speech_to_character(
        self,
        audio: np.ndarray,
        orig_sr: int,
        character: str = "rocky",
        transpose: int | None = None,
    ) -> tuple[np.ndarray, int]:
        spec = REGISTRY.get(character)
        if transpose is None:
            transpose = spec.default_transpose

        # Fast Piper-only mode when `rvc_onnx` is null / omitted!
        if not spec.rvc_onnx or not os.path.exists(spec.rvc_onnx):
            out = audio
            if transpose != 0 and len(out) > 256:
                out = self._pitch_shift_parselmouth(out, orig_sr, transpose)
            if getattr(spec, "audio_effect", None) == "tactical_radio":
                out = self._apply_tactical_chassis_fx(out, orig_sr)
            return self._polish_audio(out, orig_sr), orig_sr

        out_sr = int(spec.sample_rate)
        audio16k = (
            soxr.resample(audio, orig_sr, 16000, quality="LQ")
            if orig_sr != 16000
            else audio
        )
        audio16k = np.ascontiguousarray(audio16k, dtype=np.float32)

        peak_in = float(np.max(np.abs(audio16k)))
        if peak_in > 1e-4:
            audio16k = audio16k / peak_in * 0.9

        with self._lock:
            h = self._get_cv().run(None, {"source": audio16k[None]})[0]
            h = np.repeat(h, 2, axis=1)
            p_len = h.shape[1]

            coarse, pitchf = self._extract_f0_parselmouth(audio16k, p_len, transpose)
            p_len = min(p_len, len(coarse))
            h = h[:, :p_len].astype(np.float32)

            rnd = np.random.standard_normal((1, 192, p_len)).astype(np.float32) * NOISE_SCALE
            feed = {
                "phone": h,
                "phone_lengths": np.array([p_len], dtype=np.int64),
                "pitch": coarse[:p_len][None].astype(np.int64),
                "nsff0": pitchf[:p_len][None].astype(np.float32),
                "ds": np.array([0], dtype=np.int64),
                "rnd": rnd,
            }
            rvc_sess = self._get_rvc_by_path(spec.rvc_onnx)
            out = rvc_sess.run(None, feed)[0].flatten()

        return self._polish_audio(out, out_sr), out_sr

    @staticmethod
    def _pitch_shift_parselmouth(audio: np.ndarray, sr: int, semitones: int) -> np.ndarray:
        """Clean Praat PSOLA pitch shift (~8ms) for Piper-only characters without RVC."""
        try:
            snd = parselmouth.Sound(audio.astype(np.float64), sr)
            manip = parselmouth.praat.call(snd, "To Manipulation", 0.01, 60.0, 600.0)
            pitch_tier = parselmouth.praat.call(manip, "Extract pitch tier")
            ratio = 2.0 ** (semitones / 12.0)
            parselmouth.praat.call(pitch_tier, "Multiply frequencies", snd.xmin, snd.xmax, ratio)
            parselmouth.praat.call([pitch_tier, manip], "Replace pitch tier")
            res = parselmouth.praat.call(manip, "Get resynthesis (overlap-add)")
            return np.ascontiguousarray(res.values[0], dtype=np.float32)
        except Exception:
            return audio

    @staticmethod
    def _extract_f0_parselmouth(
        audio16k: np.ndarray, p_len: int, f0_up: int
    ) -> tuple[np.ndarray, np.ndarray]:
        time_step = 160.0 / 16000.0
        f0_min, f0_max = 50.0, 1100.0
        pad_size = int((16000 // 160) / f0_min + 1) // 2

        f0 = (
            parselmouth.Sound(audio16k.astype(np.float64), 16000)
            .to_pitch_ac(
                time_step=time_step,
                voicing_threshold=0.6,
                pitch_floor=f0_min,
                pitch_ceiling=f0_max,
            )
            .selected_array["frequency"]
        )

        lpad = pad_size * 2 - len(f0) + p_len
        if lpad > 0:
            f0 = np.pad(f0, (lpad // 2, lpad - lpad // 2), mode="constant")
        elif len(f0) < p_len:
            f0 = np.pad(f0, (0, p_len - len(f0)), mode="edge")

        f0 = np.nan_to_num(f0[:p_len]) * (2.0 ** (f0_up / 12.0))

        mel_min = 1127.0 * np.log(1.0 + f0_min / 700.0)
        mel_max = 1127.0 * np.log(1.0 + f0_max / 700.0)
        f0_mel = np.where(
            f0 > 0,
            (1127.0 * np.log(1.0 + f0 / 700.0) - mel_min) * 254.0 / (mel_max - mel_min) + 1.0,
            0.0,
        )
        coarse = np.rint(np.clip(f0_mel, 1, 255)).astype(np.int64)
        return coarse, f0.astype(np.float32)

    @staticmethod
    def _polish_audio(audio: np.ndarray, sr: int) -> np.ndarray:
        y = audio.astype(np.float32)
        if len(y) == 0:
            return y
        y = y - float(np.mean(y))
        fade_len = min(int(0.012 * sr), len(y) // 4)
        if fade_len > 1:
            ramp = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
            y[:fade_len] *= ramp
            y[-fade_len:] *= ramp[::-1]
        peak = float(np.max(np.abs(y)))
        if peak > 1e-4:
            y = y / peak * 0.92
        return y

    @staticmethod
    def _apply_tactical_chassis_fx(audio: np.ndarray, sr: int) -> np.ndarray:
        """
        Subtle metallic chassis resonator + crisp communications presence filter
        matching TARS / GPTARS internal robot speaker acoustics.
        """
        if len(audio) < 512:
            return audio
        delay = int(sr * 0.0028)
        delay2 = int(sr * 0.0055)
        resonated = 0.82 * audio.copy()
        if len(audio) > delay2:
            resonated[delay:] += 0.20 * audio[:-delay]
            resonated[delay2:] -= 0.10 * audio[:-delay2]
        crisp = np.empty_like(resonated)
        crisp[0] = resonated[0]
        crisp[1:] = resonated[1:] - 0.20 * resonated[:-1]
        blended = 0.70 * crisp + 0.30 * audio
        peak = float(np.max(np.abs(blended)))
        if peak > 1e-4:
            blended = blended / peak * 0.92
        return blended

    # ------------------------------------------------------------------
    # 4. End-to-End Text -> Character WAV Bytes (LRU + Disk Cache)
    # ------------------------------------------------------------------
    def synthesize_character_wav(
        self,
        text: str,
        character: str = "rocky",
        transpose: int | None = None,
        speed: float | None = None,
    ) -> tuple[bytes, int]:
        t0 = time.perf_counter()
        spec = REGISTRY.get(character)
        char_id = spec.id
        tr = int(spec.default_transpose) if transpose is None else int(transpose)
        sp = float(spec.default_speed) if speed is None else float(speed)

        # Keep exact v3 hash format for existing cached Rocky & Marvin RVC clips!
        rvc_tag = "" if (spec.rvc_onnx and spec.id in ("rocky", "marvin")) else f"|{os.path.basename(spec.rvc_onnx or spec.piper_onnx)}"
        key = hashlib.sha1(
            f"v3|{char_id}{rvc_tag}|{tr}|{sp:.2f}|{text.strip()}".encode()
        ).hexdigest()

        if key in self._mem_cache:
            self._mem_cache.move_to_end(key)
            return self._mem_cache[key], int((time.perf_counter() - t0) * 1000)

        disk_path = os.path.join(CACHE_DIR, f"{key}.wav")
        if os.path.exists(disk_path):
            with open(disk_path, "rb") as f:
                wav_bytes = f.read()
            self._store_mem(key, wav_bytes)
            return wav_bytes, int((time.perf_counter() - t0) * 1000)

        base_audio, base_sr = self._synthesize_base_piper(text, character=char_id, speed=sp)
        char_audio, char_sr = self.convert_speech_to_character(
            base_audio, base_sr, character=char_id, transpose=tr
        )

        buf = io.BytesIO()
        sf.write(buf, char_audio, char_sr, subtype="PCM_16", format="WAV")
        wav_bytes = buf.getvalue()

        with open(disk_path, "wb") as f:
            f.write(wav_bytes)
        self._store_mem(key, wav_bytes)

        self._cache_write_counter += 1
        if self._cache_write_counter % 25 == 0:
            threading.Thread(target=self._maybe_prune_disk_cache, daemon=True).start()

        return wav_bytes, int((time.perf_counter() - t0) * 1000)

    def synthesize_rocky_wav(
        self,
        text: str,
        transpose: int | None = None,
        speed: float | None = None,
    ) -> tuple[bytes, int]:
        return self.synthesize_character_wav(
            text, character="rocky", transpose=transpose, speed=speed
        )

    def convert_uploaded_audio_to_character(
        self,
        audio_bytes: bytes,
        character: str = "rocky",
        transpose: int | None = None,
    ) -> tuple[bytes, int]:
        t0 = time.perf_counter()
        audio16k = self._decode_to_16k(audio_bytes)
        char_audio, char_sr = self.convert_speech_to_character(
            audio16k, 16000, character=character, transpose=transpose
        )
        buf = io.BytesIO()
        sf.write(buf, char_audio, char_sr, subtype="PCM_16", format="WAV")
        return buf.getvalue(), int((time.perf_counter() - t0) * 1000)

    def _store_mem(self, key: str, wav_bytes: bytes) -> None:
        self._mem_cache[key] = wav_bytes
        if len(self._mem_cache) > self._mem_cache_max:
            self._mem_cache.popitem(last=False)

    def _maybe_prune_disk_cache(self, max_files: int = 400, max_mb: float = 80.0) -> None:
        """Background LRU disk cache pruner to keep rocky-voice lean on disk."""
        try:
            files = [
                os.path.join(CACHE_DIR, f)
                for f in os.listdir(CACHE_DIR)
                if f.endswith(".wav")
            ]
            if len(files) <= max_files:
                return

            file_stats = []
            total_bytes = 0
            for f in files:
                try:
                    st = os.stat(f)
                    total_bytes += st.st_size
                    file_stats.append((st.st_mtime, st.st_size, f))
                except OSError:
                    pass

            target_bytes = int(max_mb * 1024 * 1024 * 0.7)
            target_files = int(max_files * 0.7)

            if total_bytes > (max_mb * 1024 * 1024) or len(files) > max_files:
                file_stats.sort(key=lambda x: x[0])  # oldest first
                for _, sz, path in file_stats:
                    if total_bytes <= target_bytes and len(files) <= target_files:
                        break
                    try:
                        os.unlink(path)
                        total_bytes -= sz
                        files.pop()
                    except OSError:
                        pass
        except Exception:
            pass
