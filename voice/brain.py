"""
Rocky Local Streaming Brain — Powered by local Ollama
(qwen2.5:1.5b for fast text chat + qwen3-vl:2b-instruct for 1-hop live vision Q&A).

Supports Rocky's Eridian persona, all 14 face expressions, deterministic physical intents
(dance, head look/turn, face tracking), 1-hop grounded vision & follow-ups, SQLite memory,
biometric face enrollment/recognition, Motion Guard, Home Assistant MCP, and macOS MCP.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator
from datetime import datetime
import json
import os
import re
import subprocess
import threading
import time
from typing import Any

import httpx

import memory
import robot_vision
from character_registry import REGISTRY
from face_tracker import FACE_TRACKER
from mcp_homeassistant import detect_and_execute_hass_intent
from mcp_mac import detect_and_execute_mac_control
from rocky_filter import apply_character_filter

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
OLLAMA_BIN = os.path.join(PROJECT_DIR, "bin", "ollama")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("ROCKY_LLM_MODEL", "qwen2.5:1.5b")
VISION_MODEL = os.environ.get("ROCKY_VISION_MODEL", "qwen3-vl:2b-instruct")
OLLAMA_NUM_CTX = 2048

CLOUD_PROVIDER_BASE_URLS: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
}
CLOUD_PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "gemini": "gemini-2.5-flash",
    "openrouter": "google/gemini-2.5-flash",
    "openai": "gpt-4.1-mini",
}
_LLM_RUNTIME_CONFIG: dict[str, str] = {
    "provider": "ollama",
    "cloud_model": "gemini-2.5-flash",
    "cloud_api_key": "",
}


def set_llm_runtime_config(
    provider: str = "ollama",
    cloud_model: str = "",
    cloud_api_key: str = "",
) -> dict[str, Any]:
    """Configure local Ollama vs optional OpenAI-compatible Cloud Brain (Gemini/OpenRouter/OpenAI)."""
    prov = (provider or "ollama").strip().lower()
    if prov not in ("ollama", "gemini", "openrouter", "openai"):
        prov = "ollama"
    _LLM_RUNTIME_CONFIG["provider"] = prov
    if cloud_model and cloud_model.strip():
        _LLM_RUNTIME_CONFIG["cloud_model"] = cloud_model.strip()
    elif prov in CLOUD_PROVIDER_DEFAULT_MODELS and not _LLM_RUNTIME_CONFIG.get("cloud_model"):
        _LLM_RUNTIME_CONFIG["cloud_model"] = CLOUD_PROVIDER_DEFAULT_MODELS[prov]
    if cloud_api_key is not None:
        _LLM_RUNTIME_CONFIG["cloud_api_key"] = cloud_api_key.strip()
    return get_llm_runtime_status()


def get_llm_runtime_status() -> dict[str, Any]:
    prov = _LLM_RUNTIME_CONFIG.get("provider", "ollama")
    key = _LLM_RUNTIME_CONFIG.get("cloud_api_key", "")
    active_cloud = prov in CLOUD_PROVIDER_BASE_URLS and bool(key)
    model = _LLM_RUNTIME_CONFIG.get("cloud_model") or CLOUD_PROVIDER_DEFAULT_MODELS.get(prov, DEFAULT_MODEL)
    return {
        "provider": prov,
        "active_provider": prov if active_cloud else "ollama",
        "active_model": model if active_cloud else DEFAULT_MODEL,
        "cloud_model": model,
        "cloud_api_key_set": bool(key),
    }


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Ollama chat messages (including optional base64 `images`) to OpenAI-compatible format."""
    converted: list[dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role", "user"))
        content = msg.get("content", "")
        images = msg.get("images") or []
        if images and isinstance(content, str):
            parts: list[dict[str, Any]] = [{"type": "text", "text": content}]
            for b64 in images:
                if b64:
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        }
                    )
            converted.append({"role": role, "content": parts})
        else:
            converted.append({"role": role, "content": content})
    return converted


def _stream_openai_compat(
    messages: list[dict[str, Any]],
    max_tokens: int = 160,
    temperature: float = 0.7,
    timeout: float = 35.0,
) -> Iterator[str]:
    """Stream tokens from an OpenAI-compatible Cloud Brain (Gemini, OpenRouter, or OpenAI)."""
    prov = _LLM_RUNTIME_CONFIG.get("provider", "ollama")
    api_key = _LLM_RUNTIME_CONFIG.get("cloud_api_key", "").strip()
    base_url = CLOUD_PROVIDER_BASE_URLS.get(prov)
    if not base_url or not api_key:
        raise RuntimeError(f"Cloud provider '{prov}' is not configured")

    model = _LLM_RUNTIME_CONFIG.get("cloud_model") or CLOUD_PROVIDER_DEFAULT_MODELS.get(prov, "")
    headers: dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if prov == "openrouter":
        headers["HTTP-Referer"] = "http://127.0.0.1:7860"
        headers["X-Title"] = "Rocky Robot Studio"

    payload: dict[str, Any] = {
        "model": model,
        "messages": _to_openai_messages(messages),
        "stream": True,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }

    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", f"{base_url}/chat/completions", headers=headers, json=payload) as resp:
            if resp.status_code >= 400:
                body_preview = resp.read().decode("utf-8", errors="replace")[:240]
                raise RuntimeError(f"HTTP {resp.status_code}: {body_preview}")
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str or data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except Exception:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                tok = delta.get("content")
                if isinstance(tok, str) and tok:
                    yield tok


_TEACH_RULE_PATTERNS = [
    re.compile(
        r"\b(?:from\s+now\s+on|starting\s+now|going\s+forward)\s*,?\s*(.+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:new\s+rule|save\s+(?:a\s+)?rule|add\s+(?:a\s+)?rule|teach\s+rule)\s*[:,-]?\s*(.+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bremember\s+to\s+(.+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:please\s+)?(?:always|never|stop\s+saying|don'?t\s+ever\s+say|refer\s+to)\s+(.+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:can|could|will)\s+you\s+(?:always|never|refer\s+to)\s+(.+)",
        re.IGNORECASE,
    ),
]


def parse_teach_rule_intent(user_text: str) -> str | None:
    """Detects spoken behavioral rules taught to Rocky mid-conversation."""
    text = (user_text or "").strip()
    if not text or len(text) < 8:
        return None
    # Guard against questions about tracking/face/time/vision
    if re.search(r"\b(?:stop\s+tracking|stop\s+following|stop\s+watching|stop\s+guard)\b", text, re.I):
        return None
    for idx, pat in enumerate(_TEACH_RULE_PATTERNS):
        m = pat.search(text)
        if m:
            if idx == 3:
                # Preserve leading keyword ("Always ...", "Never ...", "Stop saying ...", "Refer to ...")
                rule = re.sub(r"^please\s+", "", text, flags=re.I).strip(" .!?")
            elif idx == 4:
                # "can you refer to X as Y" -> "Refer to X as Y"
                lead = re.search(r"\b(always|never|refer\s+to)\s+(.+)", text, re.I)
                rule = f"{lead.group(1).strip().capitalize()} {lead.group(2).strip(' .!?')}" if lead else m.group(1).strip(" .!?")
            else:
                rule = m.group(1).strip(" .!?")
            if len(rule) >= 5:
                return rule[0].upper() + rule[1:]
    return None

_EVENT_LOGGER: Callable[[str, str], None] | None = None


def set_event_logger(fn: Callable[[str, str], None] | None) -> None:
    """Register an optional server log ring-buffer callback `(category, message)`."""
    global _EVENT_LOGGER
    _EVENT_LOGGER = fn


def _log_event(category: str, message: str) -> None:
    if _EVENT_LOGGER is not None:
        try:
            _EVENT_LOGGER(category, message)
            return
        except Exception:
            pass
    print(f"[{category}] {message}", flush=True)


TIME_QUERY_RE = re.compile(
    r"\b(?:"
    r"what(?:'s|\s+is)?\s+(?:the\s+)?(?:current\s+)?(?:time|date|day(?:\s+(?:of|a)\s+(?:the\s+)?week)?|month|year)"
    r"|what\s+day(?:\s+(?:of|a)\s+(?:the\s+)?week)?\s+(?:is\s+)?(?:it|today|tomorrow|yesterday)"
    r"|what\s+day\s+(?:of|a)\s+(?:the\s+)?week\b"
    r"|what(?:'s|\s+is)\s+(?:today|tomorrow|yesterday)(?:'s\s+(?:date|day))?\b"
    r"|today'?s\s+(?:date|day)"
    r"|tell\s+me\s+the\s+(?:time|date|day)"
    r"|do\s+you\s+(?:have|know)\s+the\s+(?:time|date|day)"
    r"|current\s+(?:time|date)"
    r"|is\s+(?:today|it|tomorrow|yesterday)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r")\b",
    re.IGNORECASE,
)


def _format_time_date_reply(user_text: str, now: datetime | None = None) -> str:
    """Deterministic, 100% accurate Rocky reply for clock & calendar date questions."""
    from datetime import timedelta

    now = now or datetime.now()
    low = (user_text or "").lower()
    short_time = now.strftime("%I:%M %p").lstrip("0")
    weekday = now.strftime("%A")
    month_day = f"{now.strftime('%B')} {now.day}, {now.year}"
    tomorrow_day = (now + timedelta(days=1)).strftime("%A")
    yesterday_day = (now - timedelta(days=1)).strftime("%A")

    # Yes/no weekday check: "is today Sunday?", "is tomorrow Monday?"
    m_day = re.search(
        r"\bis\s+(today|it|tomorrow|yesterday)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        low,
    )
    if m_day:
        when, asked_day = m_day.group(1), m_day.group(2).capitalize()
        actual = tomorrow_day if when == "tomorrow" else (yesterday_day if when == "yesterday" else weekday)
        label = "Tomorrow" if when == "tomorrow" else ("Yesterday" if when == "yesterday" else "Today")
        if actual.lower() == asked_day.lower():
            return f"Yes yes yes, friend! {label} is {actual}, {month_day}!"
        if when in ("today", "it") and tomorrow_day.lower() == asked_day.lower():
            return f"Not {asked_day}, friend! Today is {weekday}, {month_day}, tomorrow is {tomorrow_day}!"
        return f"Not {asked_day}, friend! {label} is {actual}, {month_day}!"

    if "tomorrow" in low:
        tm = now + timedelta(days=1)
        return f"Tomorrow is {tomorrow_day}, {tm.strftime('%B')} {tm.day}, {tm.year}, friend!"
    if "yesterday" in low:
        yd = now - timedelta(days=1)
        return f"Yesterday was {yesterday_day}, {yd.strftime('%B')} {yd.day}, {yd.year}, friend!"

    wants_time = bool(re.search(r"\btime\b", low))
    wants_date = bool(re.search(r"\b(?:date|month|year|calendar)\b", low))
    wants_day = bool(re.search(r"\b(?:day|week|today)\b", low))

    if wants_time and (wants_date or wants_day):
        return f"Right now is {short_time} on {weekday}, {month_day}, friend!"
    if wants_time:
        return f"Right now is {short_time}, friend! Rocky clock checked, good good good!"
    if wants_date or wants_day:
        return f"Today is {weekday}, {month_day}, friend! Good good good!"
    return f"Right now is {short_time} on {weekday}, {month_day}, friend!"
