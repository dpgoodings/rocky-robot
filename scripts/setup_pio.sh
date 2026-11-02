#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

echo "=== Setting up PlatformIO Environment for Rocky-S3 Robot ==="

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv..."
    python3 -m venv .venv
fi

echo "Installing/verifying PlatformIO Core..."
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet platformio

echo "PlatformIO is ready!"
./.venv/bin/pio --version

PORT="/dev/cu.usbmodem5D160824921"
if [ -e "$PORT" ]; then
    echo "Detected ESP32-S3 serial port at: $PORT"
else
    echo "Note: Serial port $PORT not currently detected. Plug in board when ready."
fi

echo ""
echo "Quick Commands:"
echo "  Compile firmware:      ./.venv/bin/pio run"
echo "  Upload to ESP32-S3:    ./.venv/bin/pio run -t upload"
echo "  Open Serial Monitor:   ./.venv/bin/pio device monitor"
echo "  Build & Upload:        ./.venv/bin/pio run -t upload && ./.venv/bin/pio device monitor"
