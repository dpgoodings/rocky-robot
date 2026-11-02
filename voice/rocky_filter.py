"""
Generic Multi-Character Speech Filters, Dynamic Wake-Word Parser & Emotion Detector.

Reads wake words, `speech_style` ("clean" | "eridian" | "british_deadpan"), custom
`phrase_rewrites`, and default `idle_mood` dynamically from `character_registry.REGISTRY`.
"""

from __future__ import annotations

import re
from typing import Any

from character_registry import REGISTRY

BASE_SLEEP_COMMANDS = {
    "stop",
    "go to sleep",
    "sleep now",
    "goodbye",
    "bye",
    "never mind",
    "nevermind",
    "cancel",
    "quiet",
    "be quiet",
}

RESET_COMMANDS = {
    "reset conversation",
    "clear memory",
    "forget everything",
    "new conversation",
    "start over",
}

WHISPER_HALLUCINATIONS = {
    "thank you",
    "thank you.",
    "thanks for watching",
    "thanks for watching.",
    "thank you for watching.",
    "you",
    "bye.",
    "subtitles by the amara.org community",
    "amara.org",
    "...",
}

EMPHASIS_WORDS = {
    "amazing": "amaze amaze amaze",
    "awesome": "amaze amaze amaze",
    "wonderful": "amaze amaze amaze",
    "incredible": "amaze amaze amaze",
    "fantastic": "amaze amaze amaze",
    "excellent": "good good good",
    "great": "good good good",
    "perfect": "good good good",
    "terrible": "bad bad bad",
    "awful": "bad bad bad",
    "horrible": "bad bad bad",
    "excited": "happy happy happy",
    "thrilled": "happy happy happy",
    "furious": "angry angry angry",
    "terrifying": "scary scary scary",
    "dangerous": "danger danger danger",
    "confused": "confuse",
    "confusing": "confuse",
    "apologize": "apology",
    "sorry": "apology",
}

ERIDIAN_REWRITES = [
    (re.compile(r"\bi don'?t understand\b", re.IGNORECASE), "Rocky no understand"),
    (re.compile(r"\bi do not understand\b", re.IGNORECASE), "Rocky no understand"),
    (re.compile(r"\bi don'?t know\b", re.IGNORECASE), "Rocky not know"),
    (re.compile(r"\bi do not know\b", re.IGNORECASE), "Rocky not know"),
    (re.compile(r"\bi'?m not sure\b", re.IGNORECASE), "Rocky not sure"),
    (re.compile(r"\bwhat do you mean\b", re.IGNORECASE), "what mean"),
    (re.compile(r"\bwhat does that mean\b", re.IGNORECASE), "what mean"),
    (re.compile(r"\bi can'?t\b", re.IGNORECASE), "no can"),
    (re.compile(r"\bcannot\b", re.IGNORECASE), "no can"),
    (re.compile(r"\bwon'?t\b", re.IGNORECASE), "no will"),
    (re.compile(r"\bdon'?t\b", re.IGNORECASE), "not"),
    (re.compile(r"\bdoesn'?t\b", re.IGNORECASE), "not"),
    (re.compile(r"\bdidn'?t\b", re.IGNORECASE), "not"),
    (re.compile(r"\bi'?m\b", re.IGNORECASE), "Rocky"),
    (re.compile(r"\bto be honest,?\s*", re.IGNORECASE), ""),
    (re.compile(r"\bbasically,?\s*", re.IGNORECASE), ""),
    (re.compile(r"\bactually,?\s*", re.IGNORECASE), ""),
    (re.compile(r"\bliterally\s+", re.IGNORECASE), ""),
    (re.compile(r"\bas an ai(?: language model)?,?\s*", re.IGNORECASE), ""),
]