FACE_RECOG_QUERY_RE = re.compile(
    r"\b(?:who\s+am\s+i|recognize\s+(?:me|my\s+face)|do\s+you\s+know\s+(?:who\s+i\s+am|me))\b",
    re.IGNORECASE,
)
FACE_EXPR_QUERY_RE = re.compile(
    r"\b(?:"
    r"facial\s+expression"
    r"|my\s+expression"
    r"|am\s+i\s+(?:smiling|frowning|looking\s+(?:happy|sad|angry|surprised))"
    r"|do\s+i\s+look\s+(?:happy|sad|angry|surprised|tired|mad)"
    r"|what(?:'s|\s+is)\s+my\s+(?:facial\s+expression|expression|mood)"
    r")\b",
    re.IGNORECASE,
)
FACE_ENROLL_QUERY_RE = re.compile(
    r"\b(?:remember\s+my\s+face|learn\s+my\s+face|register\s+my\s+face|this\s+is\s+my\s+face)\b",
    re.IGNORECASE,
)
MOTION_GUARD_RE = re.compile(
    r"\b(?:(turn\s+on|enable|start)\s+(?:guard\s+mode|motion\s+(?:detection|guard)|watching)|(turn\s+off|disable|stop)\s+(?:guard\s+mode|motion\s+(?:detection|guard)|watching))\b",
    re.IGNORECASE,
)
VISION_QUERY_RE = re.compile(
    r"\b(?:"
    r"what\s+(?:do|can)\s+you\s+see"
    r"|what\s+are\s+you\s+(?:seeing|looking\s+at)"
    r"|what(?:'s|\s+is)?\s+(?:in\s+front(?:\s+of\s+you)?|over\s+there|in\s+my\s+hands?|on\s+(?:the\s+|my\s+)?(?:desk|table|couch|sofa|floor|wall|head|shirt|lap)|around\s+(?:you|me)|behind\s+me|next\s+to\s+me|beside\s+me)"
    r"|(?:what(?:'s|\s+is)\s+(?:this|that)\b|do\s+you\s+know\s+what\s+(?:this|that)(?:\s+is)?\b|can\s+you\s+tell\s+(?:me\s+)?what\s+(?:this|that)(?:\s+is)?\b|guess\s+what\s+(?:this|that)(?:\s+is)?\b)"
    r"|what\s+color(?:\s+\w+){0,3}\s+(?:is|are|am\s+i|wearing|holding|this|that)"
    r"|what\s+color\s+(?:is|are)\b"
    r"|what\s+(?:am\s+i|i(?:'m|\s+am)|is\s+(?:he|she|they|my\s+\w+)|are\s+(?:we|they))\s+(?:holding|wearing|doing|showing|drinking|eating|pointing\s+at)"
    r"|am\s+i\s+(?:holding|wearing|drinking|eating|waving)\b"
    r"|describe\s+(?:what\s+you\s+see|the\s+room|the\s+view|the\s+scene|everything|me|this|that)"
    r"|look(?:\s+at)?\s+(?:this|that|me|my|the|around|ahead|through|here|over\s+there)"
    r"|(?:can|do|could|will)\s+you\s+(?:see|spot|count\s+(?:my\s+)?fingers|have\s+(?:sight|eyes|vision)|(?:use|see\s+with)\s+(?:the\s+|your\s+)?(?:esp32|robot|cam|camera))"
    r"|are\s+you\s+blind"
    r"|see\s+through\s+(?:the\s+|your\s+)?(?:esp32|robot|cam|camera)"
    r"|how\s+many\s+(?:fingers|hands|people|persons|kids|children|objects|items|things|cups|mugs|chairs|pillows|pets|dogs|cats)\b"
    r"|count\s+(?:my\s+|the\s+)?(?:fingers|people|objects|items)\b"
    r"|who(?:\s+else)?\s+(?:do\s+you\s+see|is\s+(?:in\s+(?:the\s+room|front(?:\s+of\s+you)?|view|frame|here|there)|next\s+to\s+me|beside\s+me|with\s+me|sitting\s+(?:next\s+to\s+me|on\s+the\s+couch|there)|behind\s+me|this|that))"
    r"|is\s+(?:anyone|anybody|someone|somebody)\s+(?:else\s+)?(?:in\s+(?:the\s+room|view|frame|front|here|there)|next\s+to\s+me|beside\s+me|with\s+me|behind\s+me|there|here|visible)"
    r"|is\s+my\s+(?:son|daughter|wife|husband|kid|child|dog|cat|friend)(?:\s+[A-Za-z]+)?\s+(?:in\s+(?:the\s+room|view|frame|front|here|there)|next\s+to\s+me|beside\s+me|with\s+me|behind\s+me|there|here|visible)"
    r"|is\s+there\s+(?:a|an|any)\s+[^?]{1,40}\b(?:in\s+(?:the\s+room|view|frame|front|my\s+hand|here|there)|on\s+(?:the\s+|my\s+)?(?:desk|table|couch|sofa|floor|wall)|next\s+to\s+me|beside\s+me|behind\s+me|visible)"
    r"|are\s+there\s+(?:any\s+)?(?:people|kids|children|objects|cups|mugs|fingers|pets|dogs|cats)\b"
    r"|tell\s+me\s+what\s+you\s+see"
    r"|how\s+do\s+i\s+look|what\s+do\s+i\s+look\s+like"
    r"|(?:can|could|will|would)\s+you\s+(?:please\s+)?read\b"
    r"|read\s+(?:this|that|it|aloud|out\s+loud|what(?:'s|\s+is)\s+(?:on|written|printed)|(?:the\s+)?(?:whole|entire|full|rest\s+of|last|next|bottom|top|card|sign|text|words?|lines?|sentences?|note|label|letter|book|page|screen|message))"
    r"|what\s+does\s+(?:(?:this|that|the)\s+(?:card|sign|note|label|page|text|screen|paper|box|bottle|shirt)|this|that|it)\s+say"
    r"|what(?:'s|\s+is)\s+(?:written|printed)\s+on\b"
    r")\b",
    re.IGNORECASE,
)
VISION_FOLLOWUP_RE = re.compile(
    r"^\s*(?:how\s+about\s+now|what\s+about\s+now|and\s+now|now|look\s+again|check\s+again|try\s+again"
    r"|and\s+then\s+what|what(?:'s|\s+is)\s+(?:next|the\s+rest|after\s+that|below\s+that)"
    r"|(?:can|could)\s+you\s+finish(?:\s+it)?|finish\s+(?:it|reading)|keep\s+reading|read\s+(?:more|the\s+rest|on)|anything\s+else)\s*[?!.]*\s*$",
    re.IGNORECASE,
)
_READ_TEXT_VQA_RE = re.compile(
    r"\b(?:read|reading|written|printed|text|words?|lines?|sentences?|card|sign|note|label|page|finish\s+it|keep\s+reading|and\s+then\s+what|the\s+rest)\b",
    re.IGNORECASE,
)
CAMERA_SWITCH_RE = re.compile(
    r"\b(?:switch(?:\s+to)?|change(?:\s+to)?|turn\s+on|(?<!\bdo\syou\s)use)\s+(?:to\s+)?(?:the\s+)?(macbook|laptop|built-?in|webcam|esp32|robot|chassis)(?:\s+cam(?:era)?)?\b",
    re.IGNORECASE,
)

# -----------------------------------------------------------------------------
# Rocky Physical Intents: Dance, Head Look/Turn, and Face Tracking
# -----------------------------------------------------------------------------
_DANCE_INFO_GUARD_RE = re.compile(
    r"^\s*(?:what\s+(?:is|are)|why\s+(?:do|does|did|is|are)|how\s+(?:do|does)|tell\s+me\s+about|explain|who\s+invented|history\s+of)\b",
    re.IGNORECASE,
)
DANCE_INTENT_RE = re.compile(
    r"(?:"
    r"^\s*(?:(?:hey|hi|ok|okay|now|please)\s+)*(?:rocky[,!\s]+)*(?:(?:can|could|will|would)\s+you\s+|let'?s\s+|go\s+ahead\s+and\s+)?(?:dance|boogie(?:\s+down)?|groove)(?:\s+(?:for\s+me|with\s+me|now|please|a\s+little|around|groove|robot|sprinkler|disco))*\s*[?!.]*$"
    r"|\bdo\s+(?:a|the|some|your)?\s*(?:little\s+|silly\s+|happy\s+|robot\s+|disco\s+|sprinkler\s+|groove\s+)?dance\b"
    r"|\bdo\s+the\s+(?:sprinkler|robot|disco|groove)\b"
    r"|\b(?:show\s+(?:me|us|off)\s+(?:your\s+)?(?:dance\s+)?moves|bust\s+a\s+move|boogie(?:\s+down)?|time\s+to\s+dance)\b"
    r")",
    re.IGNORECASE,
)


def parse_dance_intent(user_text: str) -> str | None:
    """Detect dance requests and return the ESP32 `/action` name (`dance`, `dance_groove`, etc.)."""
    text = (user_text or "").strip()
    if not text or _DANCE_INFO_GUARD_RE.search(text):
        return None
    if not DANCE_INTENT_RE.search(text):
        return None
    low = text.lower()
    if "groove" in low:
        return "dance_groove"
    if "sprinkler" in low:
        return "dance_sprinkler"
    if "disco" in low:
        return "dance_disco"
    if re.search(r"\brobot\b", low):
        return "dance_robot"
    return "dance"


HEAD_LOOK_ANGLES: dict[str, tuple[int, int]] = {
    "left": (135, 90),
    "right": (45, 90),
    "up": (90, 115),
    "down": (90, 72),
    "center": (90, 90),
}
HEAD_LOOK_RE = re.compile(
    r"\b(?:"
    r"(?:look|glance|peek)\s+(?:to\s+(?:the\s+|your\s+)?|over\s+to\s+(?:the\s+|your\s+)?|towards?\s+(?:the\s+|your\s+)?|a\s+bit\s+|a\s+little\s+)?"
    r"(?P<dir1>left|right|up(?:wards?)?|down(?:wards?)?|center|straight(?:\s+ahead)?|forward)"
    r"|(?:turn|move|point|aim|rotate)\s+(?:your\s+)?(?:head|camera|eyes|neck)\s+(?:to\s+(?:the\s+|your\s+)?|towards?\s+(?:the\s+|your\s+)?|back\s+to\s+(?:the\s+)?)?"
    r"(?P<dir2>left|right|up(?:wards?)?|down(?:wards?)?|center|straight(?:\s+ahead)?|forward)"
    r"|(?P<center_cmd>center\s+(?:your\s+)?(?:head|camera|eyes|neck))"
    r")\b",
    re.IGNORECASE,
)
_LOOK_WITH_VISION_RE = re.compile(
    r"\b(?:"
    r"what(?:'s|\s+is)\s+(?:over\s+)?there"
    r"|tell\s+me\s+what\s+you\s+see"
    r"|what\s+(?:do|can)\s+you\s+see"
    r"|see\s+what(?:'s|\s+is)"
    r"|describe\s+(?:what|the)"
    r"|who(?:'s|\s+is)\s+(?:over\s+)?there"
    r"|anything\s+(?:over\s+)?there"
    r"|check\s+what(?:'s|\s+is)"
    r")\b",
    re.IGNORECASE,
)


def _normalize_direction(raw_dir: str) -> str:
    d = raw_dir.strip().lower()
    if d.startswith("up"):
        return "up"
    if d.startswith("down"):
        return "down"
    if d in ("left", "right"):
        return d
    return "center"


