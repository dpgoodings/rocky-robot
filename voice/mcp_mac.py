"""
macOS MCP Control Bridge for Rocky-Robot.

Connects to the @peakmojo/applescript-mcp server over stdio to control
macOS applications, system volume, media playback, windows, notifications,
and website navigation.
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
    """Executes AppleScript code via the MCP Server, falling back to osascript if needed."""
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
                return _fallback_osascript(code_snippet, timeout)

            data = json.loads(raw_res)
            content_list = data.get("result", {}).get("content", [])
            if content_list and "text" in content_list[0]:
                return content_list[0]["text"].strip()
            if "error" in data:
                return f"MCP Error: {data['error'].get('message', 'Unknown error')}"
            return "Command executed successfully."
        except Exception:
            return _fallback_osascript(code_snippet, timeout)


def _fallback_osascript(code_snippet: str, timeout: int = 10) -> str:
    try:
        res = subprocess.run(
            ["osascript", "-e", code_snippet],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if res.returncode == 0:
            return res.stdout.strip()
        return f"AppleScript Error: {res.stderr.strip()}"
    except Exception as exc:
        return f"Error executing AppleScript: {exc}"


# -----------------------------------------------------------------------------
# High-Level macOS Actions
# -----------------------------------------------------------------------------

def open_app(app_name: str) -> str:
    """Launches or focuses a macOS application with AppleScript + open -a fallback."""
    clean_name = app_name.strip()
    if clean_name.lower() in ("browser", "web browser", "internet"):
        clean_name = "Safari"
    elif clean_name.lower() in ("so far", "so far app"):
        clean_name = "Safari"
    elif clean_name.lower() == "chrome":
        clean_name = "Google Chrome"
    elif clean_name.lower() in ("code", "vscode"):
        clean_name = "Visual Studio Code"

    clean_name = clean_name.title()
    if clean_name.lower() == "google chrome":
        clean_name = "Google Chrome"

    script = f'tell application "{clean_name}" to activate'
    res = execute_applescript(script)
    if "error" in res.lower():
        try:
            r = subprocess.run(["open", "-a", clean_name], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return f"Opened {clean_name}"
        except Exception:
            pass
        return f"Could not open {clean_name}: {res}"
    return f"Opened {clean_name}"


def quit_app(app_name: str) -> str:
    """Quits a macOS application."""
    clean_name = app_name.strip()
    if clean_name.lower() in ("browser", "web browser", "safari", "so far"):
        clean_name = "Safari"
    elif clean_name.lower() == "chrome":
        clean_name = "Google Chrome"

    clean_name = clean_name.title()
    if clean_name.lower() == "google chrome":
        clean_name = "Google Chrome"

    script = f'tell application "{clean_name}" to quit'
    res = execute_applescript(script)
    if "error" in res.lower():
        try:
            r = subprocess.run(["osascript", "-e", f'tell application "{clean_name}" to quit'], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return f"Quit {clean_name}"
        except Exception:
            pass
        return f"Could not quit {clean_name}: {res}"
    return f"Quit {clean_name}"


def list_open_apps() -> str:
    """Lists currently open GUI applications on the Mac."""
    script = 'tell application "System Events" to get name of every process whose visible is true'
    res = execute_applescript(script)
    if not res or "error" in res.lower():
        try:
            res = subprocess.check_output(["osascript", "-e", script], text=True, timeout=5).strip()
        except Exception:
            res = ""
    if res:
        apps = [a.strip() for a in res.split(",") if a.strip()]
        return f"Active open applications: {', '.join(apps)}"
    return "Could not retrieve list of open applications."


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
    target_url = url.strip()
    if not target_url.startswith("http://") and not target_url.startswith("https://"):
        target_url = "https://" + target_url
    script = f'open location "{target_url}"'
    res = execute_applescript(script)
    if "error" in res.lower():
        subprocess.run(["open", target_url])
    return f"Opened {target_url}"


def open_new_tab(url: str = "") -> str:
    """Opens a new browser tab or website."""
    target = url.strip() if url else "https://www.google.com"
    return open_url(target)


# -----------------------------------------------------------------------------
# Voice Intent Matching
# -----------------------------------------------------------------------------

APP_MAP = {
    "safari": "Safari",
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "browser": "Safari",
    "web browser": "Safari",
    "the browser": "Safari",
    "a browser": "Safari",
    "a web browser": "Safari",
    "internet": "Safari",
    "so far": "Safari",
    "firefox": "Firefox",
    "brave": "Brave Browser",
    "edge": "Microsoft Edge",
    "spotify": "Spotify",
    "music": "Music",
    "apple music": "Music",
    "notes": "Notes",
    "mac notes": "Notes",
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

WEB_SERVICES = {
    "youtube": "https://youtube.com",
    "google": "https://google.com",
    "github": "https://github.com",
    "reddit": "https://reddit.com",
    "chatgpt": "https://chatgpt.com",
    "netflix": "https://netflix.com",
    "gmail": "https://mail.google.com",
}

VOLUME_RE = re.compile(
    r"\b(?:set\s+volume\s+to\s+(\d+)|volume\s+(\d+)|turn\s+volume\s+(up|down)|mute)\b",
    re.IGNORECASE,
)
MEDIA_RE = re.compile(
    r"\b(?:pause\s+(?:the\s+)?music|stop\s+(?:the\s+)?music|play\s+(?:the\s+)?music|resume\s+music|next\s+track|next\s+song|skip\s+song|previous\s+song)\b",
    re.IGNORECASE,
)
URL_EXPLICIT_RE = re.compile(
    r"\b(?:go\s+to|open\s+url|open\s+website|open\s+site)\s+(https?://\S+|\S+\.(?:com|org|io|net|edu|dev|ai))\b",
    re.IGNORECASE,
)


def detect_and_execute_mac_control(user_text: str) -> str | None:
    """
    Checks if user text is asking to control macOS apps, media, or volume.
    Executes the command via MCP and returns the execution confirmation string.
    """
    text = re.sub(r"[?!.,]+$", "", user_text.strip()).strip()
    lower = text.lower()

    # Skip camera/vision queries (e.g. "open your eyes", "open camera")
    if any(k in lower for k in ["eyes", "camera", "stream", "feed", "optical"]):
        return None

    # 1. MCP connectivity / status query
    if any(p in lower for p in ["connected to mac", "access the mac", "access mac os mcp", "mac os mcp", "mac mcp"]):
        return "Connected to macOS MCP automation. I can launch applications, list open apps, control volume, play media, and open web pages."

    # 2. List open applications
    if re.search(r"\b(?:what|which)\s+(?:apps?|applications?)\s+(?:do\s+i\s+have\s+open|are\s+(?:currently\s+)?open)\b", lower, re.I) or \
       re.search(r"\blist\s+(?:open\s+)?(?:apps?|applications?)\b", lower, re.I):
        return list_open_apps()

    # 3. Explicit URL
    m_url = URL_EXPLICIT_RE.search(text)
    if m_url:
        return open_url(m_url.group(1))

    # 4. Web service shortcuts (e.g. "open up youtube", "go to youtube", "open chrome and go to youtube")
    for svc_name, svc_url in WEB_SERVICES.items():
        if re.search(rf"\b(?:open|launch|bring\s+up|go\s+to)\s+(?:up\s+)?(?:the\s+)?{svc_name}\b", lower) or \
           re.search(rf"\b(?:open|launch)\s+.*?\s+and\s+go\s+to\s+{svc_name}\b", lower):
            return open_url(svc_url)

    # 5. New browser tab
    if re.search(r"\b(?:open|launch)\s+(?:up\s+)?(?:a\s+)?new\s+tab\b", lower) or lower == "new tab":
        return open_new_tab()

    # 6. Volume control
    m_vol = VOLUME_RE.search(text)
    if m_vol:
        if "mute" in lower:
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

    # 7. Media control
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

    # 8. Browser general open phrases:
    # "open browser", "open up browser", "can you open up a web browser", "bring up the browser"
    if re.search(r"\b(?:open|launch|start|bring\s+up)\s+(?:up\s+)?(?:a\s+|an\s+|the\s+)?(?:web\s+)?browser\b", lower):
        return open_app("Safari")

    # 9. Clean polite prefixes for app matching
    clean_prompt = re.sub(
        r"^(?:hey\s+rocky|rocky|can\s+you|could\s+you|please|would\s+you|go\s+ahead\s+and)\s+",
        "",
        lower,
    ).strip()

    # Open App regex
    # Matches: "open up safari", "open notes", "launch google chrome", "bring up terminal"
    m_open = re.search(
        r"^(?:open|launch|start|bring\s+up|run)\s+(?:up\s+)?(?:a\s+|an\s+|the\s+|my\s+)?([a-zA-Z0-9_ -]+?)(?:\s+app|\s+application)?$",
        clean_prompt,
    )
    if m_open:
        raw_target = m_open.group(1).strip()
        # Remove any lingering "up " at the start
        if raw_target.startswith("up "):
            raw_target = raw_target[3:].strip()
        if raw_target and raw_target not in ("up", "app", "application"):
            canonical = APP_MAP.get(raw_target, raw_target.title())
            return open_app(canonical)

    # Quit App regex
    # Matches: "close safari", "close so far", "quit notes", "shut down chrome"
    m_quit = re.search(
        r"^(?:close|quit|exit|kill|shut\s+down)\s+(?:down\s+)?(?:a\s+|an\s+|the\s+|my\s+)?([a-zA-Z0-9_ -]+?)(?:\s+app|\s+application)?$",
        clean_prompt,
    )
    if m_quit:
        raw_target = m_quit.group(1).strip()
        if raw_target.startswith("down "):
            raw_target = raw_target[5:].strip()
        if raw_target and raw_target not in ("down", "app", "application"):
            canonical = APP_MAP.get(raw_target, raw_target.title())
            return quit_app(canonical)

    return None