RE_ASTERISK = re.compile(r"\*[^*]+\*")
RE_BRACKETS = re.compile(r"\[[^\]]+\]")
RE_STAGE_DIRECTIONS = re.compile(
    r"\([^)]*(?:sigh|groan|moan|pause|chord|note|tone|click|chirp|sound|music|monotone|deadpan)[^)]*\)",
    re.IGNORECASE,
)
RE_CODE_BLOCK = re.compile(r"```[\s\S]*?```")
RE_INLINE_CODE = re.compile(r"`([^`]+)`")
RE_HEADING = re.compile(r"^\s*#+\s+", re.MULTILINE)
RE_HASHTAG = re.compile(r"#([A-Za-z0-9_]+)")
RE_MD_SYMBOLS = re.compile(r"[*_~#>]+")
RE_LIST_BULLETS = re.compile(r"^\s*[-•]\s+", re.MULTILINE)
RE_NEWLINES = re.compile(r"\n+")
RE_NON_ASCII_SPECIAL = re.compile(r"[^\x00-\x7F°]+")
RE_FAHRENHEIT = re.compile(r"(\d)\s*°\s*F\b")
RE_CELSIUS = re.compile(r"(\d)\s*°\s*C\b")
RE_KELVIN = re.compile(r"(\d)\s*°\s*K\b")
RE_DEGREES = re.compile(r"(\d)\s*°")
RE_PERCENT = re.compile(r"(\d)\s*%")
RE_WHITESPACE = re.compile(r"\s+")
RE_AI_DISCLAIMER = re.compile(r"\bas an ai(?: language model)?,?\s*", re.IGNORECASE)
RE_MARVIN_SIGH = re.compile(r"^(?:sigh|ugh|oh dear)[.,!]\s*", re.IGNORECASE)
RE_WORD_PUNCT = re.compile(r"^([A-Za-z']+)([^A-Za-z']*)$")

LIGHT_ARTICLES = {"a", "an", "the"}


def is_hallucination(text: str) -> bool:
    cleaned = text.strip().lower()
    if not cleaned:
        return True
    alpha_only = re.sub(r"[^a-z0-9]", "", cleaned)
    if len(alpha_only) < 2:
        return True
    return cleaned in WHISPER_HALLUCINATIONS


def _is_sleep_phrase(clean_rem: str) -> bool:
    if clean_rem in BASE_SLEEP_COMMANDS:
        return True
    for spec in REGISTRY.all_specs():
        for w in spec.wake_words:
            wl = w.lower()
            if clean_rem in (f"bye {wl}", f"goodbye {wl}", f"goodnight {wl}"):
                return True
    return False


def parse_wake_utterance(
    transcript: str,
    active_character: str = "rocky",
    require_wake_word: bool = True,
    in_followup_window: bool = False,
) -> dict[str, Any]:
    """
    Dynamically match wake words across ALL registered characters in `characters/*.json`.
    Saying 'Hey <Name>' automatically switches to that character!
    """
    fallback_char = REGISTRY.get(active_character).id
    raw = transcript.strip()
    if is_hallucination(raw):
        return {
            "triggered": False,
            "character": fallback_char,
            "wake_only": False,
            "sleep": False,
            "reset": False,
            "query": "",
        }

    matched_wake = False
    detected_char = fallback_char
    remainder = raw

    for spec in REGISTRY.all_specs():
        for pat in spec.start_wake_patterns:
            m = pat.match(raw)
            if m:
                matched_wake = True
                detected_char = spec.id
                remainder = raw[m.end() :].strip()
                break
        if matched_wake:
            break

    if not matched_wake:
        for spec in REGISTRY.all_specs():
            if spec.mid_wake_pattern is None:
                continue
            m = spec.mid_wake_pattern.search(raw)
            if m and m.start() <= 16:
                matched_wake = True
                detected_char = spec.id
                remainder = raw[m.end() :].strip()
                break

    if require_wake_word and not in_followup_window and not matched_wake:
        return {
            "triggered": False,
            "character": fallback_char,
            "wake_only": False,
            "sleep": False,
            "reset": False,
            "query": "",
        }

    clean_rem = re.sub(r"[.!?]+$", "", remainder.strip()).lower()

    if matched_wake and not clean_rem:
        return {
            "triggered": True,
            "character": detected_char,
            "wake_only": True,
            "sleep": False,
            "reset": False,
            "query": "",
        }

    if _is_sleep_phrase(clean_rem):
        return {
            "triggered": True,
            "character": detected_char,
            "wake_only": False,
            "sleep": True,
            "reset": False,
            "query": "",
        }

    if clean_rem in RESET_COMMANDS:
        return {
            "triggered": True,
            "character": detected_char,
            "wake_only": False,
            "sleep": False,
            "reset": True,
            "query": "",
        }

    query = remainder if matched_wake else raw
    return {
        "triggered": True,
        "character": detected_char,
        "wake_only": False,
        "sleep": False,
        "reset": False,
        "query": query.strip(),
    }


