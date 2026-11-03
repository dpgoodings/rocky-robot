#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
echo "Starting Rocky-S3 Robot Brain & Cyber-HUD..."
exec "$DIR/voice/start_rocky.sh"
