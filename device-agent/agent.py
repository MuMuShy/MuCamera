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
from datetime import datetime, timedelta
from typing import Optional, Dict, Set
from enum import Enum
import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from onvif_controller import ONVIFController, ONVIFConfig
from gps_reader import GPSManager, create_gps_manager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

AGENT_VERSION = "2.0.0-go2rtc"


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"


class Go2RTCProxyAgent:
    """Device agent that proxies HTTP requests to local go2rtc instance"""

    def __init__(
        self,
        backend_url: str,
        device_id: str,
        device_secret: Optional[str] = None,
        go2rtc_http: str = "http://127.0.0.1:1984",
        onvif_config: Optional[ONVIFConfig] = None,
        gps_manager: Optional[GPSManager] = None
    ):
        self.backend_url = backend_url
        self.device_id = device_id
        self.device_secret = device_secret
        self.go2rtc_http = go2rtc_http

        # ONVIF PTZ Controller
        self.onvif_controller: Optional[ONVIFController] = None
        if onvif_config:
            self.onvif_controller = ONVIFController(onvif_config)
            logger.info(f"[onvif] PTZ control enabled for {onvif_config.ip}")

        # GPS Manager (optional, non-blocking)
        self.gps_manager = gps_manager

        self.state = ConnectionState.DISCONNECTED
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False

        self.reconnect_base = 1
        self.max_reconnect_delay = 30
        self.reconnect_attempts = 0
        self.last_successful_connection = None

        self.heartbeat_interval = 15
        self.capabilities_interval = 30
        self.go2rtc_check_interval = 10

        self.http_url = self._get_http_url(backend_url)

        self._tasks: Set[asyncio.Task] = set()
        self._main_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._capabilities_task: Optional[asyncio.Task] = None
        self._go2rtc_health_task: Optional[asyncio.Task] = None
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._pending_proxy_tasks: Dict[str, asyncio.Task] = {}

        self._go2rtc_healthy = False
        self._last_go2rtc_check = None
        self._ws_send_lock = asyncio.Lock()

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

    async def _check_go2rtc_health(self) -> bool:
        """Check if go2rtc is accessible"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.go2rtc_http}/api/streams",
                    timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.debug(f"[go2rtc] Health check failed: {e}")
            return False

    async def go2rtc_health_monitor(self):
        """Monitor go2rtc health continuously"""
        try:
            while self.running:
                is_healthy = await self._check_go2rtc_health()

                if is_healthy != self._go2rtc_healthy:
                    if is_healthy:
                        logger.info("[go2rtc] ✓ Service is now healthy")
                    else:
                        logger.warning("[go2rtc] ✗ Service is unhealthy")

                self._go2rtc_healthy = is_healthy
                self._last_go2rtc_check = datetime.utcnow()

                await asyncio.sleep(self.go2rtc_check_interval)
        except asyncio.CancelledError:
            logger.debug("[go2rtc] Health monitor cancelled")
        except Exception as e:
            logger.error(f"[go2rtc] Health monitor error: {e}", exc_info=True)

    async def register_device(self) -> bool:
        """Register device with backend with retry"""
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                register_url = f"{self.http_url}/api/devices/register"
                payload = {
                    "device_id": self.device_id,
                    "device_name": f"go2rtc Device {self.device_id}",
                    "device_type": "camera"
                }

                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(register_url, json=payload) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            logger.info(f"✓ Device registered: {data.get('message')}")
                            return True
                        else:
                            error_text = await resp.text()
                            logger.error(f"Failed to register (attempt {attempt}/{max_attempts}): {resp.status} - {error_text}")
            except asyncio.TimeoutError:
                logger.error(f"Registration timeout (attempt {attempt}/{max_attempts})")
            except Exception as e:
                logger.error(f"Error registering device (attempt {attempt}/{max_attempts}): {e}")

            if attempt < max_attempts:
                await asyncio.sleep(2 ** attempt)

        return False

    async def _send_message_safe(self, message: dict):
        """Thread-safe message sending with queue fallback"""
        async with self._ws_send_lock:
            if self.ws and not self.ws.closed and self.state == ConnectionState.CONNECTED:
                try:
                    await self.ws.send(json.dumps(message))
                    logger.debug(f"[ws] → Sent: {message['type']}")
                    return True
                except Exception as e:
                    logger.error(f"[ws] Error sending message: {e}")
                    return False
            else:
                logger.debug(f"[ws] Queuing message (ws not ready): {message['type']}")
                if message['type'] in ['heartbeat', 'capabilities']:
                    return False
                await self._message_queue.put(message)
                return False

    async def _flush_message_queue(self):
        """Flush queued messages after reconnection"""
        flushed = 0
        while not self._message_queue.empty():
            try:
                message = self._message_queue.get_nowait()
                if await self._send_message_safe(message):
                    flushed += 1
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                logger.error(f"[ws] Error flushing message: {e}")

        if flushed > 0:
            logger.info(f"[ws] Flushed {flushed} queued messages")

    async def connect(self):
        """Connect to backend WebSocket with robust error handling"""
        while self.running:
            if self.state == ConnectionState.STOPPING:
                break

            try:
                self.state = ConnectionState.CONNECTING
                logger.info(f"[ws] Connecting to {self.backend_url}...")

                connect_timeout = min(10, 5 + self.reconnect_attempts)

                self.ws = await asyncio.wait_for(
                    websockets.connect(
                        self.backend_url,
                        ping_interval=20,
                        ping_timeout=10,
                        close_timeout=5,
                        max_size=10 * 1024 * 1024
                    ),
                    timeout=connect_timeout
                )

                hello_payload = {
                    "device_id": self.device_id,
                    "agent_version": AGENT_VERSION,
                    "go2rtc_http": self.go2rtc_http
                }
                if self.device_secret:
                    hello_payload["device_secret"] = self.device_secret

                await self.ws.send(json.dumps({
                    "type": "hello",
                    "ts": datetime.utcnow().isoformat(),
                    "payload": hello_payload
                }))

                self.state = ConnectionState.CONNECTED
                self.last_successful_connection = datetime.utcnow()
                self.reconnect_attempts = 0

                logger.info(f"[ws] ✓ Connected as device: {self.device_id}")

                self._start_background_tasks()
                await self._flush_message_queue()

                await self.message_loop()

            except asyncio.TimeoutError:
                logger.error(f"[ws] Connection timeout")
                await self._handle_disconnect()
            except (ConnectionClosed, ConnectionRefusedError, OSError, WebSocketException) as e:
                logger.error(f"[ws] Connection error: {e}")
                await self._handle_disconnect()
            except Exception as e:
                logger.error(f"[ws] Unexpected error: {e}", exc_info=True)
                await self._handle_disconnect()

            if self.running and self.state != ConnectionState.STOPPING:
                self.state = ConnectionState.RECONNECTING
                self.reconnect_attempts += 1

                delay = min(
                    self.reconnect_base * (2 ** (self.reconnect_attempts - 1)),
                    self.max_reconnect_delay
                )
                jitter = random.uniform(0, 1)
                delay = delay + jitter

                logger.info(f"[ws] ⟳ Reconnecting in {delay:.1f}s (attempt {self.reconnect_attempts})")

                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break

    def _start_background_tasks(self):
        """Start background tasks with proper cleanup"""
        self._stop_background_tasks()

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._capabilities_task = asyncio.create_task(self._capabilities_loop())

        self._tasks.add(self._heartbeat_task)
        self._tasks.add(self._capabilities_task)

    def _stop_background_tasks(self):
        """Stop all background tasks"""
        for task in [self._heartbeat_task, self._capabilities_task]:
            if task and not task.done():
                task.cancel()

        self._heartbeat_task = None
        self._capabilities_task = None

    async def message_loop(self):
        """Main message handling loop with error recovery"""
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError as e:
                    logger.error(f"[ws] Invalid JSON: {e}")
                except Exception as e:
                    logger.error(f"[ws] Error handling message: {e}", exc_info=True)
        except asyncio.CancelledError:
            logger.debug("[ws] Message loop cancelled")
        except Exception as e:
            logger.error(f"[ws] Message loop error: {e}", exc_info=True)

    async def _handle_message(self, message: dict):
        """Handle incoming WebSocket message"""
        msg_type = message.get("type")
        payload = message.get("payload", {})

        logger.debug(f"[ws] ← Received: {msg_type}")

        if msg_type == "hello_ack":
            logger.info(f"[ws] ✓ Server acknowledged connection")

        elif msg_type == "heartbeat_ack":
            logger.debug("[ws] ♥ Heartbeat acknowledged")

        elif msg_type == "proxy_http":
            task = asyncio.create_task(self._handle_proxy_http(payload))
            rid = payload.get("rid")
            if rid:
                self._pending_proxy_tasks[rid] = task
                task.add_done_callback(lambda t: self._pending_proxy_tasks.pop(rid, None))

        elif msg_type == "ptz_control":
            asyncio.create_task(self._handle_ptz_control(payload))

        # Legacy WebRTC signaling messages (ignored in proxy mode)
        elif msg_type in ["watch_request", "signal_offer", "signal_answer", "signal_ice", "watch_ended"]:
            logger.debug(f"[ws] Ignoring legacy message: {msg_type} (Backend should use proxy_http in go2rtc mode)")

        else:
            logger.warning(f"[ws] ? Unknown message type: {msg_type}")

    async def _handle_ptz_control(self, payload: dict):
        """Handle PTZ control commands"""
        rid = payload.get("rid")
        action = payload.get("action")

        logger.info(f"[ptz] Received command: {action} (rid={rid})")
        logger.info(f"[ptz] Payload: {payload}")

        if not self.onvif_controller:
            logger.error("[ptz] ONVIF controller not configured - check ONVIF_IP environment variable")
            result = {"success": False, "error": "ONVIF not configured"}
            await self._send_ptz_response(rid, result)
            return

        logger.info(f"[ptz] ONVIF controller exists, executing {action}...")

        try:
            if action == "move":
                pan = float(payload.get("pan", 0))
                tilt = float(payload.get("tilt", 0))
                zoom = float(payload.get("zoom", 0))
                duration = float(payload.get("duration", 0.5))
                result = await self.onvif_controller.move(pan, tilt, zoom, duration)

            elif action == "continuous_start":
                # Non-blocking continuous move - use stop() to stop
                pan = float(payload.get("pan", 0))
                tilt = float(payload.get("tilt", 0))
                zoom = float(payload.get("zoom", 0))
                result = await self.onvif_controller.continuous_start(pan, tilt, zoom)

            elif action == "stop":
                result = await self.onvif_controller.stop()

            elif action == "focus":
                direction = payload.get("direction", "near")
                speed = float(payload.get("speed", 0.5))
                duration = float(payload.get("duration", 0.3))
                result = await self.onvif_controller.focus(direction, speed, duration)

            elif action == "auto_focus":
                result = await self.onvif_controller.auto_focus()

            elif action == "preset":
                preset_num = int(payload.get("preset", 1))
                result = await self.onvif_controller.go_to_preset(preset_num)

            elif action == "capabilities":
                result = await self.onvif_controller.get_capabilities()

            else:
                result = {"success": False, "error": f"Unknown action: {action}"}

            logger.info(f"[ptz] Action {action} result: {result}")
            await self._send_ptz_response(rid, result)

        except Exception as e:
            logger.error(f"[ptz] Error handling command {action}: {e}", exc_info=True)
            await self._send_ptz_response(rid, {"success": False, "error": str(e)})

    async def _send_ptz_response(self, rid: str, result: dict):
        """Send PTZ control response"""
        await self._send_message_safe({
            "type": "ptz_control_resp",
            "ts": datetime.utcnow().isoformat(),
            "payload": {
                "rid": rid,
                **result
            }
        })

    async def _handle_proxy_http(self, payload: dict):
        """Handle HTTP proxy request to go2rtc"""
        rid = payload.get("rid")
        method = payload.get("method", "GET")
        path = payload.get("path", "/")
        headers = payload.get("headers", {})
        body_b64 = payload.get("body_b64")
        timeout_ms = payload.get("timeout_ms", 30000)

        logger.info(f"[proxy] {method} {path} (rid={rid})")

        if not self._go2rtc_healthy:
            logger.warning(f"[proxy] go2rtc unhealthy, attempting anyway")

        try:
            body = None
            if body_b64:
                try:
                    body = base64.b64decode(body_b64)
                except Exception as e:
                    logger.error(f"[proxy] Failed to decode body: {e}")

            url = self.go2rtc_http + path

            timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
            connector = aiohttp.TCPConnector(force_close=True, limit=10)

            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=body
                ) as resp:
                    status = resp.status
                    resp_headers = dict(resp.headers)
                    resp_body = await resp.read()

                    resp_body_b64 = base64.b64encode(resp_body).decode('utf-8')

                    response_sent = await self._send_message_safe({
                        "type": "proxy_http_resp",
                        "ts": datetime.utcnow().isoformat(),
                        "payload": {
                            "rid": rid,
                            "status": status,
                            "headers": resp_headers,
                            "body_b64": resp_body_b64
                        }
                    })

                    if response_sent:
                        logger.info(f"[proxy] {method} {path} → {status} ({len(resp_body)} bytes) ✓ Response sent")
                    else:
                        logger.error(f"[proxy] {method} {path} → {status} ({len(resp_body)} bytes) ✗ Failed to send response")

        except asyncio.TimeoutError:
            logger.error(f"[proxy] Timeout for rid={rid}")
            await self._send_message_safe({
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
            await self._send_message_safe({
                "type": "proxy_http_resp",
                "ts": datetime.utcnow().isoformat(),
                "payload": {
                    "rid": rid,
                    "status": 500,
                    "headers": {},
                    "body_b64": base64.b64encode(f"Internal Error: {str(e)}".encode()).decode('utf-8')
                }
            })

    async def _heartbeat_loop(self):
        """Send periodic heartbeats with optional GPS data"""
        try:
            while self.running and self.state == ConnectionState.CONNECTED:
                payload = {}

                # Include GPS data if available (non-blocking)
                if self.gps_manager:
                    try:
                        gps_data = self.gps_manager.get_snapshot()
                        # Only include if GPS is enabled and has meaningful data
                        if gps_data.get("enabled"):
                            payload["gps"] = gps_data
                    except Exception as e:
                        logger.debug(f"[gps] Error getting snapshot: {e}")

                await self._send_message_safe({
                    "type": "heartbeat",
                    "ts": datetime.utcnow().isoformat(),
                    "payload": payload
                })
                logger.debug(f"[ws] ♥ Heartbeat sent")
                await asyncio.sleep(self.heartbeat_interval)
        except asyncio.CancelledError:
            logger.debug("[ws] Heartbeat loop cancelled")
        except Exception as e:
            logger.error(f"[ws] Heartbeat error: {e}", exc_info=True)

    async def _capabilities_loop(self):
        """Report go2rtc stream capabilities periodically"""
        try:
            while self.running and self.state == ConnectionState.CONNECTED:
                await asyncio.sleep(self.capabilities_interval)

                if not self._go2rtc_healthy:
                    logger.debug("[go2rtc] Skipping capabilities report (unhealthy)")
                    continue

                await self._report_capabilities()
        except asyncio.CancelledError:
            logger.debug("[go2rtc] Capabilities loop cancelled")
        except Exception as e:
            logger.error(f"[go2rtc] Capabilities error: {e}", exc_info=True)

    async def _report_capabilities(self):
        """Fetch and report go2rtc streams"""
        try:
            streams_url = f"{self.go2rtc_http}/api/streams"
            timeout = aiohttp.ClientTimeout(total=5)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(streams_url) as resp:
                    if resp.status == 200:
                        streams_data = await resp.json()
                        await self._send_message_safe({
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

    async def _handle_disconnect(self):
        """Handle disconnection with proper cleanup"""
        logger.info("[ws] Handling disconnect...")

        self._stop_background_tasks()

        for rid, task in list(self._pending_proxy_tasks.items()):
            if not task.done():
                task.cancel()
        self._pending_proxy_tasks.clear()

        if self.ws:
            try:
                await asyncio.wait_for(self.ws.close(), timeout=2)
            except Exception as e:
                logger.debug(f"[ws] Error closing websocket: {e}")
            finally:
                self.ws = None

        if self.state != ConnectionState.STOPPING:
            self.state = ConnectionState.DISCONNECTED

        logger.info("[ws] Disconnect handled")

    async def start(self):
        """Start the agent"""
        self.running = True
        self.state = ConnectionState.DISCONNECTED

        logger.info(f"=== MuMu Camera Device Agent (go2rtc mode) ===")
        logger.info(f"Device ID: {self.device_id}")
        logger.info(f"Agent Version: {AGENT_VERSION}")
        logger.info(f"go2rtc HTTP: {self.go2rtc_http}")
        logger.info(f"Backend: {self.backend_url}")
        if self.onvif_controller:
            logger.info(f"ONVIF PTZ: ENABLED ({self.onvif_controller.config.ip}:{self.onvif_controller.config.port})")
            logger.info(f"ONVIF WSDL: {self.onvif_controller.config.wsdl_dir}")
        else:
            logger.info(f"ONVIF PTZ: DISABLED (set ONVIF_IP to enable)")
        if self.gps_manager and self.gps_manager.enabled:
            logger.info(f"GPS: ENABLED (device={self.gps_manager.device})")
        else:
            logger.info(f"GPS: DISABLED (set GPS_ENABLED=true to enable)")
        logger.info(f"================================================")

        # Start GPS manager if enabled
        if self.gps_manager:
            self.gps_manager.start()

        self._go2rtc_health_task = asyncio.create_task(self.go2rtc_health_monitor())

        logger.info("Registering device with backend...")
        registration_success = await self.register_device()

        if not registration_success:
            logger.warning("Device registration failed, will retry on connect")

        self._main_task = asyncio.create_task(self.connect())

        try:
            await self._main_task
        except asyncio.CancelledError:
            logger.info("Main task cancelled")

    async def stop(self):
        """Stop the agent gracefully"""
        logger.info("Stopping device agent...")
        self.running = False
        self.state = ConnectionState.STOPPING

        # Stop GPS manager
        if self.gps_manager:
            self.gps_manager.stop()

        if self._go2rtc_health_task and not self._go2rtc_health_task.done():
            self._go2rtc_health_task.cancel()
            try:
                await self._go2rtc_health_task
            except asyncio.CancelledError:
                pass

        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass

        await self._handle_disconnect()

        for task in list(self._tasks):
            if not task.done():
                task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        logger.info("Agent stopped")


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

  ONVIF PTZ Control (optional):
  ONVIF_IP        - ONVIF camera IP address
  ONVIF_PORT      - ONVIF camera port (default: 80)
  ONVIF_USER      - ONVIF camera username (default: admin)
  ONVIF_PASS      - ONVIF camera password
  ONVIF_WSDL_DIR  - ONVIF WSDL directory (default: /opt/mumucam/onvif/wsdl)

  GPS (optional):
  GPS_ENABLED     - Enable GPS reader (default: false)
  GPS_DEVICE      - GPS serial device (default: /dev/sim7600-gps)
  GPS_BAUDRATE    - GPS baud rate (default: 115200)

Examples:
  # Run with default settings
  DEVICE_ID=pi-cam-001 python agent.py

  # Run with custom backend
  BACKEND_URL=wss://myserver.com/ws/device DEVICE_ID=pi-cam-001 python agent.py

  # Run with ONVIF PTZ control
  DEVICE_ID=pi-cam-001 ONVIF_IP=192.168.1.100 ONVIF_USER=admin ONVIF_PASS=pass123 python agent.py
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
        "--onvif-ip",
        default=os.getenv("ONVIF_IP"),
        help="ONVIF camera IP address for PTZ control"
    )
    parser.add_argument(
        "--onvif-port",
        type=int,
        default=int(os.getenv("ONVIF_PORT", "80")),
        help="ONVIF camera port"
    )
    parser.add_argument(
        "--onvif-user",
        default=os.getenv("ONVIF_USER", "admin"),
        help="ONVIF camera username"
    )
    parser.add_argument(
        "--onvif-pass",
        default=os.getenv("ONVIF_PASS", ""),
        help="ONVIF camera password"
    )
    parser.add_argument(
        "--onvif-wsdl-dir",
        default=os.getenv("ONVIF_WSDL_DIR", "/opt/mumucam/onvif/wsdl"),
        help="ONVIF WSDL directory"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    args = parser.parse_args()

    if not args.device_id:
        parser.error("--device-id or DEVICE_ID environment variable is required")

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Create ONVIF config if IP is provided
    onvif_config = None
    if args.onvif_ip:
        onvif_config = ONVIFConfig(
            ip=args.onvif_ip,
            port=args.onvif_port,
            username=args.onvif_user,
            password=args.onvif_pass,
            wsdl_dir=args.onvif_wsdl_dir
        )

    # Create GPS manager from environment variables
    gps_manager = create_gps_manager()

    agent = Go2RTCProxyAgent(
        backend_url=args.backend,
        device_id=args.device_id,
        device_secret=args.device_secret,
        go2rtc_http=args.go2rtc_http,
        onvif_config=onvif_config,
        gps_manager=gps_manager
    )

    shutdown_event = asyncio.Event()

    def signal_handler(sig):
        logger.info(f"Received signal {sig}")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

    agent_task = asyncio.create_task(agent.start())

    try:
        await shutdown_event.wait()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    finally:
        await agent.stop()

        if not agent_task.done():
            agent_task.cancel()
            try:
                await agent_task
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Exiting...")
