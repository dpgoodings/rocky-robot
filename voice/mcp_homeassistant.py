"""
Home Assistant MCP & REST Bridge for Rocky-Robot.

Connects Rocky to Home Assistant at http://homeassistant.local:8123 (or custom IP)
using the Home Assistant MCP server and REST API.

Enables Rocky to control smart lights, switches, fans, climate, and read sensor states
using natural language speech.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

import httpx

CONFIG_FILE = Path(__file__).parent / "hass_config.json"
DEFAULT_HASS_URL = "http://homeassistant.local:8123"

_LOCK = threading.Lock()
_ENTITIES_CACHE: list[dict[str, Any]] = []
_CACHE_TIMESTAMP = 0.0


def get_config() -> dict[str, str]:
    """Reads Home Assistant configuration from environment or local JSON."""
    url = os.environ.get("HOME_ASSISTANT_URL") or os.environ.get("HASS_URL") or DEFAULT_HASS_URL
    token = os.environ.get("HOME_ASSISTANT_TOKEN") or os.environ.get("HASS_TOKEN") or ""

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                url = data.get("url") or url
                token = data.get("token") or token
        except Exception:
            pass

    return {"url": url.rstrip("/"), "token": token}


def save_config(token: str, url: str = DEFAULT_HASS_URL) -> None:
    """Saves Home Assistant credentials to local gitignored config file."""
    clean_url = url.strip().rstrip("/") or DEFAULT_HASS_URL
    clean_token = token.strip()
    with open(CONFIG_FILE, "w") as f:
        json.dump({"url": clean_url, "token": clean_token}, f, indent=2)


def is_configured() -> bool:
    """Checks whether a valid token is provided."""
    cfg = get_config()
    return bool(cfg.get("token"))


def _headers() -> dict[str, str]:
    cfg = get_config()
    return {
        "Authorization": f"Bearer {cfg['token']}",
        "Content-Type": "application/json",
    }


def test_connection() -> dict[str, Any]:
    """Verifies connection to Home Assistant."""
    cfg = get_config()
    if not cfg["token"]:
        return {"connected": False, "url": cfg["url"], "error": "No token configured"}

    try:
        with httpx.Client(timeout=4.0) as client:
            r = client.get(f"{cfg['url']}/api/", headers=_headers())
            if r.status_code == 200:
                data = r.json()
                return {"connected": True, "url": cfg["url"], "message": data.get("message", "API running")}
            elif r.status_code == 401:
                return {"connected": False, "url": cfg["url"], "error": "Invalid access token (401 Unauthorized)"}
            return {"connected": False, "url": cfg["url"], "error": f"HTTP {r.status_code}"}
    except Exception as exc:
        return {"connected": False, "url": cfg["url"], "error": str(exc)}


def fetch_entities(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Retrieves all devices, lights, switches, and sensors from Home Assistant."""
    global _ENTITIES_CACHE, _CACHE_TIMESTAMP
    cfg = get_config()
    if not cfg["token"]:
        return []

    try:
        with httpx.Client(timeout=4.0) as client:
            r = client.get(f"{cfg['url']}/api/states", headers=_headers())
            if r.status_code == 200:
                _ENTITIES_CACHE = r.json()
                return _ENTITIES_CACHE
    except Exception as exc:
        print(f"[HomeAssistant] fetch_entities error: {exc}", flush=True)

    return _ENTITIES_CACHE


def find_entity_by_name(target_name: str, domain: str | None = None) -> dict[str, Any] | None:
    """Finds an entity matching the spoken target name (e.g., 'living room light')."""
    clean_target = target_name.strip().lower()
    entities = fetch_entities()
    if not entities:
        return None

    # Exact entity_id match
    for e in entities:
        eid = e.get("entity_id", "").lower()
        if eid == clean_target:
            return e

    # Filter by domain if specified
    candidates = entities
    if domain:
        candidates = [e for e in entities if e.get("entity_id", "").startswith(f"{domain}.")]

    # Match friendly_name
    for e in candidates:
        friendly = e.get("attributes", {}).get("friendly_name", "").lower()
        if friendly and (clean_target == friendly or clean_target in friendly or friendly in clean_target):
            return e

    # Fallback substring on entity_id
    for e in candidates:
        eid = e.get("entity_id", "").lower()
        slug = clean_target.replace(" ", "_")
        if slug in eid:
            return e

    return None


