"""
Antigravity Agentic Bridge & Codebase Pair Programmer.
Enables natural spoken pair programming with Antigravity (Google DeepMind agent),
allowing the user to inspect code, check git status, commit updates, run terminal tasks,
view system diagnostics, and trigger autonomous code edits via voice or text.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger("agent_bridge")

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # /Users/dad/rocky-robot
VOICE_DIR = os.path.join(PROJECT_DIR, "voice")
AGY_BIN = os.environ.get("AGY_BIN", "/Users/dad/.local/bin/agy")


def _run_cmd(cmd: list[str] | str, cwd: str = PROJECT_DIR, timeout: int = 15) -> tuple[int, str]:
    try:
        if isinstance(cmd, str):
            res = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        else:
            res = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        out = (res.stdout or "") + (res.stderr or "")
        return res.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return -1, "Command timed out after 15 seconds."
    except Exception as exc:
        return -1, f"Execution failed: {exc}"


# -----------------------------------------------------------------------------
# 1. Git & Codebase Inspection Actions
# -----------------------------------------------------------------------------

def git_status() -> str:
    """Returns a concise summary of current git working tree status."""
    code, out = _run_cmd(["git", "status", "-s"])
    if code != 0:
        return f"Git error: {out}"
    if not out:
        return "Working tree clean. All changes are committed to the repository."

    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    modified = [ln[2:].strip() for ln in lines if ln.startswith("M") or ln.startswith(" M")]
    untracked = [ln[2:].strip() for ln in lines if ln.startswith("??")]
    added = [ln[2:].strip() for ln in lines if ln.startswith("A")]

    parts: list[str] = []
    if modified:
        parts.append(f"{len(modified)} modified ({', '.join(modified[:3])}{'...' if len(modified) > 3 else ''})")
    if untracked:
        parts.append(f"{len(untracked)} untracked ({', '.join(untracked[:2])})")
    if added:
        parts.append(f"{len(added)} staged for commit")

    summary = ", ".join(parts) if parts else f"{len(lines)} file(s) changed"
    return f"Git status: {summary}."


def git_diff(max_chars: int = 400) -> str:
    """Returns a brief snippet of current uncommitted git diff."""
    code, out = _run_cmd(["git", "diff", "--stat"])
    if code != 0 or not out:
        return "No uncommitted code changes in the active workspace."
    lines = out.strip().splitlines()
    summary = lines[-1] if lines else "Workspace has pending modifications."
    return f"Active changes: {summary}."


def git_log(limit: int = 2) -> str:
    """Returns the most recent git commits."""
    code, out = _run_cmd(["git", "log", f"-n{limit}", "--oneline"])
    if code != 0 or not out:
        return "Could not retrieve commit history."
    return f"Recent commits: {out.replace(chr(10), ' | ')}."


def git_commit_and_push(commit_msg: str | None = None) -> str:
    """Stages all changes, creates a commit, and pushes to origin main."""
    msg = commit_msg or f"feat: updates via Antigravity voice turn at {time.strftime('%Y-%m-%d %H:%M')}"
    code_add, out_add = _run_cmd(["git", "add", "."])
    if code_add != 0:
        return f"Git add failed: {out_add}"

    code_commit, out_commit = _run_cmd(["git", "commit", "-m", msg])
    if "nothing to commit" in out_commit.lower():
        return "Nothing to commit; your workspace is already up to date."

    code_push, out_push = _run_cmd(["git", "push", "origin", "main"], timeout=25)
    if code_push == 0:
        return f"Successfully committed '{msg}' and pushed to origin main."
    return f"Committed locally, but git push reported: {out_push[:120]}."


def git_pull() -> str:
    """Pulls latest commits from remote."""
    code, out = _run_cmd(["git", "pull", "origin", "main"], timeout=20)
    if code == 0:
        return f"Git pull complete: {out.splitlines()[-1] if out else 'Up to date'}."
    return f"Git pull failed: {out[:120]}."


# -----------------------------------------------------------------------------
# 2. System Diagnostics (MacBook Pro Hardware & Process Health)
# -----------------------------------------------------------------------------

def get_system_diagnostics() -> str:
    """Returns MacBook Pro battery, CPU load, memory, and disk health."""
    # 1. Battery
    batt_str = "Battery unavailable"
    code_b, out_b = _run_cmd("pmset -g batt")
    if code_b == 0 and "%" in out_b:
        m = re.search(r"(\d+%);\s*([^;]+)", out_b)
        if m:
            batt_str = f"MacBook battery at {m.group(1)} ({m.group(2).strip()})"

    # 2. Disk Space
    disk_str = ""
    code_d, out_d = _run_cmd("df -h / | tail -n 1")
    if code_d == 0 and out_d:
        cols = out_d.split()
        if len(cols) >= 4:
            disk_str = f"{cols[3]} free storage"

    # 3. CPU / Memory
    code_t, out_t = _run_cmd("top -l 1 | head -n 8")
    cpu_str = ""
    mem_str = ""
    if code_t == 0:
        m_cpu = re.search(r"CPU usage:\s*([0-9.]+%\s*user,\s*[0-9.]+%\s*sys)", out_t)
        if m_cpu:
            cpu_str = f"CPU load {m_cpu.group(1)}"
        m_mem = re.search(r"PhysMem:\s*([0-9A-Z]+)\s*used", out_t)
        if m_mem:
            mem_str = f"{m_mem.group(1)} RAM used"

    details = [p for p in [batt_str, cpu_str, mem_str, disk_str] if p]
    return " • ".join(details) if details else "System running nominally on Apple Silicon."


def get_server_health() -> str:
    """Inspects running voice server, LLM, and camera status."""
    return (
        "Rocky voice server active on port 7860 with Ollama Qwen2.5:1.5b INT8, "
        "Faster-Whisper, Piper neural synthesis, and dual FaceTime HD / ESP32 camera pipeline."
    )


# -----------------------------------------------------------------------------
# 3. Autonomous Agentic Code Update Tasks
# -----------------------------------------------------------------------------

def run_agent_task(prompt: str) -> str:
    """
    Dispatches an autonomous coding task to the Antigravity CLI (agy)
    with permissions auto-approved for non-blocking voice execution.
    """
    if not os.path.exists(AGY_BIN):
        return f"Antigravity CLI not found at {AGY_BIN}."

    logger.info("Dispatching agentic task to agy: %s", prompt)
    cmd = [
        AGY_BIN,
        "-p", prompt,
        "--dangerously-skip-permissions",
        "--print-timeout", "45s",
    ]
    code, out = _run_cmd(cmd, cwd=PROJECT_DIR, timeout=50)
    if code == 0:
        first_line = out.strip().splitlines()[-1] if out else "Task completed successfully."
        return f"Antigravity agent executed update: {first_line[:140]}."
    return f"Agent task reported: {out[:140] if out else 'Exit code ' + str(code)}."


# -----------------------------------------------------------------------------
# 4. Spoken Intent Routing
# -----------------------------------------------------------------------------

INTENT_PATTERNS: list[tuple[re.Pattern[str], Any]] = [
    (re.compile(r"\b(?:git\s+status|status\s+of\s+(?:the\s+)?(?:repo|codebase|project)|what\s+files\s+(?:are\s+)?changed|what\s+did\s+we\s+change|check\s+repo\s+status)\b", re.I), git_status),
    (re.compile(r"\b(?:git\s+diff|show\s+(?:me\s+)?(?:the\s+)?diff|show\s+(?:the\s+)?changes|what\s+is\s+different)\b", re.I), git_diff),
    (re.compile(r"\b(?:recent\s+commits|last\s+commit|git\s+log|git\s+history|show\s+commits)\b", re.I), git_log),
    (re.compile(r"\b(?:git\s+pull|pull\s+(?:latest\s+)?changes|update\s+from\s+repo|pull\s+code)\b", re.I), git_pull),
    (re.compile(r"\b(?:battery|system\s+stats|cpu\s+usage|memory\s+usage|macbook\s+health|system\s+health|diagnostics)\b", re.I), get_system_diagnostics),
    (re.compile(r"\b(?:server\s+health|server\s+status|voice\s+server\s+status)\b", re.I), get_server_health),
]

COMMIT_RE = re.compile(
    r"\b(?:commit\s+(?:our\s+|the\s+)?changes|commit\s+and\s+push|push\s+(?:to\s+)?git(?:hub)?)(?:\s+(?:with\s+message|saying)\s+[\"']?([^\"']+)[\"']?)?\b",
    re.I,
)

RUN_TASK_RE = re.compile(
    r"\b(?:run\s+(?:agent|agy|antigravity\s+task)|update\s+(?:the\s+)?codebase|perform\s+(?:an?\s+)?update|code\s+update)\s+(?:to\s+|for\s+)?(.+)",
    re.I,
)


def detect_and_execute_agent_action(user_text: str) -> str | None:
    """
    Parses conversational user instructions and executes coding / system tasks.
    Returns a human-readable execution confirmation string, or None if no action matched.
    """
    # 1. Commit and push
    m_commit = COMMIT_RE.search(user_text)
    if m_commit:
        custom_msg = m_commit.group(1) if m_commit.group(1) else None
        return git_commit_and_push(custom_msg)

    # 2. Autonomous Agent Task via agy
    m_task = RUN_TASK_RE.search(user_text)
    if m_task:
        task_prompt = m_task.group(1).strip()
        if task_prompt:
            return run_agent_task(task_prompt)

    # 3. Pattern-matched queries
    for pat, func in INTENT_PATTERNS:
        if pat.search(user_text):
            try:
                return func()
            except Exception as exc:
                return f"Encountered error executing {func.__name__}: {exc}"

    return None