def parse_head_look_intent(user_text: str) -> dict[str, Any] | None:
    """Detect head-look/turn requests and whether the user also wants a vision check after moving."""
    text = (user_text or "").strip()
    if not text:
        return None
    m = HEAD_LOOK_RE.search(text)
    if not m:
        return None
    if m.group("center_cmd"):
        direction = "center"
    else:
        raw_dir = m.group("dir1") or m.group("dir2") or "center"
        direction = _normalize_direction(raw_dir)
    pan, tilt = HEAD_LOOK_ANGLES[direction]
    wants_vision = bool(_LOOK_WITH_VISION_RE.search(text) or VISION_QUERY_RE.search(text))
    return {
        "direction": direction,
        "pan": pan,
        "tilt": tilt,
        "wants_vision": wants_vision,
    }


TRACK_START_RE = re.compile(
    r"\b(?:"
    r"track\s+(?:me|my\s+face)"
    r"|(?:start|enable|turn\s+on)\s+(?:face\s+)?tracking"
    r"|follow\s+(?:me|my\s+face)"
    r"|watch\s+me"
    r"|keep\s+your\s+eyes\s+on\s+me"
    r")\b",
    re.IGNORECASE,
)
TRACK_STOP_RE = re.compile(
    r"\b(?:"
    r"(?:stop|disable|turn\s+off)\s+(?:face\s+)?tracking"
    r"|stop\s+following(?:\s+(?:me|my\s+face))?"
    r"|stop\s+watching\s+me"
    r")\b",
    re.IGNORECASE,
)


def parse_tracking_intent(user_text: str) -> bool | None:
    """Return True to start face tracking, False to stop face tracking, or None."""
    text = (user_text or "").strip()
    if not text:
        return None
    if TRACK_STOP_RE.search(text):
        return False
    if TRACK_START_RE.search(text):
        return True
    return None


# -----------------------------------------------------------------------------
# Robot Face Expression Intent (User asks Rocky to show any of 14+ expressions)
# Maps natural emotion words -> (hud_mood, esp_mood, esp_anim, spoken_label)
# -----------------------------------------------------------------------------
_ROBOT_FACE_EXPR_MAP: dict[str, tuple[str, str, str | None, str]] = {
    # Worried / anxious / nervous
    "worried": ("worried", "worried", None, "worried"),
    "worry": ("worried", "worried", None, "worried"),
    "nervous": ("worried", "worried", None, "nervous"),
    "anxious": ("worried", "worried", None, "anxious"),
    "stressed": ("worried", "worried", None, "stressed"),
    "uneasy": ("worried", "worried", None, "uneasy"),
    # Alarmed / scared
    "scared": ("alarmed", "alarmed", None, "scared"),
    "afraid": ("alarmed", "alarmed", None, "scared"),
    "frightened": ("alarmed", "alarmed", None, "frightened"),
    "terrified": ("alarmed", "alarmed", None, "terrified"),
    "fearful": ("alarmed", "alarmed", None, "fearful"),
    "panicked": ("alarmed", "alarmed", None, "panicked"),
    "panic": ("alarmed", "alarmed", None, "panicked"),
    "alarmed": ("alarmed", "alarmed", None, "alarmed"),
    "spooked": ("alarmed", "alarmed", None, "spooked"),
    # Surprised / shocked
    "surprised": ("surprised", "surprised", None, "surprised"),
    "surprise": ("surprised", "surprised", None, "surprised"),
    "shocked": ("surprised", "surprised", None, "shocked"),
    "shock": ("surprised", "surprised", None, "shocked"),
    "astonished": ("surprised", "surprised", None, "astonished"),
    "startled": ("surprised", "surprised", None, "startled"),
    "amazed": ("surprised", "surprised", None, "amazed"),
    # Happy / smiling
    "happy": ("happy", "happy", None, "happy"),
    "smile": ("happy", "happy", None, "happy smile"),
    "smiling": ("happy", "happy", None, "smiling"),
    "smiley": ("happy", "happy", None, "smiley"),
    "grin": ("happy", "happy", None, "big grin"),
    "grinning": ("happy", "happy", None, "grinning"),
    "cheerful": ("happy", "happy", None, "cheerful"),
    "glad": ("happy", "happy", None, "happy"),
    "joy": ("happy", "happy", None, "joyful"),
    "joyful": ("happy", "happy", None, "joyful"),
    "pleased": ("happy", "happy", None, "pleased"),
    "delighted": ("happy", "happy", None, "delighted"),
    # Excited / laughing / silly
    "excited": ("excited", "excited", "laugh", "excited"),
    "thrilled": ("excited", "excited", "laugh", "thrilled"),
    "hyped": ("excited", "excited", "laugh", "excited"),
    "ecstatic": ("excited", "excited", "laugh", "ecstatic"),
    "laugh": ("excited", "excited", "laugh", "laughing"),
    "laughing": ("excited", "excited", "laugh", "laughing"),
    "giggle": ("excited", "excited", "laugh", "giggly"),
    "giggling": ("excited", "excited", "laugh", "giggly"),
    "silly": ("excited", "excited", "laugh", "silly"),
    "goofy": ("excited", "excited", "laugh", "goofy"),
    "playful": ("excited", "excited", "laugh", "playful"),
    # Sad / frowning
    "sad": ("sad", "sad", None, "sad"),
    "unhappy": ("sad", "sad", None, "sad"),
    "frown": ("sad", "sad", None, "frown"),
    "frowning": ("sad", "sad", None, "frowning"),
    "crying": ("sad", "sad", None, "sad"),
    "cry": ("sad", "sad", None, "sad"),
    "upset": ("sad", "sad", None, "upset"),
    "gloomy": ("sad", "sad", None, "gloomy"),
    "depressed": ("sad", "sad", None, "gloomy"),
    "disappointed": ("sad", "sad", None, "disappointed"),
    "lonely": ("sad", "sad", None, "lonely"),
    "pout": ("sad", "sad", None, "pouty"),
    "pouting": ("sad", "sad", None, "pouty"),
    "melancholy": ("sad", "sad", None, "melancholy"),
    # Angry / mad / grumpy
    "angry": ("angry", "angry", None, "angry"),
    "mad": ("angry", "angry", None, "mad"),
    "furious": ("angry", "angry", None, "angry"),
    "grumpy": ("angry", "angry", None, "grumpy"),
    "annoyed": ("angry", "angry", None, "annoyed"),
    "frustrated": ("angry", "angry", None, "frustrated"),
    "fierce": ("angry", "angry", None, "fierce"),
    "mean": ("angry", "angry", None, "fierce"),
    "scowl": ("angry", "angry", None, "scowl"),
    "scowling": ("angry", "angry", None, "scowling"),
    "cranky": ("angry", "angry", None, "cranky"),
    # Curious / confused / thinking / skeptical / love
    "curious": ("curious", "curious", None, "curious"),
    "inquisitive": ("curious", "curious", None, "curious"),
    "interested": ("curious", "curious", None, "curious"),
    "intrigued": ("curious", "curious", None, "intrigued"),
    "confused": ("confused", "confused", None, "confused"),
    "puzzled": ("confused", "confused", None, "puzzled"),
    "bewildered": ("confused", "confused", None, "confused"),
    "perplexed": ("confused", "confused", None, "puzzled"),
    "skeptical": ("skeptical", "skeptical", None, "skeptical"),
    "sceptical": ("skeptical", "skeptical", None, "skeptical"),
    "suspicious": ("skeptical", "skeptical", None, "suspicious"),
    "doubtful": ("skeptical", "skeptical", None, "skeptical"),
    "dubious": ("skeptical", "skeptical", None, "skeptical"),
    "love": ("love", "love", None, "loving"),
    "loving": ("love", "love", None, "loving"),
    "lovely": ("love", "love", None, "loving"),
    "heart": ("love", "love", None, "heart-eyes"),
    "hearts": ("love", "love", None, "heart-eyes"),
    "adore": ("love", "love", None, "adoring"),
    "adoring": ("love", "love", None, "adoring"),
    "sweet": ("love", "love", None, "sweet"),
    "affectionate": ("love", "love", None, "affectionate"),
    "thinking": ("thinking", "thinking", None, "thinking"),
    "thoughtful": ("thinking", "thinking", None, "thoughtful"),
    "pondering": ("thinking", "thinking", None, "thinking"),
    # Sleepy / tired vs Asleep (Zzz)
    "sleepy": ("sleepy", "sleepy", None, "sleepy"),
    "tired": ("sleepy", "sleepy", None, "sleepy"),
    "drowsy": ("sleepy", "sleepy", None, "drowsy"),
    "yawning": ("sleepy", "sleepy", None, "sleepy"),
    "exhausted": ("sleepy", "sleepy", None, "tired"),
    "asleep": ("asleep", "asleep", None, "asleep"),
    "sleeping": ("asleep", "asleep", None, "asleep"),
    "zzz": ("asleep", "asleep", None, "asleep"),
    # Bored / focused / pained
    "bored": ("bored", "bored", None, "bored"),
    "unimpressed": ("bored", "bored", None, "unimpressed"),
    "deadpan": ("bored", "bored", None, "deadpan"),
    "blank": ("bored", "bored", None, "blank"),
    "unamused": ("bored", "bored", None, "unamused"),
    "focused": ("focused", "focused", None, "focused"),
    "serious": ("focused", "focused", None, "serious"),
    "determined": ("focused", "focused", None, "determined"),
    "stern": ("focused", "focused", None, "stern"),
    "concentrating": ("focused", "focused", None, "focused"),
    "intense": ("focused", "focused", None, "intense"),
    "pained": ("pained", "pained", None, "pained"),
    "pain": ("pained", "pained", None, "pained"),
    "hurt": ("pained", "pained", None, "pained"),
    "hurting": ("pained", "pained", None, "pained"),
    "wince": ("pained", "pained", None, "wince"),
    "wincing": ("pained", "pained", None, "wincing"),
    "ouch": ("pained", "pained", None, "pained"),
    "dizzy": ("pained", "pained", None, "dizzy"),
    # Neutral / normal / calm
    "neutral": ("neutral", "neutral", None, "neutral"),
    "normal": ("neutral", "neutral", None, "normal"),
    "calm": ("neutral", "neutral", None, "calm"),
    "default": ("neutral", "neutral", None, "normal"),
    "regular": ("neutral", "neutral", None, "normal"),
    "relaxed": ("neutral", "neutral", None, "relaxed"),
}

