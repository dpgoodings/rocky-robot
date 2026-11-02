"""
Dynamic Character Pack Registry (`characters/*.json`).

Allows new voices & personas to be added simply by dropping a JSON manifest into
`characters/` (or running `add_voice.py`) with zero edits to engine, brain, filter,
server, or frontend code!
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


class CharacterRegistry:
    def __init__(self, directory: str = CHARACTERS_DIR) -> None:
        self.directory = directory
        self._lock = threading.RLock()
        self._specs: dict[str, CharacterSpec] = {}
        self.reload()

    def reload(self) -> list[str]:
        os.makedirs(self.directory, exist_ok=True)
        loaded: dict[str, CharacterSpec] = {}

        for path in sorted(glob.glob(os.path.join(self.directory, "*.json"))):
            fname = os.path.basename(path)
            if fname.startswith("_"):
                continue
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)

            cid = str(raw["id"]).strip().lower()
            voice = raw.get("voice", {})
            persona = raw.get("persona", {})
            ui_raw = raw.get("ui", {})

            piper_path = _resolve_model_path(voice.get("piper_onnx", "piper/en_US-ryan-medium.onnx"))
            assert piper_path is not None
            rvc_path = _resolve_model_path(voice.get("rvc_onnx"))

            wake_words = list(raw.get("wake_words") or [cid])
            starts, mid = _compile_wake_regexes(wake_words)

            rewrites: list[tuple[re.Pattern[str], str]] = []
            for item in persona.get("phrase_rewrites", []):
                if isinstance(item, list | tuple) and len(item) == 2:
                    rewrites.append((re.compile(str(item[0]), re.IGNORECASE), str(item[1])))

            tts_pron: list[tuple[re.Pattern[str], str]] = []
            for item in voice.get("tts_pronunciations", []):
                if isinstance(item, list | tuple) and len(item) == 2:
                    tts_pron.append((re.compile(str(item[0]), re.IGNORECASE), str(item[1])))

            name = str(raw.get("name", cid.capitalize()))
            ui = {
                "accent": ui_raw.get("accent", "#f5a623"),
                "bmo_shell": ui_raw.get("bmo_shell", "#53b09c"),
                "bmo_shell_dark": ui_raw.get("bmo_shell_dark", "#3b8978"),
                "bmo_screen_rgb": ui_raw.get("bmo_screen_rgb", [205, 238, 224]),
                "bmo_badge_prefix": ui_raw.get("bmo_badge_prefix", "BMO"),
                "idle_mood": ui_raw.get("idle_mood", "neutral"),
            }

            spec = CharacterSpec(
                id=cid,
                order=int(raw.get("order", 50)),
                name=name,
                title=str(raw.get("title", name)),
                emoji=str(raw.get("emoji", "🎙️")),
                subtitle=str(raw.get("subtitle", f"{name} voice assistant")),
                wake_words=wake_words,
                piper_onnx=piper_path,
                rvc_onnx=rvc_path,
                sample_rate=int(voice.get("sample_rate", 48000)),
                default_transpose=int(voice.get("default_transpose", 0)),
                default_speed=float(voice.get("default_speed", 1.0)),
                speech_style=str(persona.get("speech_style", "clean")).lower(),
                phrase_rewrites=rewrites,
                tts_pronunciations=tts_pron,
                system_prompt=str(
                    persona.get(
                        "system_prompt",
                        f"You are {name}. Answer accurately in 1 or 2 short spoken sentences under 30 words.",
                    )
                ),
                wake_replies=list(persona.get("wake_replies") or [f"{name} here. How can I help?"]),
                common_openers=list(persona.get("common_openers") or []),
                sleep_reply=str(persona.get("sleep_reply", "Going to standby.")),
                reset_reply=str(persona.get("reset_reply", "Memory cleared.")),
                fallback_reply=str(persona.get("fallback_reply", "Say that again?")),
                ui=ui,
                start_wake_patterns=starts,
                mid_wake_pattern=mid,
            )
            loaded[cid] = spec

        ordered = dict(sorted(loaded.items(), key=lambda kv: (kv[1].order, kv[0])))
        with self._lock:
            self._specs = ordered
        return list(ordered.keys())

    @property
    def default_id(self) -> str:
        with self._lock:
            if "rocky" in self._specs:
                return "rocky"
            return next(iter(self._specs), "rocky")

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._specs.keys())

    def has(self, char_id: str) -> bool:
        with self._lock:
            return char_id.lower() in self._specs

    def get(self, char_id: str | None) -> CharacterSpec:
        with self._lock:
            if char_id and char_id.lower() in self._specs:
                return self._specs[char_id.lower()]
            return self._specs[self.default_id]

    def all_specs(self) -> list[CharacterSpec]:
        with self._lock:
            return list(self._specs.values())

    def public_map(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {cid: spec.to_public_dict() for cid, spec in self._specs.items()}


REGISTRY = CharacterRegistry()
