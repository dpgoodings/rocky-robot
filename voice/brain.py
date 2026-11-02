"""
Generic Multi-Character Streaming Local Brain — Powered by local Ollama (qwen2.5:1.5b).

Reads character `system_prompt` and `fallback_reply` dynamically from
`character_registry.REGISTRY` so adding a JSON file in `characters/` automatically
gives the new character its own persona and conversation history!
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import datetime
import json
import os
import re
import subprocess
import time
from typing import Any

import httpx

from character_registry import REGISTRY
from rocky_filter import apply_character_filter

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
OLLAMA_BIN = os.path.join(PROJECT_DIR, "bin", "ollama")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("ROCKY_LLM_MODEL", "qwen2.5:1.5b")

TIME_QUERY_RE = re.compile(
    r"\b(?:time|date|day|today|tonight|tomorrow|month|year|clock|hour|morning|afternoon|evening)\b",
    re.IGNORECASE,
)
VISION_QUERY_RE = re.compile(
    r"\b(?:see|look|looking|camera|view|sight|watch|observe|what(?:'s|\s+is)\s+in\s+front|what\s+do\s+you\s+see|describe\s+what\s+you\s+see|inspect|surroundings|cup|blue\s+cup|object|room)\b",
    re.IGNORECASE,
)

def fetch_robot_vision(timeout: float = 25.0) -> str | None:
    """Grabs a live frame from ESP32-CAM and queries Moondream vision model."""
    robot_ip = os.environ.get("ROBOT_HOST", "192.168.4.136")
    try:
        with httpx.Client(timeout=4.0) as client:
            resp = client.get(f"http://{robot_ip}/capture")
            if resp.status_code != 200:
                return None
            img_b64 = base64.b64encode(resp.content).decode("utf-8")

        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": "moondream:latest",
                    "prompt": "Identify and describe what is directly in front of the camera, noting objects, colors, and items clearly.",
                    "images": [img_b64],
                    "stream": False,
                },
            )
            if r.status_code == 200:
                return r.json().get("response", "").strip()
    except Exception as exc:
        print(f"[Brain Vision Notice] {exc}", flush=True)
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
FAREWELL_REPLY_RE = re.compile(
    r"\b(?:bye|goodbye|see\s+you|see\s+ya|take\s+care|good\s*night|catch\s+you|later)\b",
    re.IGNORECASE,
)


def _pop_next_chunk(buffer: str, idx: int) -> tuple[str, str] | None:
    # First chunk allows fast conversational openers ("Yes!", "Good!", "Amaze!") to pop immediately
    min_sent = 3 if idx == 0 else 14
    for m in SENTENCE_SPLIT_RE.finditer(buffer):
        cand = buffer[: m.end()].strip()
        if len(cand) >= min_sent:
            return cand, buffer[m.end() :]

    min_clause = 10 if idx == 0 else 42
    for m in CLAUSE_SPLIT_RE.finditer(buffer):
        cand = buffer[: m.end()].strip()
        after = buffer[m.end() :].strip()
        if len(cand) >= min_clause and len(after) >= 8 and not DATE_OR_TAG_AFTER_RE.match(after):
            return cand, buffer[m.end() :]

    return None


class RockyBrain:
    def __init__(self, model: str = DEFAULT_MODEL, max_turns: int = 4) -> None:
        self.model = model
        self.max_turns = max_turns
        self.histories: dict[str, list[dict[str, str]]] = {}
        self._client = httpx.Client(timeout=35.0)
        self._ollama_proc: subprocess.Popen[Any] | None = None

    def ensure_ollama_running(self) -> bool:
        if self._ping():
            return True

        bin_path = OLLAMA_BIN if os.path.exists(OLLAMA_BIN) else "ollama"
        env = os.environ.copy()
        env["OLLAMA_MAX_LOADED_MODELS"] = "1"
        env["OLLAMA_KEEP_ALIVE"] = "60m"
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

    def warmup(self) -> None:
        self.ensure_ollama_running()
        default_spec = REGISTRY.get(REGISTRY.default_id)
        try:
            self._client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": default_spec.system_prompt},
                        {"role": "user", "content": "Hi"},
                    ],
                    "stream": False,
                    "keep_alive": "60m",
                    "options": {"num_ctx": 512, "num_predict": 2, "num_thread": 4},
                },
                timeout=30.0,
            )
        except Exception as exc:
            print(f"[Brain] Warmup warning: {exc}", flush=True)

    def reset_history(self, character: str | None = None) -> None:
        if character and character.lower() in self.histories:
            self.histories[character.lower()].clear()
        else:
            for h in self.histories.values():
                h.clear()

    def _build_messages(self, user_text: str, char_id: str) -> list[dict[str, str]]:
        spec = REGISTRY.get(char_id)
        hist = self.histories.setdefault(spec.id, [])
        messages = [{"role": "system", "content": spec.system_prompt}]
        messages.extend(hist[-(self.max_turns * 2) :])

        if TIME_QUERY_RE.search(user_text):
            now = datetime.now()
            short_time = now.strftime("%I:%M %p").lstrip("0")
            date_str = now.strftime("%A, %B %d, %Y")
            user_payload = (
                f"(Live clock: {short_time}; date: {date_str}. Keep your 2-sentence character format; "
                f"only read out the full date if the user asked for the date.)\n{user_text}"
            )
        elif VISION_QUERY_RE.search(user_text):
            v_desc = fetch_robot_vision()
            if v_desc:
                user_payload = (
                    f"(Real-time robot camera optical feed currently sees: \"{v_desc}\". "
                    f"Directly voice what you see in your authentic 2-sentence character persona. Do NOT ask questions back or claim you cannot see.)\n{user_text}"
                )
            else:
                user_payload = user_text
        else:
            user_payload = user_text

        messages.append({"role": "user", "content": user_payload})
        return messages

    def ask_stream(
        self,
        user_text: str,
        character: str = "rocky",
        filter_intensity: str = "balanced",
    ) -> Iterator[dict[str, Any]]:
        t0 = time.perf_counter()
        spec = REGISTRY.get(character)
        char_id = spec.id
        self.ensure_ollama_running()
        messages = self._build_messages(user_text, char_id)

        buffer = ""
        emitted_sentences: list[str] = []
        first_token_ms: int | None = None
        idx = 0

        with self._client.stream(
            "POST",
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": True,
                "keep_alive": "60m",
                "options": {
                    "temperature": 0.6,
                    "top_p": 0.9,
                    "num_ctx": 512,
                    "num_predict": 70,
                    "num_thread": 4,
                },
            },
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                tok = chunk.get("message", {}).get("content", "")
                if tok:
                    if first_token_ms is None:
                        first_token_ms = int((time.perf_counter() - t0) * 1000)
                    buffer += tok

                    while True:
                        popped = _pop_next_chunk(buffer, idx)
                        if not popped:
                            break
                        candidate, buffer = popped
                        ended = "🔚" in candidate
                        candidate = candidate.replace("🔚", "")
                        filtered = apply_character_filter(
                            candidate, character=char_id, intensity=filter_intensity
                        )
                        if filtered:
                            emitted_sentences.append(filtered)
                            yield {
                                "sentence": filtered,
                                "index": idx,
                                "first_token_ms": first_token_ms or 0,
                                "clause_ms": int((time.perf_counter() - t0) * 1000),
                                "end_conversation": ended,
                            }
                            idx += 1

        rem = buffer.strip()
        saw_end_marker = False
        if rem:
            ended = "🔚" in rem
            if ended:
                saw_end_marker = True
            rem = rem.replace("🔚", "")
            filtered = apply_character_filter(
                rem, character=char_id, intensity=filter_intensity
            )
            if filtered:
                emitted_sentences.append(filtered)
                yield {
                    "sentence": filtered,
                    "index": idx,
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

        hist = self.histories.setdefault(char_id, [])
        hist.append({"role": "user", "content": user_text})
        hist.append({"role": "assistant", "content": full_reply})

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
