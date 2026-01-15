#!/usr/bin/env python3
"""
Test script for ONVIF PTZ control
Run on Raspberry Pi: python3 test_ptz.py
"""

import asyncio
from onvif_controller import ONVIFController, ONVIFConfig

# Camera settings - modify these
CAM_IP = "10.10.0.48"
CAM_PORT = 80
CAM_USER = "admin"
CAM_PASS = "123456"
# WSDL location - found on your Pi
WSDL_DIR = "/opt/mumucam/venv/lib/python3.4/site-packages/wsdl"


async def main():
    print(f"Testing ONVIF PTZ control for {CAM_IP}...")
    print(f"WSDL directory: {WSDL_DIR}")

    config = ONVIFConfig(
        ip=CAM_IP,
        port=CAM_PORT,
        username=CAM_USER,
        password=CAM_PASS,
        wsdl_dir=WSDL_DIR
    )

    ctrl = ONVIFController(config)

    # Test initialization
    print("\n1. Initializing ONVIF connection...")
    success = await ctrl.initialize()
    if not success:
        print("   FAILED - Check camera IP, credentials, and WSDL path")
        return
    print("   OK")

    # Test capabilities
    print("\n2. Getting capabilities...")
    caps = await ctrl.get_capabilities()
    print(f"   {caps}")

    # Test PTZ move
    print("\n3. Testing PTZ move (pan right)...")
    result = await ctrl.move(pan=0.3, tilt=0, zoom=0, duration=1)
    print(f"   {result}")

    await asyncio.sleep(1)

    print("\n4. Testing PTZ move (pan left)...")
    result = await ctrl.move(pan=-0.3, tilt=0, zoom=0, duration=1)
    print(f"   {result}")

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