def clean_for_speech(text: str) -> str:
    """Remove markdown, stage directions (*sigh*, [groans]), lists, and expand symbols."""
    if not text:
        return ""

    s = RE_ASTERISK.sub(" ", text)
    s = RE_BRACKETS.sub(" ", s)
    s = RE_STAGE_DIRECTIONS.sub(" ", s)
    s = RE_CODE_BLOCK.sub(" ", s)
    s = RE_INLINE_CODE.sub(r"\1", s)
    s = RE_HEADING.sub("", s)
    s = RE_HASHTAG.sub(r"hashtag \1", s)
    s = RE_MD_SYMBOLS.sub("", s)
    s = RE_LIST_BULLETS.sub("", s)
    s = RE_NEWLINES.sub(" ", s)
    s = RE_NON_ASCII_SPECIAL.sub(" ", s)

    s = RE_FAHRENHEIT.sub(r"\1 degrees Fahrenheit", s)
    s = RE_CELSIUS.sub(r"\1 degrees Celsius", s)
    s = RE_KELVIN.sub(r"\1 Kelvin", s)
    s = RE_DEGREES.sub(r"\1 degrees", s)
    s = RE_PERCENT.sub(r"\1 percent", s)
    s = s.replace("&", " and ")

    return RE_WHITESPACE.sub(" ", s).strip()


def apply_clean_filter(text: str) -> str:
    s = clean_for_speech(text)
    if not s:
        return s
    return RE_AI_DISCLAIMER.sub("", s).strip()


def apply_marvin_filter(text: str) -> str:
    s = apply_clean_filter(text)
    if not s:
        return s
    return RE_MARVIN_SIGH.sub("Oh dear. ", s).strip()


def apply_rocky_filter(text: str, intensity: str = "balanced") -> str:
    s = clean_for_speech(text)
    if not s or intensity == "off":
        return s

    parts = re.split(r"(?<=[.!?])\s+", s)
    out_sentences: list[str] = []

    for part in parts:
        sent = part.strip()
        if not sent:
            continue

        is_q = sent.endswith("?")

        for pat, rep in ERIDIAN_REWRITES:
            sent = pat.sub(rep, sent)

        words = sent.split()
        transformed: list[str] = []
        for idx, token in enumerate(words):
            m = RE_WORD_PUNCT.match(token)
            if not m:
                transformed.append(token)
                continue
            word, punct = m.group(1), m.group(2)
            low = word.lower()

            prev_low = transformed[-1].rstrip(".,!?").lower() if transformed else ""
            if low in EMPHASIS_WORDS and prev_low not in ("amaze", "good", "bad", "happy", "danger"):
                transformed.append(EMPHASIS_WORDS[low] + punct)
                continue

            if low in LIGHT_ARTICLES:
                next_low = words[idx + 1].lower().rstrip(".,!?") if idx + 1 < len(words) else ""
                if intensity == "strict" or next_low not in ("few", "little", "lot", "bit"):
                    continue

            if intensity == "strict" and idx > 0 and low in {"is", "are", "was", "were", "am"}:
                continue

            transformed.append(token)

        sent = " ".join(transformed).strip()
        if not sent:
            continue

        if is_q:
            core = sent.rstrip("?.!,").strip()
            if not re.search(r"\bquestion$", core, flags=re.IGNORECASE):
                sent = f"{core}, question?"
            else:
                sent = f"{core}?"

        sent = sent[0].upper() + sent[1:]
        out_sentences.append(sent)

    final = " ".join(out_sentences)
    final = re.sub(r"\s+([.,!?])", r"\1", final)
    final = re.sub(r",\s*,", ",", final)
    final = re.sub(r"\b(\w+)(?:\s+\1){3,}\b", r"\1 \1 \1", final, flags=re.IGNORECASE)
    return final.strip()


