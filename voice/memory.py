"""
Persistent Long-Term Episodic & Semantic Memory for Rocky-Robot.

Stores:
- User facts, identity & preferences across reboots (SQLite)
- Episodic visual memories (every scene the robot camera observed)
- Long-term conversation logs
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent / "memory.db"

_LOCK = threading.Lock()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _LOCK:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    key TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS visual_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    description TEXT NOT NULL,
                    character TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS dialogue_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


init_db()


# -----------------------------------------------------------------------------
# Semantic Fact Memory
# -----------------------------------------------------------------------------

def set_fact(key: str, value: str, category: str = "general") -> None:
    """Stores or updates a learned fact about the user or robot environment."""
    key = key.strip().lower()
    value = value.strip()
    if not key or not value:
        return
    with _LOCK:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO facts (key, category, value, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    category = excluded.category,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, category, value),
            )
            conn.commit()
        finally:
            conn.close()


def get_facts() -> list[dict[str, Any]]:
    """Returns all stored facts ordered by category and update time."""
    with _LOCK:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT key, category, value, updated_at FROM facts ORDER BY category, updated_at DESC")
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_facts_summary() -> str:
    """Returns a concise text summary of all known facts to inject into LLM prompts."""
    facts = get_facts()
    if not facts:
        return ""
    lines = []
    for f in facts:
        k = f["key"].replace("_", " ")
        v = f["value"]
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Visual Memory (Robot Camera Observations)
# -----------------------------------------------------------------------------

def remember_vision(description: str, character: str = "rocky") -> None:
    """Logs an optical observation from the ESP32-CAM to persistent memory."""
    desc = description.strip()
    if not desc:
        return
    with _LOCK:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO visual_memories (description, character) VALUES (?, ?)",
                (desc, character),
            )
            conn.commit()
        finally:
            conn.close()


def get_recent_visual_memories(limit: int = 5) -> list[dict[str, Any]]:
    """Returns recent camera observations timestamped."""
    with _LOCK:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, description, character, timestamp FROM visual_memories ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# -----------------------------------------------------------------------------
# Dialogue Persistence
# -----------------------------------------------------------------------------

def log_dialogue(character: str, role: str, content: str) -> None:
    """Persists a conversation turn to SQLite."""
    text = content.strip()
    if not text:
        return
    with _LOCK:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO dialogue_history (character, role, content) VALUES (?, ?, ?)",
                (character.lower(), role, text),
            )
            conn.commit()
        finally:
            conn.close()


def load_recent_dialogue(character: str, limit: int = 10) -> list[dict[str, str]]:
    """Loads previous turns across server restarts."""
    with _LOCK:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT role, content FROM dialogue_history
                WHERE character = ?
                ORDER BY id DESC LIMIT ?
                """,
                (character.lower(), limit),
            )
            rows = cur.fetchall()
            # Reverse so it is chronological
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        finally:
            conn.close()


# -----------------------------------------------------------------------------
# Automatic Fact Extraction from Speech
# -----------------------------------------------------------------------------

_PATTERNS = [
    # User Name
    (re.compile(r"\b(?:my name is|call me|i am called|i'm called)\s+([A-Z][a-zA-Z]+)", re.I), "user_name", "identity"),
    # Preferences / Likes
    (re.compile(r"\b(?:i like|i love|i enjoy)\s+([^.,!?;]+)", re.I), "preference_like", "preference"),
    # Dislikes
    (re.compile(r"\b(?:i hate|i dislike|i don't like)\s+([^.,!?;]+)", re.I), "preference_dislike", "preference"),
    # Favorites
    (re.compile(r"\bmy favorite\s+([a-zA-Z0-9_ ]+?)\s+is\s+([^.,!?;]+)", re.I), "favorite_{0}", "preference"),
    # Explicit memory instruction
    (re.compile(r"\b(?:remember that|don't forget that|keep in mind that)\s+([^.,!?;]+)", re.I), "note", "memory"),
    # Identity / Profession
    (re.compile(r"\bi am an?\s+([a-zA-Z0-9_ ]+?)(?:by profession|engineer|doctor|student|developer|builder|programmer)?\b", re.I), "profession", "identity"),
    # Location
    (re.compile(r"\bi live in\s+([A-Za-z ]+)", re.I), "location", "identity"),
]

PAST_VISION_QUERY_RE = re.compile(
    r"\b(?:did you see|have you seen|what did you see|what was the last thing you saw|remember what you saw|earlier you saw|last time you saw)\b",
    re.I,
)


def extract_and_learn(user_text: str) -> list[str]:
    """Inspects user input and automatically registers facts into persistent memory."""
    learned = []
    clean = user_text.strip()
    
    for pattern, key_template, category in _PATTERNS:
        m = pattern.search(clean)
        if m:
            if "{0}" in key_template:
                slot = m.group(1).strip().replace(" ", "_").lower()
                key = key_template.format(slot)
                val = m.group(2).strip()
            else:
                key = key_template
                val = m.group(1).strip()
            
            # Avoid single common words or false positives
            if len(val) >= 2 and val.lower() not in {"to", "a", "the", "it", "this"}:
                set_fact(key, val, category=category)
                learned.append(f"{key}={val}")
                
    return learned


def build_memory_prompt_block(user_text: str) -> str:
    """Builds a rich context block containing persistent facts & visual memories."""
    blocks = []
    
    facts_summary = get_facts_summary()
    if facts_summary:
        blocks.append(f"Permanent knowledge about friend / user:\n{facts_summary}")
        
    if PAST_VISION_QUERY_RE.search(user_text):
        vis = get_recent_visual_memories(limit=4)
        if vis:
            vis_lines = [f"- Observed: \"{v['description']}\" (recorded at {v['timestamp']})" for v in vis]
            blocks.append("Past optical camera memories:\n" + "\n".join(vis_lines))
            
    if not blocks:
        return ""
        
    return "\n[PERMANENT LONG-TERM MEMORY]\n" + "\n\n".join(blocks) + "\n"
