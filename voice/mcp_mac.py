"""
macOS MCP Control Bridge for Rocky-Robot.

Connects to the @peakmojo/applescript-mcp server over stdio to control
macOS applications, system volume, media playback, windows, and notifications.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from typing import Any

NPX_BIN = os.environ.get("NPX_BIN", "/Users/dad/.local/bin/npx")
MCP_PACKAGE = "@peakmojo/applescript-mcp"

_LOCK = threading.Lock()
_PROC: subprocess.Popen[str] | None = None
_REQ_ID = 0


def _get_mcp_process() -> subprocess.Popen[str]:
    global _PROC, _REQ_ID
    if _PROC is not None and _PROC.poll() is None:
        return _PROC

    env = os.environ.copy()
    env["PATH"] = f"/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/dad/.local/bin:{env.get('PATH', '')}"

    _PROC = subprocess.Popen(
        [NPX_BIN, "-y", MCP_PACKAGE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )

    # Initialize handshake
    _REQ_ID = 1
    init_payload = {
        "jsonrpc": "2.0",
        "id": _REQ_ID,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "rocky-brain", "version": "1.0"},
        },
    }
    _PROC.stdin.write(json.dumps(init_payload) + "\n")
    _PROC.stdin.flush()
    _PROC.stdout.readline()

    # Initialized notification
    notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    _PROC.stdin.write(json.dumps(notif) + "\n")
    _PROC.stdin.flush()

    return _PROC


def execute_applescript(code_snippet: str, timeout: int = 20) -> str:
    """Executes AppleScript code via the MCP Server."""
    global _REQ_ID
    with _LOCK:
        try:
            proc = _get_mcp_process()
            _REQ_ID += 1
            call_req = {
                "jsonrpc": "2.0",
                "id": _REQ_ID,
                "method": "tools/call",
                "params": {
                    "name": "applescript_execute",
                    "arguments": {
                        "code_snippet": code_snippet,
                        "timeout": timeout,
                    },
                },
            }
            proc.stdin.write(json.dumps(call_req) + "\n")
            proc.stdin.flush()

            raw_res = proc.stdout.readline()
            if not raw_res:
                return "Error: Empty response from macOS MCP server."

            data = json.loads(raw_res)
            content_list = data.get("result", {}).get("content", [])
            if content_list and "text" in content_list[0]:
                return content_list[0]["text"].strip()
            if "error" in data:
                return f"MCP Error: {data['error'].get('message', 'Unknown error')}"
            return "Command executed successfully."
        except Exception as exc:
            return f"Error executing AppleScript via MCP: {exc}"


# -----------------------------------------------------------------------------
# High-Level macOS Actions
# -----------------------------------------------------------------------------

def open_app(app_name: str) -> str:
    """Launches or focuses a macOS application."""
    clean_name = app_name.strip().title()
    script = f'tell application "{clean_name}" to activate'
    res = execute_applescript(script)
    if "error" in res.lower():
        return f"Could not open {clean_name}: {res}"
    return f"Opened {clean_name}"


def quit_app(app_name: str) -> str:
    """Quits a macOS application."""
    clean_name = app_name.strip().title()
    script = f'tell application "{clean_name}" to quit'
    res = execute_applescript(script)
    if "error" in res.lower():
        return f"Could not quit {clean_name}: {res}"
    return f"Quit {clean_name}"


def set_system_volume(level: int) -> str:
    """Sets system volume 0-100."""
    target = max(0, min(100, level))
    script = f"set volume output volume {target}"
    execute_applescript(script)
    return f"Volume set to {target}%"


def control_media(action: str) -> str:
    """Controls Music or Spotify (play, pause, next, previous)."""
    action_lower = action.lower()
    if action_lower in ("play", "resume"):
        script = """
        if application "Music" is running then
            tell application "Music" to play
        else if application "Spotify" is running then
            tell application "Spotify" to play
        else
            tell application "Music" to play
        end if
        """
        execute_applescript(script)
        return "Resumed media playback"
    elif action_lower in ("pause", "stop"):
        script = """
        if application "Music" is running then
            tell application "Music" to pause
        end if
        if application "Spotify" is running then
            tell application "Spotify" to pause
        end if
        """
        execute_applescript(script)
        return "Paused media playback"
    elif action_lower in ("next", "skip"):
        script = """
        if application "Music" is running then
            tell application "Music" to next track
        else if application "Spotify" is running then
            tell application "Spotify" to next track
        end if
        """
        execute_applescript(script)
        return "Skipped to next track"
    elif action_lower in ("previous", "prev", "back"):
        script = """
        if application "Music" is running then
            tell application "Music" to previous track
        else if application "Spotify" is running then
            tell application "Spotify" to previous track
        end if
        """
        execute_applescript(script)
        return "Returned to previous track"
    return f"Unknown media action: {action}"


def send_notification(message: str, title: str = "Rocky-S3") -> str:
    """Displays a native macOS banner notification."""
    safe_msg = message.replace('"', '\\"')
    safe_title = title.replace('"', '\\"')
    script = f'display notification "{safe_msg}" with title "{safe_title}"'
    execute_applescript(script)
    return f"Sent notification: {message}"


def open_url(url: str) -> str:
    """Opens a website URL in the default browser."""
    if not url.startswith("http://") and not url.startswith("https://"):
        target_url = "https://" + url
    else:
        target_url = url
    script = f'open location "{target_url}"'
    execute_applescript(script)
    return f"Opened {target_url}"


# -----------------------------------------------------------------------------
# Voice Intent Matching
# -----------------------------------------------------------------------------

APP_MAP = {
    "safari": "Safari",
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "browser": "Safari",
    "web browser": "Safari",
    "internet": "Safari",
    "firefox": "Firefox",
    "brave": "Brave Browser",
    "edge": "Microsoft Edge",
    "spotify": "Spotify",
    "music": "Music",
    "apple music": "Music",
    "notes": "Notes",
    "calendar": "Calendar",
    "reminders": "Reminders",
    "finder": "Finder",
    "terminal": "Terminal",
    "calculator": "Calculator",
    "mail": "Mail",
    "messages": "Messages",
    "photos": "Photos",
    "slack": "Slack",
    "code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
}

OPEN_APP_RE = re.compile(
    r"\b(?:open|launch|start|bring up)\s+([a-zA-Z0-9_ -]+?)(?:\s+app|\s+application)?\b",
    re.IGNORECASE,
)
QUIT_APP_RE = re.compile(
    r"\b(?:close|quit|exit|kill|shut down)\s+([a-zA-Z0-9_ -]+?)(?:\s+app|\s+application)?\b",
    re.IGNORECASE,
)
VOLUME_RE = re.compile(
    r"\b(?:set\s+volume\s+to\s+(\d+)|volume\s+(\d+)|turn\s+volume\s+(up|down)|mute)\b",
    re.IGNORECASE,
)
MEDIA_RE = re.compile(
    r"\b(?:pause\s+(?:the\s+)?music|stop\s+(?:the\s+)?music|play\s+(?:the\s+)?music|resume\s+music|next\s+track|next\s+song|skip\s+song|previous\s+song)\b",
    re.IGNORECASE,
)
URL_RE = re.compile(
    r"\b(?:go\s+to|open\s+url|open\s+website|open\s+site)\s+(https?://\S+|\S+\.(?:com|org|io|net|edu|dev|ai))\b",
    re.IGNORECASE,
)


def detect_and_execute_mac_control(user_text: str) -> str | None:
    """
    Checks if user text is asking to control macOS apps, media, or volume.
    Executes the command via MCP and returns the execution confirmation string.
    """
    text = user_text.strip()

    # Skip vision queries (e.g. "open your eyes", "open camera")
    if any(k in text.lower() for k in ["eyes", "camera", "stream", "feed", "optical"]):
        return None

    # 1. URL / Website
    m_url = URL_RE.search(text)
    if m_url:
        return open_url(m_url.group(1))

    # 2. Volume control
    m_vol = VOLUME_RE.search(text)
    if m_vol:
        if "mute" in text.lower():
            return set_system_volume(0)
        num = m_vol.group(1) or m_vol.group(2)
        if num:
            return set_system_volume(int(num))
        direction = m_vol.group(3)
        if direction:
            script = "output volume of (get volume settings)"
            cur_vol_str = execute_applescript(script)
            try:
                cur_vol = int(re.search(r"\d+", cur_vol_str).group(0))
            except Exception:
                cur_vol = 50
            new_vol = min(100, cur_vol + 15) if direction.lower() == "up" else max(0, cur_vol - 15)
            return set_system_volume(new_vol)

    # 3. Media control
    m_media = MEDIA_RE.search(text)
    if m_media:
        matched = m_media.group(0).lower()
        if "pause" in matched or "stop" in matched:
            return control_media("pause")
        elif "play" in matched or "resume" in matched:
            return control_media("play")
        elif "next" in matched or "skip" in matched:
            return control_media("next")
        elif "previous" in matched:
            return control_media("previous")

    # 4. Open App
    if re.search(r"\b(?:open|launch|start|bring up)\s+(?:the\s+)?(?:web\s+)?browser\b", text, re.I):
        return open_app("Safari")

    m_open = OPEN_APP_RE.search(text)
    if m_open:
        target = m_open.group(1).strip().lower()
        canonical = APP_MAP.get(target, target.title())
        return open_app(canonical)

    # 5. Quit App
    m_quit = QUIT_APP_RE.search(text)
    if m_quit:
        target = m_quit.group(1).strip().lower()
        canonical = APP_MAP.get(target, target.title())
        return quit_app(canonical)

    return None
