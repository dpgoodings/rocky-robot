#!/usr/bin/env python3
"""
Python Client for ESP32 Servo Autoclicker
Can be run from terminal or integrated into Rocky/automation scripts.
"""

import sys
import argparse
import requests

DEFAULT_URL = "http://autoclicker.local"

def get_status(base_url):
    try:
        r = requests.get(f"{base_url}/api/status", timeout=3)
        return r.json()
    except Exception as e:
        print(f"Error connecting to {base_url}: {e}")
        return None

def click_now(base_url):
    try:
        r = requests.post(f"{base_url}/api/click", timeout=3)
        print("Click triggered:", r.json())
    except Exception as e:
        print(f"Error: {e}")

def start_auto(base_url):
    try:
        r = requests.post(f"{base_url}/api/start", timeout=3)
        print("Autoclick started:", r.json())
    except Exception as e:
        print(f"Error: {e}")

def stop_auto(base_url):
    try:
        r = requests.post(f"{base_url}/api/stop", timeout=3)
        print("Autoclick stopped:", r.json())
    except Exception as e:
        print(f"Error: {e}")

def main():
    parser = argparse.ArgumentParser(description="Control ESP32 Servo Autoclicker")
    parser.add_argument("--url", default=DEFAULT_URL, help="Base URL of ESP32 (default: http://autoclicker.local)")
    parser.add_argument("command", choices=["status", "click", "start", "stop"], help="Command to execute")

    args = parser.parse_args()

    if args.command == "status":
        status = get_status(args.url)
        if status:
            print(f"Status: {status.get('state')} | Auto: {status.get('auto')} | Clicks: {status.get('clicks')} | Next in: {status.get('next_in')}s")
    elif args.command == "click":
        click_now(args.url)
    elif args.command == "start":
        start_auto(args.url)
    elif args.command == "stop":
        stop_auto(args.url)

if __name__ == "__main__":
    main()