_EXPR_WORDS_PAT = "|".join(
    sorted((re.escape(w) for w in _ROBOT_FACE_EXPR_MAP), key=len, reverse=True)
)
_ROBOT_FACE_EXPR_PATTERNS = [
    # "show me your worried face", "make a sad face", "show me your asleep face"
    re.compile(
        rf"\b(?:show(?:\s+me)?|make|do|put\s+on|give(?:\s+me)?|try|display)\s+"
        rf"(?:me\s+)?(?:your\s+|a\s+|an\s+|the\s+|some\s+)?"
        rf"(?:big\s+|little\s+|super\s+|very\s+|best\s+|cute\s+|funny\s+|scary\s+)?"
        rf"(?P<emo>{_EXPR_WORDS_PAT})(?:\s+(?:or|and)\s+(?:{_EXPR_WORDS_PAT}))?(?:\s+facial)?\s+(?:face|expression|look|eyes|one)\b",
        re.IGNORECASE,
    ),
    # "let me see your worried face", "what does your happy face look like?"
    re.compile(
        rf"\b(?:let(?:'s|\s+me)\s+see|what\s+does)\s+(?:your\s+|a\s+|an\s+)?"
        rf"(?:big\s+|little\s+|best\s+)?(?P<emo>{_EXPR_WORDS_PAT})(?:\s+(?:or|and)\s+(?:{_EXPR_WORDS_PAT}))?(?:\s+facial)?\s+"
        rf"(?:face|expression|look)(?:\s+look\s+like)?\b",
        re.IGNORECASE,
    ),
    # "change your face to happy", "set your expression to surprised"
    re.compile(
        rf"\b(?:change|set|switch|turn|make)\s+(?:your\s+|the\s+)?(?:face|expression|mood|eyes)\s+"
        rf"(?:to\s+|into\s+)?(?P<emo>{_EXPR_WORDS_PAT})(?:\s+(?:or|and)\s+(?:{_EXPR_WORDS_PAT}))?\b",
        re.IGNORECASE,
    ),
    # "give me a smile", "show me a big grin", "make a frown"
    re.compile(
        r"\b(?:show(?:\s+me)?|make|do|give(?:\s+me)?)\s+(?:me\s+)?(?:your\s+|a\s+|an\s+)?"
        r"(?:big\s+|little\s+|best\s+|cute\s+)?(?P<emo>smile|grin|frown|scowl|pout|wince|laugh|giggle)\b",
        re.IGNORECASE,
    ),
    # "can you smile for me", "smile for me", "frown please"
    re.compile(
        r"^(?:please\s+|can\s+you\s+|could\s+you\s+|will\s+you\s+|now\s+)?"
        r"(?P<emo>smile|grin|frown|scowl|pout)\s+(?:for\s+me|at\s+me|now|please)\s*[?!.]*$",
        re.IGNORECASE,
    ),
    # Imperative / request: "look surprised", "can you look worried?", "act scared", "be happy"
    re.compile(
        rf"^(?:(?:hey|ok|okay|now|please)\s+)*"
        rf"(?:(?:can|could|will|would)\s+you\s+|please\s+|try\s+to\s+|go\s+ahead\s+and\s+)?"
        rf"(?:look|act|be|pretend\s+to\s+be|pretend\s+you(?:'re|\s+are))\s+"
        rf"(?:very\s+|super\s+|really\s+|a\s+little\s+|all\s+)?"
        rf"(?P<emo>{_EXPR_WORDS_PAT})(?:\s+(?:or|and)\s+(?:{_EXPR_WORDS_PAT}))?(?:\s+(?:for\s+me|right\s+now|now|please))?\s*[?!.]*$",
        re.IGNORECASE,
    ),
]


def parse_robot_face_expression(user_text: str) -> dict[str, Any] | None:
    """Detect user requests for Rocky to display a face/expression on his screen."""
    text = (user_text or "").strip()
    if not text:
        return None
    # Guard against questions about the user's own face/expression
    if re.search(r"\b(?:my\s+(?:face|facial\s+expression|expression|mood)|am\s+i\s+|do\s+i\s+look)\b", text, re.I):
        return None
    for pat in _ROBOT_FACE_EXPR_PATTERNS:
        m = pat.search(text)
        if m:
            raw_word = m.group("emo").lower()
            mapped = _ROBOT_FACE_EXPR_MAP.get(raw_word)
            if mapped:
                hud_mood, esp_mood, esp_anim, label = mapped
                return {
                    "word": raw_word,
                    "label": label,
                    "hud_mood": hud_mood,
                    "esp_mood": esp_mood,
                    "esp_anim": esp_anim,
                }
    return None


def _robot_expression_reply(char_id: str, label: str, hud_mood: str) -> str:
    """Warm, in-character 1-2 sentence Rocky reply when displaying an expression on the robot face screen."""
    if hud_mood in ("worried", "alarmed"):
        return f"Watch Rocky face screen, friend! Putting on {label} expression right now, how does Rocky look, question?"
    if hud_mood == "surprised":
        return f"Watch Rocky robot face, friend! Showing big {label} expression right now, how does Rocky look, question?"
    if hud_mood == "happy":
        if "smile" in label or "grin" in label:
            return f"Watch Rocky face screen, friend! Putting {label} on robot face right now, fist my bump!"
        return f"Watch Rocky face screen, friend! Showing big {label} face right now, fist my bump!"
    if hud_mood == "excited":
        return f"Watch Rocky face screen, friend! Showing super {label} face right now, happy happy happy!"
    if hud_mood == "sad":
        return f"Look at Rocky face screen, friend! Showing {label} face right now, good acting, question?"
    if hud_mood == "angry":
        return f"Watch Rocky face screen, friend! Putting on {label} face right now, pretty fierce, question?"
    if hud_mood in ("curious", "thinking"):
        return f"Look at Rocky face screen, friend! Showing {label} face right now, what cool thing we explore next, question?"
    if hud_mood == "confused":
        return f"Look at Rocky face screen, friend! Putting on {label} face with question mark right now, amaze, question?"
    if hud_mood == "skeptical":
        return f"Watch Rocky face screen, friend! Showing {label} face right now, one eyebrow up, good acting, question?"
    if hud_mood == "love":
        return f"Watch Rocky face screen, friend! Showing {label} face with floating hearts right now, happy happy happy!"
    if hud_mood == "sleepy":
        return f"Watch Rocky face screen, friend! Putting on {label} face right now, time for robot nap, question?"
    if hud_mood == "asleep":
        return "Watch Rocky face screen, friend! Closing robot eyes with floating zees right now, cozy robot nap!"
    if hud_mood == "bored":
        return f"Look at Rocky face screen, friend! Showing {label} face right now, still happy to help you!"
    if hud_mood == "focused":
        return f"Watch Rocky face screen, friend! Putting on {label} expression right now, ready for science!"
    if hud_mood == "pained":
        return f"Look at Rocky face screen, friend! Showing {label} face right now, robot circuits totally fine though!"
    return f"Watch Rocky face screen, friend! Setting {label} expression right now, ready to help you!"


def _head_look_reply(direction: str) -> str:
    if direction == "left":
        return "Turning Rocky head left now, friend! Looking over there, what exciting thing should Rocky inspect, question?"
    if direction == "right":
        return "Turning Rocky head right now, friend! Looking over there, what exciting thing should Rocky inspect, question?"
    if direction == "up":
        return "Tilting Rocky head up now, friend! Looking toward ceiling and stars, amaze!"
    if direction == "down":
        return "Tilting Rocky head down now, friend! Checking down below, good good good!"
    return "Centering Rocky head straight ahead now, friend! Ready and looking right at you, happy happy happy!"


def _dance_reply(action: str) -> str:
    if action == "dance_disco":
        return "Disco dance time, friend! Watch Rocky groove moves right now, amaze amaze amaze!"
    if action == "dance_sprinkler":
        return "Sprinkler dance activating, friend! Watch Rocky robot moves right now, happy happy happy!"
    if action == "dance_robot":
        return "Robot dance mode on, friend! Watch Rocky mechanical moves right now, fist my bump!"
    if action == "dance_groove":
        return "Groove mode on, friend! Watch Rocky smooth head moves right now, happy happy happy!"
    return "Happy happy happy, friend! Watch Rocky dance moves right now, amaze amaze amaze!"


_CACHED_ROBOT_IP: str | None = None
_CACHED_ROBOT_IP_TIME: float = 0.0
_IP_CACHE_TTL: float = 15.0  # Trust cached IP for 15s to eliminate redundant HTTP handshake latency


def set_cached_robot_ip(ip: str | None) -> None:
    global _CACHED_ROBOT_IP, _CACHED_ROBOT_IP_TIME
    cleaned = (ip or "").strip()
    if cleaned:
        _CACHED_ROBOT_IP = cleaned
        _CACHED_ROBOT_IP_TIME = time.time()
    else:
        _CACHED_ROBOT_IP = None
        _CACHED_ROBOT_IP_TIME = 0.0


def _get_robot_ip_fast() -> str | None:
    """Returns cached/configured ESP32 IP immediately without blocking on a subnet scan."""
    if _CACHED_ROBOT_IP:
        return _CACHED_ROBOT_IP
    env_ip = os.environ.get("ROBOT_HOST")
    if env_ip:
        return env_ip.strip()
    for fname, key in (("settings.json", "robot_ip"), ("camera_config.json", "esp32_ip")):
        try:
            cfg_path = os.path.join(PROJECT_DIR, fname)
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    saved_ip = json.load(f).get(key)
                    if saved_ip:
                        return str(saved_ip).strip()
        except Exception:
            pass
    return None


def push_robot_face_mood(mood: str, anim: str | None = None) -> None:
    """Non-blocking short-timeout push of requested mood directly to ESP32 /face."""
    payload: dict[str, Any] = {"mood": mood}
    if anim:
        payload["anim"] = anim

    def _worker() -> None:
        ip = _get_robot_ip_fast() or resolve_robot_ip()
        try:
            with httpx.Client(timeout=httpx.Timeout(connect=0.6, read=1.2, write=0.6, pool=0.6)) as client:
                client.post(f"http://{ip}/face", json=payload)
            _log_event("FACE", f"Dispatched mood='{mood}'{f' anim={anim}' if anim else ''} to {ip}")
        except Exception as exc:
            _log_event("FACE", f"ESP32 ({ip}) unreachable for mood '{mood}': {exc}")

    threading.Thread(target=_worker, daemon=True, name="robot_face_push").start()


def push_robot_action(action: str) -> None:
    """Non-blocking short-timeout dispatch to ESP32 POST /action."""
    payload = {"action": action}

    def _worker() -> None:
        ip = _get_robot_ip_fast() or resolve_robot_ip()
        try:
            with httpx.Client(timeout=httpx.Timeout(connect=0.6, read=1.5, write=0.6, pool=0.6)) as client:
                client.post(f"http://{ip}/action", json=payload)
            _log_event("DANCE", f"Dispatched action='{action}' to {ip}")
        except Exception as exc:
            _log_event("DANCE", f"ESP32 ({ip}) unreachable for action '{action}': {exc}")

    threading.Thread(target=_worker, daemon=True, name="robot_action_push").start()


def push_robot_servo(
    pan: float,
    tilt: float,
    speed: int = 180,
    blocking: bool = False,
) -> None:
    """Dispatch head pan/tilt to ESP32 POST /servo and keep FACE_TRACKER synced."""
    pan_c = round(max(15.0, min(165.0, float(pan))), 1)
    tilt_c = round(max(58.0, min(132.0, float(tilt))), 1)
    spd = max(30, min(600, int(speed)))
    if hasattr(FACE_TRACKER, "set_manual_pose"):
        try:
            FACE_TRACKER.set_manual_pose(pan_c, tilt_c)
        except Exception:
            pass
    payload = {"pan": pan_c, "tilt": tilt_c, "speed": spd}

    def _send() -> None:
        ip = _get_robot_ip_fast() or resolve_robot_ip()
        try:
            with httpx.Client(timeout=httpx.Timeout(connect=0.6, read=1.2, write=0.6, pool=0.6)) as client:
                client.post(f"http://{ip}/servo", json=payload)
            _log_event("SERVO", f"Moved head pan={pan_c}° tilt={tilt_c}° (speed={spd}) on {ip}")
        except Exception as exc:
            _log_event("SERVO", f"ESP32 ({ip}) unreachable for servo move: {exc}")

    if blocking:
        _send()
    else:
        threading.Thread(target=_send, daemon=True, name="robot_servo_push").start()


def _probe_ip(ip: str) -> bool:
    try:
        with httpx.Client(timeout=0.35) as c:
            r = c.get(f"http://{ip}/status")
            return r.status_code == 200 and ("robot" in r.text or "Rocky" in r.text)
    except Exception:
        return False


def invalidate_robot_ip() -> None:
    global _CACHED_ROBOT_IP, _CACHED_ROBOT_IP_TIME
    _CACHED_ROBOT_IP = None
    _CACHED_ROBOT_IP_TIME = 0.0


