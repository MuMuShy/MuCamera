#!/usr/bin/env python3
"""
MuMu Camera Pairing Tool

Generate a pairing code for this device.
The code can be entered in the web interface to bind this device to a user account.

Usage:
  python pair.py
  python pair.py --device-id my-camera
  DEVICE_ID=my-camera python pair.py

Environment Variables:
  BACKEND_URL  - Backend HTTP URL (default: http://localhost:8000)
  DEVICE_ID    - Device identifier (required)
"""

import argparse
import os
import sys
import requests
from datetime import datetime


def get_backend_url(ws_url: str) -> str:
    """Convert WebSocket URL to HTTP URL if needed"""
    if ws_url.startswith("ws://"):
        http_url = ws_url.replace("ws://", "http://")
    elif ws_url.startswith("wss://"):
        http_url = ws_url.replace("wss://", "https://")
    else:
        http_url = ws_url

    # Remove WebSocket path if present
    if "/ws/device" in http_url:
        http_url = http_url.replace("/ws/device", "")

    return http_url


def generate_pairing_code(backend_url: str, device_id: str) -> dict:
    """Call backend API to generate pairing code"""
    url = f"{backend_url}/api/pairing/generate"
    params = {"device_id": device_id}

    try:
        response = requests.post(url, params=params, timeout=10)

        if response.status_code == 200:
            return {"success": True, "data": response.json()}
        elif response.status_code == 404:
            return {
                "success": False,
                "error": f"Device '{device_id}' not found. Make sure the device agent has registered first."
            }
        else:
            return {
                "success": False,
                "error": f"API error: {response.status_code} - {response.text}"
            }

    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": f"Cannot connect to backend at {backend_url}. Is the server running?"
        }
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": "Request timeout. Backend server may be overloaded."
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Unexpected error: {e}"
        }


def main():
    parser = argparse.ArgumentParser(
        description="Generate a pairing code for MuMu Camera device",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use environment variables
  export DEVICE_ID=pi-cam-001
  export BACKEND_URL=http://api.example.com
  python pair.py

  # Use command line arguments
  python pair.py --device-id pi-cam-001 --backend http://api.example.com

  # Quick one-liner
  DEVICE_ID=pi-cam-001 python pair.py
        """
    )

    parser.add_argument(
        "--backend",
        default=os.getenv("BACKEND_URL", "http://localhost:8000"),
        help="Backend HTTP URL (default: http://localhost:8000)"
    )

    parser.add_argument(
        "--device-id",
        default=os.getenv("DEVICE_ID"),
        help="Device identifier (required)"
    )

    args = parser.parse_args()

    # Validate device_id
    if not args.device_id:
        print("Error: --device-id or DEVICE_ID environment variable is required")
        print()
        print("Example:")
        print("  python pair.py --device-id my-camera-001")
        print("  DEVICE_ID=my-camera-001 python pair.py")
        sys.exit(1)

    # Convert WebSocket URL to HTTP if needed
    backend_url = get_backend_url(args.backend)

    print()
    print("=" * 50)
    print("  MuMu Camera Pairing")
    print("=" * 50)
    print(f"  Device ID: {args.device_id}")
    print(f"  Backend:   {backend_url}")
    print("=" * 50)
    print()

    # Generate pairing code
    print("Generating pairing code...")
    result = generate_pairing_code(backend_url, args.device_id)

    if result["success"]:
        data = result["data"]
        code = data["code"]
        ttl = data["ttl"]
        expires_at = data["expires_at"]

        print()
        print("+" + "-" * 30 + "+")
        print("|" + " " * 30 + "|")
        print("|" + f"  PAIRING CODE: {code}".center(28) + "|")
        print("|" + " " * 30 + "|")
        print("+" + "-" * 30 + "+")
        print()
        print(f"  Valid for {ttl // 60} minutes")
        print(f"  Expires at: {expires_at}")
        print()
        print("  Enter this code in the web interface to pair this device.")
        print()

    else:
        print()
        print(f"Error: {result['error']}")
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()
