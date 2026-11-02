#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "=================================================="
echo "  HAIL MARY TRANSLATOR — ROCKY VOICE ASSISTANT"
echo "=================================================="

# Ensure Ollama local daemon is running
if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "[1/2] Starting local Ollama brain..."
  OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_KEEP_ALIVE=30m ./bin/ollama serve > ollama.log 2>&1 &
  sleep 2
fi

echo "[2/2] Starting Rocky Voice Server at http://127.0.0.1:7860 ..."
(sleep 2 && open "http://127.0.0.1:7860") &
exec ./.venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 7860
