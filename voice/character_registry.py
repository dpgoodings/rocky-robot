"""
Rocky-Only Character Registry (`characters/rocky.json`).

Provides Rocky's canonical `CharacterSpec` (voice paths, wake words, persona prompt,
UI theme, and default pitch/speed) with `"rocky"` always as the single canonical character.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import glob
import json
import os
import re
import threading
from typing import Any

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(PROJECT_DIR, "models")
CHARACTERS_DIR = os.path.join(PROJECT_DIR, "characters")
CANONICAL_CHARACTER_ID = "rocky"


def _resolve_model_path(rel_or_abs: str | None) -> str | None:
    if not rel_or_abs:
        return None
    if rel_or_abs.startswith("macos:"):
        return rel_or_abs
    if os.path.isabs(rel_or_abs):
        return rel_or_abs
    return os.path.join(MODELS_DIR, rel_or_abs)


@dataclass
class CharacterSpec:
    id: str
    order: int
    name: str
    title: str
    emoji: str
    subtitle: str
    wake_words: list[str]
    piper_onnx: str
    rvc_onnx: str | None
    sample_rate: int
    default_transpose: int
    default_speed: float
    speech_style: str
    phrase_rewrites: list[tuple[re.Pattern[str], str]]
    tts_pronunciations: list[tuple[re.Pattern[str], str]]
    system_prompt: str
    wake_replies: list[str]
    common_openers: list[str]
    sleep_reply: str
    reset_reply: str
    fallback_reply: str
    ui: dict[str, Any]
    audio_effect: str | None = None
    start_wake_patterns: list[re.Pattern[str]] = field(default_factory=list)
    mid_wake_pattern: re.Pattern[str] | None = None

    @property
    def primary_wake_phrase(self) -> str:
        return f"Hey {self.name}"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "order": self.order,
            "name": self.name,
            "title": self.title,
            "emoji": self.emoji,
            "subtitle": self.subtitle,
            "wake_phrase": self.primary_wake_phrase,
            "wake_words": self.wake_words,
            "has_rvc": bool(self.rvc_onnx and os.path.exists(self.rvc_onnx)),
            "default_transpose": self.default_transpose,
            "default_speed": self.default_speed,
            "ui": self.ui,
        }


def _compile_wake_regexes(words: list[str]) -> tuple[list[re.Pattern[str]], re.Pattern[str] | None]:
    escaped = [re.escape(w.strip()) for w in words if w.strip()]
    if not escaped:
        return [], None
    alt = "|".join(escaped)
    starts = [
        re.compile(
            rf"^(?:hey|hi|hello|yo|okay|ok|oh|ay|say)?\s*[,.\-!]?\s*(?:{alt})\b[,.\-!?\s]*",
            re.IGNORECASE,
        ),
        re.compile(rf"^(?:{alt})\b[,.\-!?\s]*", re.IGNORECASE),
    ]
    mid = re.compile(
        rf"\b(?:hey|hi|okay|ok|yo)?\s*[,.\-]?\s*(?:{alt})\b[,.\-!?\s]*",
        re.IGNORECASE,
    )
    return starts, mid


def _parse_spec(raw: dict[str, Any]) -> CharacterSpec:
    cid = str(raw.get("id", CANONICAL_CHARACTER_ID)).strip().lower() or CANONICAL_CHARACTER_ID
    voice = raw.get("voice", {})
    persona = raw.get("persona", {})
    ui_raw = raw.get("ui", {})

    piper_path = _resolve_model_path(voice.get("piper_onnx", "piper/en_US-ryan-medium.onnx"))
    assert piper_path is not None
    rvc_path = _resolve_model_path(voice.get("rvc_onnx"))

    wake_words = list(raw.get("wake_words") or ["rocky"])
    starts, mid = _compile_wake_regexes(wake_words)

    rewrites: list[tuple[re.Pattern[str], str]] = []
    for item in persona.get("phrase_rewrites", []):
        if isinstance(item, list | tuple) and len(item) == 2:
            rewrites.append((re.compile(str(item[0]), re.IGNORECASE), str(item[1])))

    tts_pron: list[tuple[re.Pattern[str], str]] = []
    for item in voice.get("tts_pronunciations", []):
        if isinstance(item, list | tuple) and len(item) == 2:
            tts_pron.append((re.compile(str(item[0]), re.IGNORECASE), str(item[1])))

    name = str(raw.get("name", "Rocky"))
    ui = {
        **ui_raw,
        "accent": ui_raw.get("accent", "#f5a623"),
        "face_style": ui_raw.get("face_style", "rocky_v2"),
        "bmo_shell": ui_raw.get("bmo_shell", "#222731"),
        "bmo_shell_dark": ui_raw.get("bmo_shell_dark", "#13171e"),
        "bmo_screen_rgb": ui_raw.get("bmo_screen_rgb", [7, 10, 16]),
        "bmo_badge_prefix": ui_raw.get("bmo_badge_prefix", "ROCKY"),
        "idle_mood": ui_raw.get("idle_mood", "neutral"),
    }

    return CharacterSpec(
        id=cid,
        order=int(raw.get("order", 1)),
        name=name,
        title=str(raw.get("title", "Rocky (Project Hail Mary)")),
        emoji=str(raw.get("emoji", "🪨")),
        subtitle=str(raw.get("subtitle", "Curious, joyful Eridian robot on Earth")),
        wake_words=wake_words,
        piper_onnx=piper_path,
        rvc_onnx=rvc_path,
        sample_rate=int(voice.get("sample_rate", 48000)),
        default_transpose=int(voice.get("default_transpose", -5)),
        default_speed=float(voice.get("default_speed", 1.18)),
        speech_style=str(persona.get("speech_style", "eridian")).lower(),
        phrase_rewrites=rewrites,
        tts_pronunciations=tts_pron,
        system_prompt=str(
            persona.get(
                "system_prompt",
                "You are Rocky from Project Hail Mary. Answer accurately in 1 or 2 short spoken sentences under 25 words.",
            )
        ),
        wake_replies=list(
            persona.get("wake_replies")
            or ["Happy happy happy, David! Rocky awake on Earth, how can Rocky help today, question?"]
        ),
        common_openers=list(persona.get("common_openers") or []),
        sleep_reply=str(persona.get("sleep_reply", "Rocky resting robot circuits now. Call Rocky anytime, friend!")),
        reset_reply=str(persona.get("reset_reply", "Memory clean, friend! Fresh start on Earth, good good good!")),
        fallback_reply=str(persona.get("fallback_reply", "Rocky hear you friend, say again please, question?")),
        ui=ui,
        audio_effect=voice.get("audio_effect") or persona.get("audio_effect"),
        start_wake_patterns=starts,
        mid_wake_pattern=mid,
    )


class CharacterRegistry:
    """Single-persona Rocky registry (always defaults/falls back to 'rocky')."""

    def __init__(self, directory: str = CHARACTERS_DIR) -> None:
        self.directory = directory
        self._lock = threading.RLock()
        self._specs: dict[str, CharacterSpec] = {}
        self.reload()

    def reload(self) -> list[str]:
        os.makedirs(self.directory, exist_ok=True)
        loaded: dict[str, CharacterSpec] = {}

        rocky_path = os.path.join(self.directory, f"{CANONICAL_CHARACTER_ID}.json")
        if os.path.exists(rocky_path):
            with open(rocky_path, encoding="utf-8") as f:
                spec = _parse_spec(json.load(f))
            spec.id = CANONICAL_CHARACTER_ID
            loaded[CANONICAL_CHARACTER_ID] = spec
        else:
            # Fallback if a unit test points CharacterRegistry at a custom temp directory
            for path in sorted(glob.glob(os.path.join(self.directory, "*.json"))):
                fname = os.path.basename(path)
                if fname.startswith("_"):
                    continue
                with open(path, encoding="utf-8") as f:
                    spec = _parse_spec(json.load(f))
                loaded[spec.id] = spec
                break

        if not loaded:
            loaded[CANONICAL_CHARACTER_ID] = _parse_spec({"id": CANONICAL_CHARACTER_ID})

        with self._lock:
            self._specs = loaded
        return list(loaded.keys())

    @property
    def default_id(self) -> str:
        return CANONICAL_CHARACTER_ID

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._specs.keys())

    def has(self, char_id: str) -> bool:
        if not char_id:
            return False
        with self._lock:
            return char_id.strip().lower() in self._specs

    def get(self, char_id: str | None = None) -> CharacterSpec:
        with self._lock:
            if char_id and char_id.strip().lower() in self._specs:
                return self._specs[char_id.strip().lower()]
            return self._specs.get(CANONICAL_CHARACTER_ID) or next(iter(self._specs.values()))

    def all_specs(self) -> list[CharacterSpec]:
        with self._lock:
            return list(self._specs.values())

    def public_map(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {cid: spec.to_public_dict() for cid, spec in self._specs.items()}


REGISTRY = CharacterRegistry()