def resolve_robot_ip(force_probe: bool = False) -> str:
    global _CACHED_ROBOT_IP, _CACHED_ROBOT_IP_TIME
    now = time.time()
    if _CACHED_ROBOT_IP:
        if not force_probe and (now - _CACHED_ROBOT_IP_TIME) < _IP_CACHE_TTL:
            return _CACHED_ROBOT_IP
        if _probe_ip(_CACHED_ROBOT_IP):
            _CACHED_ROBOT_IP_TIME = now
            return _CACHED_ROBOT_IP

    env_ip = os.environ.get("ROBOT_HOST")
    if env_ip and _probe_ip(env_ip):
        _CACHED_ROBOT_IP = env_ip
        _CACHED_ROBOT_IP_TIME = now
        return env_ip

    # Check saved settings / camera config
    for fname, key in (("settings.json", "robot_ip"), ("camera_config.json", "esp32_ip")):
        try:
            cfg_path = os.path.join(PROJECT_DIR, fname)
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    saved_ip = json.load(f).get(key)
                    if saved_ip and _probe_ip(str(saved_ip)):
                        _CACHED_ROBOT_IP = str(saved_ip)
                        _CACHED_ROBOT_IP_TIME = now
                        return _CACHED_ROBOT_IP
        except Exception:
            pass

    # High-probability DHCP / Static candidates
    candidates = [
        "192.168.4.40", "192.168.4.46", "192.168.4.39", "192.168.4.173",
        "192.168.4.147", "192.168.4.136", "192.168.4.100", "192.168.4.150",
    ]
    for c in candidates:
        if _probe_ip(c):
            _CACHED_ROBOT_IP = c
            _CACHED_ROBOT_IP_TIME = now
            return c

    # Check ARP table for Espressif MAC OUI
    try:
        out = subprocess.check_output(["arp", "-a"], text=True)
        for line in out.splitlines():
            if any(oui in line.lower() for oui in ["84:c7:bb", "34:85:18", "24:4c:ab", "ec:da:3b", "40:22:d8", "a0:76:4e", "30:ae:a4"]):
                m = re.search(r"\((192\.168\.\d+\.\d+)\)", line)
                if m and _probe_ip(m.group(1)):
                    _CACHED_ROBOT_IP = m.group(1)
                    _CACHED_ROBOT_IP_TIME = now
                    return _CACHED_ROBOT_IP
    except Exception:
        pass

    # Concurrent subnet scan of common DHCP range (100 to 205)
    import concurrent.futures

    def check_candidate(host: str) -> str | None:
        return host if _probe_ip(host) else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as ex:
        probe_hosts = [f"192.168.4.{i}" for i in range(100, 205)]
        for result in ex.map(check_candidate, probe_hosts):
            if result:
                _CACHED_ROBOT_IP = result
                _CACHED_ROBOT_IP_TIME = now
                try:
                    cfg_path = os.path.join(PROJECT_DIR, "camera_config.json")
                    cfg = {}
                    if os.path.exists(cfg_path):
                        with open(cfg_path, "r", encoding="utf-8") as f:
                            cfg = json.load(f)
                    cfg["esp32_ip"] = result
                    with open(cfg_path, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, indent=2)
                except Exception:
                    pass
                return result

    return _CACHED_ROBOT_IP or "192.168.4.40"


_STOP_SEQUENCES = [
    "\nUser:",
    "User:",
    "\nQuestion:",
    "Question:",
    "\nAnswer:",
    "Answer:",
    "\nRocky:",
    "Rocky:",
    "\nGrace:",
    "Grace:",
    "\nHuman:",
    "Human:",
    "\nAssistant:",
    "Assistant:",
]


def _active_camera_label(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg if cfg is not None else robot_vision.load_camera_config()
    source_type = cfg.get("source_type", "esp32")
    return (
        "MacBook camera"
        if source_type in ("macbook", "webcam", "local")
        else "ESP32 camera"
    )


def _encode_frame_jpeg_b64(frame: Any, quality: int = 85) -> str | None:
    import cv2

    success, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not success:
        return None
    return base64.b64encode(enc.tobytes()).decode("utf-8")


def _extract_known_names(frame: Any) -> list[str]:
    if frame is None:
        return []
    try:
        faces = robot_vision.recognize_faces(frame)
        names: list[str] = []
        for f in faces:
            name = f.get("name")
            if name and name != "Unknown" and name not in names:
                names.append(name)
        return names
    except Exception:
        return []


_CHILD_VQA_RE = re.compile(
    r"\b(?:son|daughter|kid|child|children|baby|boy|girl)\b",
    re.IGNORECASE,
)
_PERSON_VQA_RE = re.compile(
    r"\b(?:wife|husband|who|anyone|anybody|someone|somebody|people|person|man|woman|friend|he|she|him|her)\b",
    re.IGNORECASE,
)
_FINGER_VQA_RE = re.compile(
    r"\b(?:fingers?|holding\s+up)\b",
    re.IGNORECASE,
)


def build_vision_system_prompt(
    char_id: str,
    cam_label: str,
    known_names: list[str] | None = None,
    user_query: str = "",
    is_reading: bool = False,
) -> str:
    """Dedicated 1-hop grounded vision prompt for Rocky with zero stale history."""
    q = user_query or ""
    if is_reading:
        prompt = (
            "You are Rocky from Project Hail Mary, looking live through your robot eyes to read text for David.\n"
            "TEXT READING MODE:\n"
            "1. Read EVERY visible line of text from top to bottom completely and verbatim, including the bottom lines.\n"
            "2. Do NOT stop after two lines, do NOT truncate with '...', and do NOT skip lines.\n"
            "3. Plain spoken text only (no markdown or asterisks). If any line is too blurry, cut off, or out of frame to read, say so honestly instead of guessing."
        )
        if known_names:
            prompt += f"\nFace recognition match in current frame: {', '.join(known_names)}."
        return prompt

    extra_rules: list[str] = []
    if _FINGER_VQA_RE.search(q):
        extra_rules.append(
            "- Finger check: ONLY state a number if a raised hand with clearly countable extended fingers is visible in frame. "
            "If no raised hand or countable fingers are clearly visible, say no raised fingers are visible right now and NEVER guess a number."
        )
    if _CHILD_VQA_RE.search(q):
        extra_rules.append(
            "- Child check: son/daughter/kid means a CHILD (young boy or girl), NOT an adult. "
            "If a child is clearly visible in frame, start with Yes and describe the child briefly "
            "(do not deny seeing them just because their name is not written). "
            "If NO child is visible (only an adult or empty room), start with No and state only what is actually visible (never guess where an unseen child is or say probably)."
        )
    elif _PERSON_VQA_RE.search(q):
        extra_rules.append(
            "- Person check: if a person matching the question is visible in frame, start with Yes and describe them briefly. "
            "If NO matching person is visible, start with No and state only what is actually visible (never guess where an unseen person is or say probably)."
        )

    extra_block = ("\n" + "\n".join(extra_rules)) if extra_rules else ""

    prompt = (
        "You are Rocky from Project Hail Mary, a warm, joyful, curious Eridian robot on Earth looking live through your robot eyes.\n"
        "Answer the user's exact question directly and truthfully using ONLY clearly visible facts in the image.\n"
        "RULES:\n"
        "1. Strictly 1 or 2 SHORT spoken sentences (under 22 words total). Plain speech only, no markdown or emojis.\n"
        "2. Sentence 1: Directly state what is clearly visible (actions like drinking, waving, holding up an object). For small/unfamiliar objects, describe shape, color, and material honestly instead of guessing.\n"
        "3. Sentence 2: Brief warm Eridian remark (Amaze!, Good good good!, etc.). Always call your camera 'my eyes' — NEVER say 'ESP32' or 'camera device'. Never say you are blind."
        f"{extra_block}"
    )

    if known_names:
        prompt += f"\nFace recognition match in current frame: {', '.join(known_names)}."
    rules_block = memory.get_rules_prompt_block()
    if rules_block:
        prompt += f"\n\n{rules_block}"
    return prompt


def _camera_unavailable_reply(char_id: str, cam_label: str) -> str:
    """Deterministic in-character Rocky reply when camera capture fails — zero LLM hallucination."""
    return "Rocky eyes timed out right now, friend, so I cannot see frame clearly. Check connection, question?"


def fetch_robot_vision(
    user_query: str | None = None,
    timeout: float = 35.0,
    frame: Any = None,
    image_bytes: bytes | None = None,
    model: str | None = None,
) -> str | None:
    """Grabs a live frame (or uses provided frame/bytes) and runs focused VQA on the local VLM."""
    try:
        img_b64: str | None = None
        if image_bytes is not None:
            img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        else:
            if frame is None:
                frame = robot_vision.fetch_live_camera_frame()
            if frame is None:
                return None
            img_b64 = _encode_frame_jpeg_b64(frame, quality=85)
        if not img_b64:
            return None

        v_model = model or VISION_MODEL
        question = (
            user_query.strip()
            if user_query and user_query.strip()
            else "Briefly describe what is visibly in front of the camera: people, hands, objects, and colors."
        )
        payload: dict[str, Any] = {
            "model": v_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an accurate visual observer. Answer the exact question in 1-2 short factual sentences "
                        "using ONLY what is clearly visible in the image. Never guess or invent unseen people, fingers, or objects."
                    ),
                },
                {
                    "role": "user",
                    "content": question,
                    "images": [img_b64],
                },
            ],
            "stream": False,
            "keep_alive": "30m",
            "options": {
                "temperature": 0.1,
                "top_p": 0.9,
                "num_ctx": OLLAMA_NUM_CTX,
                "num_predict": 65,
                "num_thread": 4,
            },
        }
        if v_model.lower().startswith("qwen3.5"):
            payload["think"] = False

        with httpx.Client(timeout=timeout) as client:
            r = client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            if r.status_code == 200:
                txt = r.json().get("message", {}).get("content", "").strip()
                if txt:
                    return txt
    except Exception as exc:
        _log_event("VISION", f"Vision VQA error: {exc}")
    return None


SENTENCE_SPLIT_RE = re.compile(r"(?<!\bMr)(?<!\bDr)(?<!\bMs)(?<!\bSt)(?<!\b[A-Z])([.!?]+)\s+")
CLAUSE_SPLIT_RE = re.compile(r"([,;:\u2014]+)\s+")
DATE_OR_TAG_AFTER_RE = re.compile(
    r"^(?:January|February|March|April|May|June|July|August|September|October|November|December|\d{4}\b|question\b|grace\b|friend\b)",
    re.IGNORECASE,
)
FAREWELL_USER_RE = re.compile(
    r"\b(?:bye|goodbye|see\s+ya|see\s+you\s+later|good\s*night|talk\s+to\s+you\s+later|catch\s+you\s+later|gotta\s+(?:go|run))\b",
    re.IGNORECASE,
)


def _pop_next_chunk(
    buffer: str,
    idx: int,
    allow_clause_split: bool = True,
    split_newlines: bool = False,
) -> tuple[str, str] | None:
    if split_newlines:
        nl = buffer.find("\n")
        if nl >= 0:
            cand = buffer[:nl].strip()
            rest = buffer[nl + 1 :].lstrip("\n ")
            if len(cand) >= 4:
                if cand[-1] not in ".!?,;:":
                    cand += "."
                return cand, rest

    min_sent = 3 if idx == 0 else 14
    for m in SENTENCE_SPLIT_RE.finditer(buffer):
        cand = buffer[: m.end()].strip()
        if len(cand) >= min_sent:
            return cand, buffer[m.end() :]

    if allow_clause_split:
        min_clause = 18 if split_newlines else (10 if idx == 0 else 42)
        for m in CLAUSE_SPLIT_RE.finditer(buffer):
            cand = buffer[: m.end()].strip()
            after = buffer[m.end() :].strip()
            if len(cand) >= min_clause and len(after) >= 8 and not DATE_OR_TAG_AFTER_RE.match(after):
                if split_newlines and cand.endswith(","):
                    cand = cand[:-1] + "."
                return cand, buffer[m.end() :]

    return None