def call_service(domain: str, service: str, entity_id: str | None = None, extra_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Calls a Home Assistant service (e.g. light.turn_on, switch.turn_off)."""
    cfg = get_config()
    if not cfg["token"]:
        return {"success": False, "error": "Home Assistant token is not configured."}

    payload: dict[str, Any] = {}
    if entity_id:
        payload["entity_id"] = entity_id
    if extra_data:
        payload.update(extra_data)

    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.post(
                f"{cfg['url']}/api/services/{domain}/{service}",
                headers=_headers(),
                json=payload,
            )
            if r.status_code == 200:
                return {"success": True, "status": "executed", "data": r.json()}
            return {"success": False, "error": f"Home Assistant error {r.status_code}: {r.text}"}
    except Exception as exc:
        return {"success": False, "error": f"Connection error: {exc}"}


# -----------------------------------------------------------------------------
# Voice Intent Detection
# -----------------------------------------------------------------------------

TURN_ON_RE = re.compile(
    r"\b(?:turn\s+on|switch\s+on|activate|enable|start)\s+(?:the\s+)?([a-zA-Z0-9_ -]+?)(?:\s+light|\s+lamp|\s+switch|\s+plug)?\b",
    re.IGNORECASE,
)
TURN_OFF_RE = re.compile(
    r"\b(?:turn\s+off|switch\s+off|deactivate|disable|shut\s+off)\s+(?:the\s+)?([a-zA-Z0-9_ -]+?)(?:\s+light|\s+lamp|\s+switch|\s+plug)?\b",
    re.IGNORECASE,
)
STATE_QUERY_RE = re.compile(
    r"\b(?:what\s+is\s+the|status\s+of|is\s+the|check\s+the)\s+(?:the\s+)?([a-zA-Z0-9_ -]+?)(?:\s+on|\s+off|\s+open|\s+locked|\s+temperature|\s+status)?\b",
    re.IGNORECASE,
)


def detect_and_execute_hass_intent(user_text: str) -> str | None:
    """
    Parses natural language smart home commands and executes them in Home Assistant.
    Returns a short confirmation for the persona LLM to voice.
    """
    if not is_configured():
        return None

    text = user_text.strip()

    # Skip Mac app commands and vision queries
    if any(k in text.lower() for k in ["safari", "notes", "camera", "volume", "website", "desktop"]):
        return None

    # 1. Turn ON command
    m_on = TURN_ON_RE.search(text)
    if m_on:
        target = m_on.group(1).strip()
        entity = find_entity_by_name(target)
        if entity:
            eid = entity["entity_id"]
            domain = eid.split(".")[0]
            fname = entity.get("attributes", {}).get("friendly_name", eid)
            res = call_service(domain, "turn_on", entity_id=eid)
            if res.get("success"):
                return f"Turned on {fname}"
            return f"Failed to turn on {fname}: {res.get('error')}"

    # 2. Turn OFF command
    m_off = TURN_OFF_RE.search(text)
    if m_off:
        target = m_off.group(1).strip()
        entity = find_entity_by_name(target)
        if entity:
            eid = entity["entity_id"]
            domain = eid.split(".")[0]
            fname = entity.get("attributes", {}).get("friendly_name", eid)
            res = call_service(domain, "turn_off", entity_id=eid)
            if res.get("success"):
                return f"Turned off {fname}"
            return f"Failed to turn off {fname}: {res.get('error')}"

    # 3. Query state command
    m_state = STATE_QUERY_RE.search(text)
    if m_state:
        target = m_state.group(1).strip()
        entity = find_entity_by_name(target)
        if entity:
            fname = entity.get("attributes", {}).get("friendly_name", entity["entity_id"])
            state = entity.get("state", "unknown")
            unit = entity.get("attributes", {}).get("unit_of_measurement", "")
            return f"{fname} is currently {state} {unit}".strip()

    return None
