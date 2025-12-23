#!/usr/bin/env python3
"""
MuMu Camera Device Agent - go2rtc Proxy Mode

This agent runs on Raspberry Pi alongside go2rtc and:
1. Registers device with backend
2. Maintains WebSocket connection
3. Reports go2rtc stream capabilities
4. Proxies HTTP requests to local go2rtc instance
"""

import asyncio
import json
import logging
import argparse
import signal
import sys
import os
import base64
import random
from datetime import datetime
from typing import Optional, Dict
import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Agent version
AGENT_VERSION = "2.0.0-go2rtc"


class Go2RTCProxyAgent:
    """Device agent that proxies HTTP requests to local go2rtc instance"""

    def __init__(
        self,
        backend_url: str,
        device_id: str,
        device_secret: Optional[str] = None,
        go2rtc_http: str = "http://127.0.0.1:1984"
    ):
        self.backend_url = backend_url
        self.device_id = device_id
        self.device_secret = device_secret
        self.go2rtc_http = go2rtc_http
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False

        # Reconnection settings
        self.reconnect_base = 1  # 1 second
        self.max_reconnect_delay = 30  # 30 seconds max
        self.reconnect_attempts = 0

        # Tasks
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.capabilities_task: Optional[asyncio.Task] = None
        self.heartbeat_interval = 15  # seconds
        self.capabilities_interval = 30  # seconds

        # HTTP API URL
        self.http_url = self._get_http_url(backend_url)

        # Proxy request tracking
        self.proxy_sessions: Dict[str, asyncio.Task] = {}

        logger.info(f"[go2rtc] Agent initialized: {device_id}, go2rtc: {go2rtc_http}")

    def _get_http_url(self, ws_url: str) -> str:
        """Convert WebSocket URL to HTTP URL"""
        if ws_url.startswith("ws://"):
            http_url = ws_url.replace("ws://", "http://")
        elif ws_url.startswith("wss://"):
            http_url = ws_url.replace("wss://", "https://")
        else:
            http_url = ws_url

        if "/ws/device" in http_url:
            http_url = http_url.replace("/ws/device", "")

        return http_url

    async def register_device(self):
        """Register device with backend"""
        try:
            register_url = f"{self.http_url}/api/devices/register"
            payload = {
                "device_id": self.device_id,
                "device_name": f"go2rtc Device {self.device_id}",
                "device_type": "camera"
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(register_url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        logger.info(f"✓ Device registered: {data.get('message')}")
                        return True
                    else:
                        error_text = await resp.text()
                        logger.error(f"Failed to register: {resp.status} - {error_text}")
                        return False
        except Exception as e:
            logger.error(f"Error registering device: {e}")
            return False

    async def connect(self):
        """Connect to backend WebSocket with exponential backoff"""
        while self.running:
            try:
                logger.info(f"[ws] Connecting to {self.backend_url}...")
                self.ws = await websockets.connect(
                    self.backend_url,
                    ping_interval=20,
                    ping_timeout=10
                )

                # Send hello message
                hello_payload = {
                    "device_id": self.device_id,
                    "agent_version": AGENT_VERSION,
                    "go2rtc_http": self.go2rtc_http
                }
                if self.device_secret:
                    hello_payload["device_secret"] = self.device_secret

                await self.send_message({
                    "type": "hello",
                    "ts": datetime.utcnow().isoformat(),
                    "payload": hello_payload
                })

                logger.info(f"[ws] ✓ Connected as device: {self.device_id}")

                # Reset reconnection on success
                self.reconnect_attempts = 0

                # Start background tasks
                self._start_background_tasks()

                # Handle messages
                await self.message_loop()

            except (ConnectionClosed, ConnectionRefusedError, OSError) as e:
                logger.error(f"[ws] Connection error: {e}")
                await self.handle_disconnect()

                if self.running:
                    self.reconnect_attempts += 1
                    # Exponential backoff with jitter
                    delay = min(
                        self.reconnect_base * (2 ** (self.reconnect_attempts - 1)),
                        self.max_reconnect_delay
                    )
                    jitter = random.uniform(0, 1)
                    delay = delay + jitter
                    logger.info(f"[ws] ⟳ Reconnecting in {delay:.1f}s (attempt {self.reconnect_attempts})")
                    await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"[ws] Unexpected error: {e}", exc_info=True)
                await asyncio.sleep(5)

    def _start_background_tasks(self):
        """Start heartbeat and capabilities reporting tasks"""
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
        if self.capabilities_task:
            self.capabilities_task.cancel()

        self.heartbeat_task = asyncio.create_task(self.heartbeat_loop())
        self.capabilities_task = asyncio.create_task(self.capabilities_loop())

    async def send_message(self, message: dict):
        """Send message via WebSocket"""
        if self.ws and not self.ws.closed:
            try:
                await self.ws.send(json.dumps(message))
                logger.debug(f"[ws] → Sent: {message['type']}")
            except Exception as e:
                logger.error(f"[ws] Error sending message: {e}")

    async def message_loop(self):
        """Main message handling loop"""
        async for message in self.ws:
            try:
                data = json.loads(message)
                await self.handle_message(data)
            except json.JSONDecodeError as e:
                logger.error(f"[ws] Invalid JSON: {e}")
            except Exception as e:
                logger.error(f"[ws] Error handling message: {e}", exc_info=True)

    async def handle_message(self, message: dict):
        """Handle incoming WebSocket message"""
        msg_type = message.get("type")
        payload = message.get("payload", {})

        logger.debug(f"[ws] ← Received: {msg_type}")

        if msg_type == "hello_ack":
            logger.info(f"[ws] ✓ Server acknowledged connection")

        elif msg_type == "heartbeat_ack":
            logger.debug("[ws] ♥ Heartbeat acknowledged")

        elif msg_type == "proxy_http":
            # HTTP proxy request
            asyncio.create_task(self.handle_proxy_http(payload))

        else:
            logger.warning(f"[ws] ? Unknown message type: {msg_type}")

    async def handle_proxy_http(self, payload: dict):
        """Handle HTTP proxy request to go2rtc"""
        rid = payload.get("rid")
        method = payload.get("method", "GET")
        path = payload.get("path", "/")
        headers = payload.get("headers", {})
        body_b64 = payload.get("body_b64")
        timeout_ms = payload.get("timeout_ms", 30000)

        logger.info(f"[proxy] {method} {path} (rid={rid})")

        try:
            # Decode body if present
            body = None
            if body_b64:
                try:
                    body = base64.b64decode(body_b64)
                except Exception as e:
                    logger.error(f"[proxy] Failed to decode body: {e}")

            # Build full URL
            url = self.go2rtc_http + path

            # Make request to go2rtc
            timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=body
                ) as resp:
                    status = resp.status
                    resp_headers = dict(resp.headers)
                    resp_body = await resp.read()

                    # Encode response body
                    resp_body_b64 = base64.b64encode(resp_body).decode('utf-8')

                    # Send response back
                    await self.send_message({
                        "type": "proxy_http_resp",
                        "ts": datetime.utcnow().isoformat(),
                        "payload": {
                            "rid": rid,
                            "status": status,
                            "headers": resp_headers,
                            "body_b64": resp_body_b64
                        }
                    })

                    logger.info(f"[proxy] {method} {path} → {status} ({len(resp_body)} bytes)")

        except asyncio.TimeoutError:
            logger.error(f"[proxy] Timeout for rid={rid}")
            await self.send_message({
                "type": "proxy_http_resp",
                "ts": datetime.utcnow().isoformat(),
                "payload": {
                    "rid": rid,
                    "status": 504,
                    "headers": {},
                    "body_b64": base64.b64encode(b"Gateway Timeout").decode('utf-8')
                }
            })
        except Exception as e:
            logger.error(f"[proxy] Error handling request: {e}", exc_info=True)
            await self.send_message({
                "type": "proxy_http_resp",
                "ts": datetime.utcnow().isoformat(),
                "payload": {
                    "rid": rid,
                    "status": 500,
                    "headers": {},
                    "body_b64": base64.b64encode(f"Internal Error: {str(e)}".encode()).decode('utf-8')
                }
            })

    async def heartbeat_loop(self):
        """Send periodic heartbeats"""
        try:
            while self.running and self.ws and not self.ws.closed:
                await self.send_message({
                    "type": "heartbeat",
                    "ts": datetime.utcnow().isoformat(),
                    "payload": {}
                })
                logger.debug(f"[ws] ♥ Heartbeat sent")
                await asyncio.sleep(self.heartbeat_interval)
        except asyncio.CancelledError:
            logger.debug("[ws] Heartbeat loop cancelled")
        except Exception as e:
            logger.error(f"[ws] Heartbeat error: {e}")

    async def capabilities_loop(self):
        """Report go2rtc stream capabilities periodically"""
        try:
            while self.running and self.ws and not self.ws.closed:
                await asyncio.sleep(self.capabilities_interval)
                await self.report_capabilities()
        except asyncio.CancelledError:
            logger.debug("[go2rtc] Capabilities loop cancelled")
        except Exception as e:
            logger.error(f"[go2rtc] Capabilities error: {e}")

    async def report_capabilities(self):
        """Fetch and report go2rtc streams"""
        try:
            streams_url = f"{self.go2rtc_http}/api/streams"
            async with aiohttp.ClientSession() as session:
                async with session.get(streams_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        streams_data = await resp.json()
                        await self.send_message({
                            "type": "capabilities",
                            "ts": datetime.utcnow().isoformat(),
                            "payload": {
                                "device_id": self.device_id,
                                "streams": streams_data
                            }
                        })
                        logger.debug(f"[go2rtc] ✓ Reported capabilities ({len(streams_data)} streams)")
                    else:
                        logger.warning(f"[go2rtc] Failed to fetch streams: {resp.status}")
        except Exception as e:
            logger.error(f"[go2rtc] Error fetching streams: {e}")

    async def handle_disconnect(self):
        """Handle disconnection - clean up resources"""
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            self.heartbeat_task = None

        if self.capabilities_task:
            self.capabilities_task.cancel()
            self.capabilities_task = None

        # Cancel all proxy sessions
        for task in self.proxy_sessions.values():
            task.cancel()
        self.proxy_sessions.clear()

        if self.ws:
            await self.ws.close()
            self.ws = None

        logger.info("[ws] Disconnected from backend")

    async def start(self):
        """Start the agent"""
        self.running = True
        logger.info(f"=== MuMu Camera Device Agent (go2rtc mode) ===")
        logger.info(f"Device ID: {self.device_id}")
        logger.info(f"Agent Version: {AGENT_VERSION}")
        logger.info(f"go2rtc HTTP: {self.go2rtc_http}")
        logger.info(f"Backend: {self.backend_url}")
        logger.info(f"================================================")

        # Register device
        logger.info("Registering device with backend...")
        await self.register_device()

        # Connect to WebSocket
        await self.connect()

    async def stop(self):
        """Stop the agent"""
        logger.info("Stopping device agent...")
        self.running = False
        await self.handle_disconnect()


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="MuMu Camera Device Agent - go2rtc Proxy Mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  BACKEND_URL     - WebSocket backend URL (default: ws://localhost:8000/ws/device)
  DEVICE_ID       - Unique device identifier (required)
  DEVICE_SECRET   - Optional device authentication secret
  GO2RTC_HTTP     - go2rtc HTTP API URL (default: http://127.0.0.1:1984)

Examples:
  # Run with default settings
  DEVICE_ID=pi-cam-001 python agent.py

  # Run with custom backend
  BACKEND_URL=wss://myserver.com/ws/device DEVICE_ID=pi-cam-001 python agent.py

  # Run with device secret
  DEVICE_ID=pi-cam-001 DEVICE_SECRET=mysecret123 python agent.py
        """
    )
    parser.add_argument(
        "--backend",
        default=os.getenv("BACKEND_URL", "ws://localhost:8000/ws/device"),
        help="Backend WebSocket URL"
    )
    parser.add_argument(
        "--device-id",
        default=os.getenv("DEVICE_ID"),
        help="Unique device identifier (required)"
    )
    parser.add_argument(
        "--device-secret",
        default=os.getenv("DEVICE_SECRET"),
        help="Device authentication secret"
    )
    parser.add_argument(
        "--go2rtc-http",
        default=os.getenv("GO2RTC_HTTP", "http://127.0.0.1:1984"),
        help="go2rtc HTTP API URL"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    args = parser.parse_args()

    # Validate required arguments
    if not args.device_id:
        parser.error("--device-id or DEVICE_ID environment variable is required")

    # Set log level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Create agent
    agent = Go2RTCProxyAgent(
        backend_url=args.backend,
        device_id=args.device_id,
        device_secret=args.device_secret,
        go2rtc_http=args.go2rtc_http
    )

    # Handle shutdown signals
    loop = asyncio.get_event_loop()

    def signal_handler(sig):
        logger.info("Received shutdown signal")
        asyncio.create_task(agent.stop())
        loop.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

    # Start agent
    try:
        await agent.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    finally:
        await agent.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Exiting...")