def apply_character_filter(text: str, character: str = "rocky", intensity: str = "balanced") -> str:
    spec = REGISTRY.get(character)
    style = spec.speech_style
    if style == "eridian":
        out = apply_rocky_filter(text, intensity=intensity)
    elif style == "british_deadpan":
        out = apply_marvin_filter(text)
    else:
        out = apply_clean_filter(text)

    for pat, rep in spec.phrase_rewrites:
        out = pat.sub(rep, out)
    return out.strip()


_EMOTION_TAG_RE = re.compile(
    r"^\s*\[(excited|happy|curious|focused|alarmed|sad|bored|pained|sleepy|neutral)\]\s*",
    re.IGNORECASE,
)
_EXCITED_RE = re.compile(
    r"\b(?:amaze|good\s+good|happy\s+happy|yay|woohoo|hooray|oh\s+boy|video\s+games|bmo\s+chop|mathematical|celebrate|fist\s+my\s+bump|fresh\s+start|awesome|wonderful|brilliant)\b",
    re.IGNORECASE,
)
_ALARMED_RE = re.compile(
    r"\b(?:danger|bad\s+bad|scary|explode|explosion|melt|deadly|crush|careful|warning|radiation|poison|boiling|venus)\b",
    re.IGNORECASE,
)
_PAINED_RE = re.compile(
    r"\b(?:diode|diodes|ache|aching|pain|miserable|agony|headache|rusting)\b",
    re.IGNORECASE,
)
_SAD_RE = re.compile(
    r"\b(?:apology|sorry|sad|alone|lonely|miss|mourn|quiet\s+now|brooding|nothing\s+good|end\s+of\s+the\s+universe|forget\s+how\s+tedious|sigh|oh\s+dear)\b",
    re.IGNORECASE,
)
_BORED_RE = re.compile(
    r"\b(?:mundane|thrilling|tedious|bored|boring|dreary|hardly\s+seems\s+worth|doubt\s+i\s+shall|suspense|unimpressed)\b",
    re.IGNORECASE,
)
_CURIOUS_RE = re.compile(
    r"(?:\bquestion\?|\?\s*$|\b(?:wonder|curious|what\s+need|what\s+mean|interesting)\b)",
    re.IGNORECASE,
)
_FOCUSED_RE = re.compile(
    r"(?:\b\d+(?:[.,]\d+)?\b|\b(?:science|math|calculate|calculated|meters|degrees|celsius|fahrenheit|kelvin|scatter|atmosphere|gravity|orbit|oxide|wavelength|planet|sync|telemetry|confirmed)\b)",
    re.IGNORECASE,
)
_HAPPY_RE = re.compile(
    r"\b(?:good|easy|friend|yes\s+grace|glad|nice|welcome|here|love|loves|cute|fun|play|finn|jake|football|beep\s+boop)\b",
    re.IGNORECASE,
)


def detect_utterance_emotion(text: str, character: str = "rocky") -> str:
    """Zero-latency (<0.01ms) lexical + punctuation emotion detector for BMO face sync."""
    spec = REGISTRY.get(character)
    default_mood = str(spec.ui.get("idle_mood", "neutral"))
    if not text:
        return default_mood

    m = _EMOTION_TAG_RE.match(text)
    if m:
        return m.group(1).lower()

    s = text.strip()
    if _PAINED_RE.search(s):
        return "pained"
    if _ALARMED_RE.search(s):
        return "alarmed"
    if _EXCITED_RE.search(s):
        return "excited"
    if _SAD_RE.search(s):
        return "sad"
    if _BORED_RE.search(s):
        return "bored"
    if _CURIOUS_RE.search(s):
        return "curious"
    if spec.speech_style == "eridian" and s.endswith("!") and _HAPPY_RE.search(s):
        return "excited"
    if _FOCUSED_RE.search(s):
        return "focused"
    if _HAPPY_RE.search(s) or (default_mood == "neutral" and s.endswith("!")):
        return "happy"

    return default_mood