class RockyBrain:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        vision_model: str = VISION_MODEL,
        max_turns: int = 4,
    ) -> None:
        self.model = model
        self.vision_model = vision_model
        self.max_turns = max_turns
        self.histories: dict[str, list[dict[str, str]]] = {}
        self._last_turn_was_vision: dict[str, bool] = {}
        self._last_vision_query: dict[str, str] = {}
        self._last_vision_ts: dict[str, float] = {}
        # Pre-seed Rocky's working memory from persistent SQLite store (ignoring duplicate loops)
        for cid in REGISTRY.ids():
            raw_prior = memory.load_recent_dialogue(cid, limit=max_turns * 2)
            prior: list[dict[str, str]] = []
            for item in raw_prior:
                if (
                    prior
                    and item.get("role") == "assistant"
                    and prior[-1].get("role") == "assistant"
                    and item.get("content") == prior[-1].get("content")
                ):
                    continue
                prior.append(item)
            if prior:
                self.histories[cid] = prior
        self._client = httpx.Client(timeout=httpx.Timeout(45.0, connect=6.0))
        self._ollama_proc: subprocess.Popen[Any] | None = None

    def ensure_ollama_running(self) -> bool:
        if self._ping():
            return True

        bin_path = OLLAMA_BIN if os.path.exists(OLLAMA_BIN) else "ollama"
        env = os.environ.copy()
        env["OLLAMA_MAX_LOADED_MODELS"] = "2"
        env["OLLAMA_KEEP_ALIVE"] = "30m"
        log_file = open(os.path.join(PROJECT_DIR, "ollama.log"), "a")
        self._ollama_proc = subprocess.Popen(
            [bin_path, "serve"],
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        for _ in range(25):
            time.sleep(0.4)
            if self._ping():
                return True
        return False

    def _ping(self) -> bool:
        try:
            r = self._client.get(f"{OLLAMA_URL}/api/tags", timeout=1.5)
            return r.status_code == 200
        except Exception:
            return False

    def _preload_vision_model(self) -> None:
        try:
            with httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0)) as c:
                c.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={
                        "model": self.vision_model,
                        "prompt": "",
                        "stream": False,
                        "keep_alive": "30m",
                        "options": {"num_ctx": OLLAMA_NUM_CTX, "num_thread": 4},
                    },
                )
        except Exception as exc:
            _log_event("BRAIN", f"Vision preload warning: {exc}")

    def warmup(self) -> None:
        self.ensure_ollama_running()
        spec = REGISTRY.get("rocky")
        try:
            self._client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": spec.system_prompt},
                        {"role": "user", "content": "Hi"},
                    ],
                    "stream": False,
                    "keep_alive": "60m",
                    "options": {"num_ctx": OLLAMA_NUM_CTX, "num_predict": 2, "num_thread": 4},
                },
                timeout=30.0,
            )
        except Exception as exc:
            _log_event("BRAIN", f"Warmup warning: {exc}")
        threading.Thread(target=self._preload_vision_model, daemon=True, name="vision_preload").start()

    def reset_history(self, character: str | None = None) -> None:
        if character and character.lower() in self.histories:
            cid = character.lower()
            self.histories[cid].clear()
            self._last_turn_was_vision[cid] = False
            self._last_vision_query.pop(cid, None)
            self._last_vision_ts.pop(cid, None)
            memory.clear_dialogue(cid)
        else:
            for h in self.histories.values():
                h.clear()
            self._last_turn_was_vision.clear()
            self._last_vision_query.clear()
            self._last_vision_ts.clear()
            memory.clear_dialogue()

    def classify_intent(self, user_text: str, char_id: str = "rocky") -> str:
        """Deterministically routes user text without regex collisions."""
        text = user_text.strip()
        if CAMERA_SWITCH_RE.search(text):
            return "camera_switch"
        track_intent = parse_tracking_intent(text)
        if track_intent is True:
            return "track_start"
        if track_intent is False:
            return "track_stop"
        if MOTION_GUARD_RE.search(text):
            return "motion_guard"
        if FACE_ENROLL_QUERY_RE.search(text):
            return "face_enroll"
        if parse_teach_rule_intent(text):
            return "teach_rule"
        if parse_dance_intent(text):
            return "dance"
        head_look = parse_head_look_intent(text)
        if head_look:
            return "head_look_vision" if head_look["wants_vision"] else "head_look"
        if parse_robot_face_expression(text):
            return "robot_face_expr"
        if VISION_QUERY_RE.search(text):
            return "vision"
        last_vision_ts = getattr(self, "_last_vision_ts", {})
        recent_vision = self._last_turn_was_vision.get(char_id, False) or (
            (time.time() - last_vision_ts.get(char_id, 0.0)) < 60.0
        )
        if recent_vision and VISION_FOLLOWUP_RE.search(text):
            return "vision_followup"
        if TIME_QUERY_RE.search(text):
            return "time"
        if FACE_EXPR_QUERY_RE.search(text):
            return "face_expr"
        if FACE_RECOG_QUERY_RE.search(text):
            return "face_recog"
        return "chat"

    def _build_vision_turn(
        self,
        user_text: str,
        char_id: str,
        is_followup: bool = False,
        frame: Any = None,
        image_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        """Builds a 1-hop direct VLM chat turn with zero stale conversation history."""
        cfg = robot_vision.load_camera_config()
        cam_label = _active_camera_label(cfg)
        _log_event("VISION", f"1-hop VLM ({self.vision_model}) query via {cam_label}: {user_text.strip()[:80]}")

        img_b64: str | None = None
        known_names: list[str] = []

        is_reading = bool(
            _READ_TEXT_VQA_RE.search(user_text or "")
            or (is_followup and _READ_TEXT_VQA_RE.search(self._last_vision_query.get(char_id, "")))
        )
        jpeg_quality = 95 if is_reading else 85

        if image_bytes is not None:
            img_b64 = base64.b64encode(image_bytes).decode("utf-8")
            try:
                import cv2
                import numpy as np
                decoded = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
                known_names = _extract_known_names(decoded)
            except Exception:
                known_names = []
        else:
            if frame is None:
                frame = robot_vision.fetch_live_camera_frame()
            if frame is not None:
                known_names = _extract_known_names(frame)
                img_b64 = _encode_frame_jpeg_b64(frame, quality=jpeg_quality)

        if is_followup and self._last_vision_query.get(char_id):
            prev_q = self._last_vision_query[char_id]
            vlm_query = f"{prev_q} (Look again at this new live frame; user asked: {user_text.strip()})"
            effective_query = prev_q
        else:
            vlm_query = user_text.strip()
            effective_query = vlm_query

        if img_b64:
            sys_prompt = build_vision_system_prompt(
                char_id,
                cam_label,
                known_names=known_names,
                user_query=vlm_query,
                is_reading=is_reading,
            )
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": vlm_query, "images": [img_b64]},
            ]
            extra_body: dict[str, Any] = {}
            if self.vision_model.lower().startswith("qwen3.5"):
                extra_body["think"] = False
            return {
                "model": self.vision_model,
                "messages": messages,
                "keep_alive": "30m",
                "options": {
                    "temperature": 0.1 if is_reading else 0.15,
                    "top_p": 0.9,
                    "repeat_penalty": 1.08 if is_reading else 1.15,
                    "presence_penalty": 0.1 if is_reading else 0.35,
                    "num_ctx": OLLAMA_NUM_CTX,
                    "num_predict": 180 if is_reading else 65,
                    "num_thread": 4,
                    "stop": _STOP_SEQUENCES,
                },
                "extra_body": extra_body,
                "is_vision": True,
                "is_reading": is_reading,
                "max_sentences": 8 if is_reading else 2,
                "has_frame": True,
                "effective_query": effective_query,
            }

        # Camera frame temporarily unavailable: deterministic reply (never invoke LLM to hallucinate)
        _log_event("VISION", f"Camera frame unavailable on {cam_label}")
        return {
            "model": self.model,
            "messages": [],
            "keep_alive": "60m",
            "options": {},
            "extra_body": {},
            "direct_reply": _camera_unavailable_reply(char_id, cam_label),
            "is_vision": True,
            "has_frame": False,
            "effective_query": effective_query,
        }

    def _build_messages(
        self,
        user_text: str,
        char_id: str,
        hass_action: str | None = None,
        mac_action: str | None = None,
        intent: str | None = None,
        prechecked_actions: bool = False,
    ) -> list[dict[str, str]]:
        spec = REGISTRY.get(char_id)
        hist = self.histories.setdefault(spec.id, [])

        if not prechecked_actions:
            memory.extract_and_learn(user_text)
            robot_vision.mark_conversation_active()
        mem_block = memory.build_memory_prompt_block(user_text)

        cfg = robot_vision.load_camera_config()
        cam_label = _active_camera_label(cfg)
        vis_ctx = robot_vision.get_current_visual_context(max_age=3.0)
        sys_content = spec.system_prompt
        now_ctx = datetime.now()
        sys_content += (
            f"\n\nLive system clock: {now_ctx.strftime('%A, %B')} {now_ctx.day}, {now_ctx.year}, "
            f"{now_ctx.strftime('%I:%M %p').lstrip('0')}."
        )
        if mem_block:
            sys_content += f"\n\n{mem_block}"

        if vis_ctx.get("in_view"):
            name = vis_ctx.get("name")
            expr_desc = vis_ctx.get("expression_desc") or "looking attentive"
            expr_label = vis_ctx.get("expression") or "neutral"

            if name and name != "Unknown":
                sys_content += (
                    f"\n\n[Live Sight — {cam_label}: You see your friend {name} "
                    f"({expr_label}, {expr_desc}). Never say you are blind or lack vision.]"
                )
            else:
                sys_content += (
                    f"\n\n[Live Sight — {cam_label}: You see someone in front of you "
                    f"({expr_label}, {expr_desc}; face not enrolled). Never say you are blind or lack vision.]"
                )
        else:
            sys_content += (
                f"\n\n[Live Sight — {cam_label}: Optical sight is active; never say you are blind or lack vision.]"
            )

        messages: list[dict[str, str]] = [{"role": "system", "content": sys_content}]
        messages.extend(hist[-(self.max_turns * 2) :])

        if not prechecked_actions:
            try:
                hass_action = detect_and_execute_hass_intent(user_text)
            except Exception as exc:
                _log_event("HASS", f"Intent error: {exc}")
                hass_action = None
            mac_action = detect_and_execute_mac_control(user_text) if not hass_action else None

        if intent is None:
            intent = self.classify_intent(user_text, char_id)

        if hass_action:
            user_payload = (
                f"(Smart Home Action Executed in Home Assistant: \"{hass_action}\". "
                f"Directly confirm this action to friend in your authentic 1-2 sentence Rocky persona.)\n{user_text}"
            )
        elif mac_action:
            user_payload = (
                f"(System Action Executed via macOS MCP server: \"{mac_action}\". "
                f"Directly confirm this action to friend in your authentic 1-2 sentence Rocky persona.)\n{user_text}"
            )
        elif intent == "camera_switch":
            m_cam = CAMERA_SWITCH_RE.search(user_text)
            target = m_cam.group(1).lower() if m_cam else "esp32"
            if any(k in target for k in ("macbook", "laptop", "built", "webcam")):
                robot_vision.save_camera_config({"source_type": "macbook"})
                action_desc = "Camera feed successfully switched to MacBook Pro FaceTime HD camera. Looking through your laptop's camera now."
            else:
                robot_vision.save_camera_config({"source_type": "esp32"})
                action_desc = "Camera feed successfully switched to ESP32 robot camera. Looking through the mobile robot's chassis camera now."
            user_payload = (
                f"(Vision System Action Executed: \"{action_desc}\". "
                f"Directly confirm this camera switch to friend in your authentic 1-2 sentence Rocky persona.)\n{user_text}"
            )
        elif intent == "motion_guard":
            m_guard = MOTION_GUARD_RE.search(user_text)
            enable = bool(m_guard.group(1)) if m_guard else True
            robot_vision.save_camera_config({"motion_guard_enabled": enable})
            action_desc = "Motion Guard sentry mode activated. Watching room for movement." if enable else "Motion Guard sentry mode deactivated."
            user_payload = (
                f"(Vision System Action Executed: \"{action_desc}\". "
                f"Directly confirm this action to friend in your authentic 1-2 sentence Rocky persona.)\n{user_text}"
            )
        elif intent == "face_enroll":
            m_name = re.search(r"\b(?:name\s+is|i\s+am|i'm|call\s+me)\s+([A-Za-z]+)\b", user_text, re.I)
            user_name = m_name.group(1).strip().title() if m_name else ""
            if not user_name:
                facts = memory.get_facts()
                for f in facts:
                    if f.get("key") == "user_name":
                        user_name = f.get("value")
                        break
            if not user_name:
                user_name = "David"

            frame = robot_vision.fetch_live_camera_frame()
            if frame is not None:
                ok, msg = robot_vision.enroll_face(user_name, frame)
                if ok:
                    memory.set_fact("user_name", user_name, category="identity")
                    action_desc = f"Successfully enrolled and remembered {user_name}'s face in biometric memory."
                else:
                    action_desc = f"Could not register face: {msg}"
            else:
                action_desc = "Camera feed was unreachable. Ensure robot camera is powered and connected."
            user_payload = (
                f"(Vision System Action Executed: \"{action_desc}\". "
                f"Directly confirm this action to friend in your authentic 1-2 sentence Rocky persona.)\n{user_text}"
            )
        elif intent == "time":
            now = datetime.now()
            short_time = now.strftime("%I:%M %p").lstrip("0")
            date_str = now.strftime("%A, %B %d, %Y")
            user_payload = (
                f"(Live clock: {short_time}; date: {date_str}. Keep your 2-sentence Rocky format; "
                f"only read out the full date if the user asked for the date.)\n{user_text}"
            )
        elif intent == "face_expr":
            vis = robot_vision.get_current_visual_context(max_age=1.5)
            if vis.get("in_view"):
                name = vis.get("name") if vis.get("name") != "Unknown" else "friend"
                expr_desc = vis.get("expression_desc") or "looking attentive"
                expr_label = vis.get("expression") or "neutral"
                user_payload = (
                    f"(User asks about their facial expression. Through your active camera eyes, you clearly see {name} "
                    f"and their facial expression is {expr_label} ({expr_desc}). Directly and warmly tell {name} what facial expression "
                    f"you see them making in your authentic 1-2 sentence Rocky persona. Never claim to be blind.)\n{user_text}"
                )
            else:
                user_payload = (
                    f"(User asks about their expression. Through your camera eyes, no human face is centered in view right now. "
                    f"Politely tell them in your Rocky persona to look into your camera.)\n{user_text}"
                )
        elif intent == "face_recog":
            vis = robot_vision.get_current_visual_context(max_age=1.5)
            if vis.get("in_view") and vis.get("name") and vis.get("name") != "Unknown":
                name = vis["name"]
                expr_desc = vis.get("expression_desc") or "looking attentive"
                user_payload = (
                    f"(User asks who they are or if you recognize them. Looking through your active camera eyes, you clearly recognize them as {name} "
                    f"and notice they are {expr_desc}. Directly and warmly confirm you recognize them as your friend {name} in your authentic 1-2 sentence Rocky persona. Never claim to be blind.)\n{user_text}"
                )
            elif vis.get("in_view") and vis.get("name") == "Unknown":
                expr_desc = vis.get("expression_desc") or "looking attentive"
                user_payload = (
                    f"(User asks who they are or if you recognize them. Looking through your camera eyes, you see someone in front of you ({expr_desc}), but you don't recognize their face yet. "
                    f"Tell them politely in your Rocky persona, and let them know you can remember them if they say 'Remember my face'.)\n{user_text}"
                )
            else:
                user_payload = (
                    f"(User asks who they are. Looking through your camera eyes, no face is currently in view. "
                    f"Tell them in your Rocky persona that you don't see a face right now.)\n{user_text}"
                )
        elif intent in ("vision", "vision_followup"):
            v_desc = fetch_robot_vision(user_query=user_text)
            if v_desc:
                memory.remember_vision(v_desc, character=char_id)
                user_payload = (
                    f"(Live camera observation for the user's question: {v_desc}. "
                    f"Answer the user's exact question in 1-2 short spoken sentences using only this observation.)\n{user_text}"
                )
            else:
                user_payload = (
                    f"(Live camera frame momentarily unavailable. Tell David briefly without inventing details.)\n{user_text}"
                )
        else:
            user_payload = user_text

        messages.append({"role": "user", "content": user_payload})
        return messages

    def _prepare_turn(self, user_text: str, char_id: str) -> dict[str, Any]:
        """Prepares model, messages, and options; routes deterministic physical & visual intents."""
        memory.extract_and_learn(user_text)
        robot_vision.mark_conversation_active()

        hass_action = None
        try:
            hass_action = detect_and_execute_hass_intent(user_text)
        except Exception as exc:
            _log_event("HASS", f"Intent error: {exc}")
        mac_action = detect_and_execute_mac_control(user_text) if not hass_action else None

        intent = self.classify_intent(user_text, char_id)

        if not hass_action and not mac_action:
            if intent == "teach_rule":
                rule_text = parse_teach_rule_intent(user_text)
                if rule_text:
                    saved = memory.add_rule(rule_text)
                    _log_event("RULE", f"Learned spoken rule #{saved.get('id')}: {rule_text}")
                    push_robot_face_mood("happy")
                    return {
                        "model": self.model,
                        "messages": [],
                        "keep_alive": "60m",
                        "options": {},
                        "extra_body": {},
                        "direct_reply": f"Got it, friend! Saved new rule: {rule_text}. Rocky will follow this from now on, fist my bump!",
                        "forced_emotion": "happy",
                        "esp_mood": "happy",
                        "hold_emotion_ms": 4000,
                        "is_vision": False,
                        "has_frame": False,
                        "effective_query": user_text.strip(),
                    }

            if intent == "dance":
                dance_action = parse_dance_intent(user_text) or "dance"
                push_robot_action(dance_action)
                push_robot_face_mood("excited", anim="laugh")
                return {
                    "model": self.model,
                    "messages": [],
                    "keep_alive": "60m",
                    "options": {},
                    "extra_body": {},
                    "direct_reply": _dance_reply(dance_action),
                    "forced_emotion": "excited",
                    "esp_mood": "excited",
                    "esp_anim": "laugh",
                    "hold_emotion_ms": 6000,
                    "is_vision": False,
                    "has_frame": False,
                    "effective_query": user_text.strip(),
                }

            if intent == "head_look":
                look = parse_head_look_intent(user_text)
                if look:
                    push_robot_servo(look["pan"], look["tilt"], speed=180)
                    mood = "neutral" if look["direction"] == "center" else "curious"
                    push_robot_face_mood(mood)
                    return {
                        "model": self.model,
                        "messages": [],
                        "keep_alive": "60m",
                        "options": {},
                        "extra_body": {},
                        "direct_reply": _head_look_reply(look["direction"]),
                        "forced_emotion": mood,
                        "esp_mood": mood,
                        "hold_emotion_ms": 4000,
                        "is_vision": False,
                        "has_frame": False,
                        "effective_query": user_text.strip(),
                    }

            if intent == "head_look_vision":
                look = parse_head_look_intent(user_text)
                if look:
                    push_robot_servo(look["pan"], look["tilt"], speed=220, blocking=True)
                    time.sleep(0.45)
                return self._build_vision_turn(
                    user_text,
                    char_id,
                    is_followup=False,
                )

            if intent == "track_start":
                ip = _get_robot_ip_fast() or resolve_robot_ip()
                FACE_TRACKER.start(ip)
                _log_event("TRACK", f"Face tracking started via voice on {ip}")
                push_robot_face_mood("curious")
                return {
                    "model": self.model,
                    "messages": [],
                    "keep_alive": "60m",
                    "options": {},
                    "extra_body": {},
                    "direct_reply": "Yes yes yes, friend! Rocky eyes locked on you now, moving head to follow your face!",
                    "forced_emotion": "curious",
                    "esp_mood": "curious",
                    "hold_emotion_ms": 4000,
                    "is_vision": False,
                    "has_frame": False,
                    "effective_query": user_text.strip(),
                }

            if intent == "track_stop":
                FACE_TRACKER.stop()
                _log_event("TRACK", "Face tracking stopped via voice")
                push_robot_face_mood("neutral")
                return {
                    "model": self.model,
                    "messages": [],
                    "keep_alive": "60m",
                    "options": {},
                    "extra_body": {},
                    "direct_reply": "Okay friend, stopping face tracking now! Rocky head resting still, call anytime you want follow again!",
                    "forced_emotion": "neutral",
                    "esp_mood": "neutral",
                    "hold_emotion_ms": 3000,
                    "is_vision": False,
                    "has_frame": False,
                    "effective_query": user_text.strip(),
                }

            if intent == "robot_face_expr":
                expr = parse_robot_face_expression(user_text)
                if expr:
                    push_robot_face_mood(expr["esp_mood"], anim=expr["esp_anim"])
                    return {
                        "model": self.model,
                        "messages": [],
                        "keep_alive": "60m",
                        "options": {},
                        "extra_body": {},
                        "direct_reply": _robot_expression_reply(
                            char_id, expr["label"], expr["hud_mood"]
                        ),
                        "forced_emotion": expr["hud_mood"],
                        "esp_mood": expr["esp_mood"],
                        "esp_anim": expr["esp_anim"],
                        "hold_emotion_ms": 8000,
                        "is_vision": False,
                        "has_frame": False,
                        "effective_query": user_text.strip(),
                    }

            if intent == "time":
                return {
                    "model": self.model,
                    "messages": [],
                    "keep_alive": "60m",
                    "options": {},
                    "extra_body": {},
                    "direct_reply": _format_time_date_reply(user_text),
                    "forced_emotion": "happy",
                    "esp_mood": "happy",
                    "hold_emotion_ms": 3000,
                    "is_vision": False,
                    "has_frame": False,
                    "effective_query": user_text.strip(),
                }

            if intent in ("vision", "vision_followup"):
                return self._build_vision_turn(
                    user_text,
                    char_id,
                    is_followup=(intent == "vision_followup"),
                )

        messages = self._build_messages(
            user_text,
            char_id,
            hass_action=hass_action,
            mac_action=mac_action,
            intent=intent,
            prechecked_actions=True,
        )
        return {
            "model": self.model,
            "messages": messages,
            "keep_alive": "60m",
            "options": {
                "temperature": 0.72,
                "top_p": 0.9,
                "repeat_penalty": 1.15,
                "presence_penalty": 0.4,
                "num_ctx": OLLAMA_NUM_CTX,
                "num_predict": 52,
                "num_thread": 4,
                "stop": _STOP_SEQUENCES,
            },
            "extra_body": {},
            "is_vision": False,
            "has_frame": False,
            "effective_query": user_text.strip(),
        }

    def inspect_image(
        self,
        image_bytes: bytes,
        prompt: str = "Describe what you see right now.",
        character: str = "rocky",
        filter_intensity: str = "balanced",
        apply_filter: bool = True,
    ) -> str:
        """1-hop VLM inspection of raw JPEG bytes with Rocky persona and exact prompt grounding."""
        spec = REGISTRY.get(character)
        char_id = spec.id
        turn = self._build_vision_turn(
            user_text=prompt or "Describe what you see right now.",
            char_id=char_id,
            image_bytes=image_bytes,
        )
        reply = ""
        llm_status = get_llm_runtime_status()
        if llm_status["active_provider"] != "ollama":
            try:
                max_tok = max(160, int(turn["options"].get("num_predict", 160)) + 60)
                temp = float(turn["options"].get("temperature", 0.3))
                reply = "".join(
                    _stream_openai_compat(turn["messages"], max_tokens=max_tok, temperature=temp)
                ).strip()
            except Exception as exc:
                _log_event("BRAIN", f"Cloud vision fallback to Ollama: {exc}")
                reply = ""

        if not reply:
            self.ensure_ollama_running()
            req_body: dict[str, Any] = {
                "model": turn["model"],
                "messages": turn["messages"],
                "stream": False,
                "keep_alive": turn["keep_alive"],
                "options": turn["options"],
                **turn["extra_body"],
            }
            try:
                resp = self._client.post(f"{OLLAMA_URL}/api/chat", json=req_body, timeout=45.0)
                resp.raise_for_status()
                reply = resp.json().get("message", {}).get("content", "").strip()
            except Exception as exc:
                _log_event("VISION", f"Inspect image error: {exc}")
                return ""

        reply = re.sub(r"^(?:Rocky|Grace|User|Assistant|Human):\s*", "", reply, flags=re.I)
        for marker in ["\nQuestion:", "\nAnswer:", "\nUser:", "\nRocky:", "\nGrace:", "\nHuman:", "\nAssistant:"]:
            if marker in reply:
                reply = reply.split(marker)[0].strip()

        sents = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", reply)
            if s.strip() and not re.fullmatch(r"question[.!?]*", s.strip(), re.I)
        ]
        reply = " ".join(sents[:2]).strip()
        if apply_filter and reply:
            reply = apply_character_filter(reply, character=char_id, intensity=filter_intensity)
        if reply:
            memory.remember_vision(reply, character=char_id)
        return reply

    def _iter_turn_tokens(self, turn: dict[str, Any]) -> Iterator[str]:
        """Yield tokens from Cloud Brain when configured, with seamless automatic fallback to local Ollama."""
        llm_status = get_llm_runtime_status()
        if llm_status["active_provider"] != "ollama":
            yielded_any = False
            try:
                max_tok = max(120, int(turn["options"].get("num_predict", 90)) + 60)
                temp = float(turn["options"].get("temperature", 0.7))
                for tok in _stream_openai_compat(
                    turn["messages"],
                    max_tokens=max_tok,
                    temperature=temp,
                ):
                    if tok:
                        yielded_any = True
                        yield tok
                if yielded_any:
                    return
            except Exception as exc:
                _log_event("BRAIN", f"Cloud fallback to Ollama ({llm_status['active_provider']}): {exc}")
                if yielded_any:
                    return

        self.ensure_ollama_running()
        req_body: dict[str, Any] = {
            "model": turn["model"],
            "messages": turn["messages"],
            "stream": True,
            "keep_alive": turn["keep_alive"],
            "options": turn["options"],
            **turn["extra_body"],
        }
        with self._client.stream(
            "POST",
            f"{OLLAMA_URL}/api/chat",
            json=req_body,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                tok = chunk.get("message", {}).get("content", "")
                if tok:
                    yield tok

    def ask_stream(
        self,
        user_text: str,
        character: str = "rocky",
        filter_intensity: str = "balanced",
    ) -> Iterator[dict[str, Any]]:
        t0 = time.perf_counter()
        spec = REGISTRY.get(character)
        char_id = spec.id
        if get_llm_runtime_status()["active_provider"] == "ollama":
            self.ensure_ollama_running()
        turn = self._prepare_turn(user_text, char_id)
        is_vision = bool(turn["is_vision"])

        # Fast deterministic path (dance, head-look, face tracking, robot-face expression, camera timeout)
        direct_reply = turn.get("direct_reply")
        forced_emotion = turn.get("forced_emotion")
        hold_emotion_ms = turn.get("hold_emotion_ms", 0)
        esp_mood = turn.get("esp_mood")
        esp_anim = turn.get("esp_anim")
        if direct_reply:
            emitted_direct: list[str] = []
            sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", direct_reply) if s.strip()][:2]
            for d_idx, sent in enumerate(sents):
                filtered = apply_character_filter(sent, character=char_id, intensity=filter_intensity)
                if filtered:
                    emitted_direct.append(filtered)
                    now_ms = int((time.perf_counter() - t0) * 1000)
                    out_item: dict[str, Any] = {
                        "type": "sentence",
                        "sentence": filtered,
                        "index": d_idx,
                        "first_token_ms": now_ms,
                        "clause_ms": now_ms,
                        "end_conversation": False,
                    }
                    if forced_emotion:
                        out_item["emotion"] = forced_emotion
                        if esp_mood:
                            out_item["esp_mood"] = esp_mood
                        if esp_anim:
                            out_item["esp_anim"] = esp_anim
                        if hold_emotion_ms:
                            out_item["hold_emotion_ms"] = hold_emotion_ms
                    yield out_item
            full_direct = " ".join(emitted_direct)
            self._last_turn_was_vision[char_id] = is_vision
            if is_vision:
                self._last_vision_query[char_id] = turn["effective_query"]
            hist = self.histories.setdefault(char_id, [])
            hist.append({"role": "user", "content": user_text})
            hist.append({"role": "assistant", "content": full_direct})
            memory.log_dialogue(char_id, "user", user_text)
            memory.log_dialogue(char_id, "assistant", full_direct)
            return

        buffer = ""
        emitted_sentences: list[str] = []
        completed_sentences = 0
        max_sentences = int(turn.get("max_sentences", 2))
        is_reading = bool(turn.get("is_reading", False))
        effective_filter = "off" if is_reading else filter_intensity
        first_token_ms: int | None = None
        idx = 0

        for tok in self._iter_turn_tokens(turn):
            if first_token_ms is None:
                first_token_ms = int((time.perf_counter() - t0) * 1000)
            buffer += tok

            if idx == 0 and not emitted_sentences:
                buffer = re.sub(r"^(?:Rocky|Grace|User|Assistant|Human):\s*", "", buffer, flags=re.I)

            cut = False
            for marker in ["\nQuestion:", "\nAnswer:", "\nUser:", "\nRocky:", "\nGrace:", "\nHuman:", "\nAssistant:"]:
                if marker in buffer:
                    buffer = buffer.split(marker)[0].strip()
                    cut = True
                    break

            while True:
                if completed_sentences >= max_sentences:
                    break
                popped = _pop_next_chunk(
                    buffer,
                    idx,
                    allow_clause_split=(not is_vision or is_reading),
                    split_newlines=is_reading,
                )
                if not popped:
                    break
                candidate, buffer = popped
                ended = "🔚" in candidate
                candidate = candidate.replace("🔚", "")
                cap_start = (
                    idx == 0
                    or not emitted_sentences
                    or emitted_sentences[-1][-1] in ".!?"
                )
                filtered = apply_character_filter(
                    candidate,
                    character=char_id,
                    intensity=effective_filter,
                    capitalize_start=cap_start,
                )
                if filtered and re.fullmatch(r"question[.!?]*", filtered.strip(), re.I):
                    continue
                if filtered:
                    emitted_sentences.append(filtered)
                    if filtered[-1] in ".!?":
                        completed_sentences += 1
                    yield {
                        "type": "sentence",
                        "sentence": filtered,
                        "index": idx,
                        "is_reading": is_reading,
                        "first_token_ms": first_token_ms or 0,
                        "clause_ms": int((time.perf_counter() - t0) * 1000),
                        "end_conversation": ended,
                    }
                    idx += 1

            if cut or completed_sentences >= max_sentences:
                break

        saw_end_marker = False
        if completed_sentences < max_sentences:
            rem = buffer.strip()
            if idx == 0 and not emitted_sentences:
                rem = re.sub(r"^(?:Rocky|Grace|User|Assistant|Human):\s*", "", rem, flags=re.I)
            for marker in ["\nQuestion:", "\nAnswer:", "\nUser:", "\nRocky:", "\nGrace:", "\nHuman:", "\nAssistant:"]:
                if marker in rem:
                    rem = rem.split(marker)[0].strip()
            if rem:
                ended = "🔚" in rem
                if ended:
                    saw_end_marker = True
                rem = rem.replace("🔚", "")
                cap_start = (
                    idx == 0
                    or not emitted_sentences
                    or emitted_sentences[-1][-1] in ".!?"
                )
                filtered = apply_character_filter(
                    rem,
                    character=char_id,
                    intensity=effective_filter,
                    capitalize_start=cap_start,
                )
                if filtered and not re.fullmatch(r"question[.!?]*", filtered.strip(), re.I):
                    emitted_sentences.append(filtered)
                    yield {
                        "type": "sentence",
                        "sentence": filtered,
                        "index": idx,
                        "is_reading": is_reading,
                        "first_token_ms": first_token_ms or int((time.perf_counter() - t0) * 1000),
                        "clause_ms": int((time.perf_counter() - t0) * 1000),
                        "end_conversation": ended,
                    }

        if not emitted_sentences:
            fallback = spec.fallback_reply
            emitted_sentences.append(fallback)
            yield {
                "sentence": fallback,
                "index": 0,
                "first_token_ms": int((time.perf_counter() - t0) * 1000),
                "clause_ms": int((time.perf_counter() - t0) * 1000),
            }

        full_reply = " ".join(emitted_sentences)
        if saw_end_marker or (
            FAREWELL_USER_RE.search(user_text) and "?" not in user_text
        ):
            yield {
                "sentence": "",
                "index": idx + 1,
                "first_token_ms": first_token_ms or 0,
                "clause_ms": int((time.perf_counter() - t0) * 1000),
                "end_conversation": True,
            }

        self._last_turn_was_vision[char_id] = is_vision
        if is_vision:
            self._last_vision_query[char_id] = turn["effective_query"]
            self._last_vision_ts[char_id] = time.time()
            if turn.get("has_frame") and full_reply:
                memory.remember_vision(full_reply, character=char_id)

        hist = self.histories.setdefault(char_id, [])
        hist.append({"role": "user", "content": user_text})
        hist.append({"role": "assistant", "content": full_reply})
        memory.log_dialogue(char_id, "user", user_text)
        memory.log_dialogue(char_id, "assistant", full_reply)

    def ask(
        self,
        user_text: str,
        character: str = "rocky",
        filter_intensity: str = "balanced",
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        spec = REGISTRY.get(character)
        parts = [
            item["sentence"]
            for item in self.ask_stream(
                user_text, character=spec.id, filter_intensity=filter_intensity
            )
            if item.get("sentence")
        ]
        full = " ".join(parts)
        return {
            "character": spec.id,
            "raw_reply": full,
            "filtered_reply": full,
            "rocky_reply": full,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }
