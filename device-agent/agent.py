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
import socket
import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from onvif_controller import ONVIFController, ONVIFConfig
from gps_reader import GPSManager, create_gps_manager
from system_monitor import SystemMonitor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

AGENT_VERSION = "2.1.0-go2rtc"


class FFmpegRTMPPusher:
    """Manages FFmpeg subprocess that pushes RTSP stream to LiveKit via RTMP."""

    def __init__(self, go2rtc_http: str = "http://127.0.0.1:8554"):
        self._process: Optional[asyncio.subprocess.Process] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._rtmp_url: Optional[str] = None
        self._stream_key: Optional[str] = None
        self._stream_src: str = "cam_sub"
        self._running = False
        # Derive RTSP base from go2rtc HTTP (go2rtc serves RTSP on port 8554)
        self._rtsp_base = "rtsp://127.0.0.1:8554"

    @property
    def rtsp_url(self) -> str:
        return f"{self._rtsp_base}/{self._stream_src}"

    @property
    def full_rtmp_url(self) -> str:
        if self._rtmp_url and self._stream_key:
            return f"{self._rtmp_url}/{self._stream_key}"
        return ""

    async def start(self, rtmp_url: str, stream_key: str, stream_src: str = "cam_sub"):
        """Start FFmpeg RTMP push."""
        self._rtmp_url = rtmp_url
        self._stream_key = stream_key
        self._stream_src = stream_src
        self._running = True
        await self._start_ffmpeg()
        self._monitor_task = asyncio.create_task(self._crash_monitor())

    async def stop(self):
        """Stop FFmpeg and crash monitor."""
        self._running = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        await self._kill_ffmpeg()

    async def switch_stream(self, stream_src: str):
        """Switch to a different RTSP source (restarts FFmpeg)."""
        if stream_src == self._stream_src:
            return
        logger.info(f"[ffmpeg] Switching stream: {self._stream_src} -> {stream_src}")
        self._stream_src = stream_src
        await self._kill_ffmpeg()
        if self._running:
            await self._start_ffmpeg()

    async def _start_ffmpeg(self):
        """Launch FFmpeg subprocess."""
        if not self.full_rtmp_url:
            logger.error("[ffmpeg] No RTMP URL configured")
            return

        cmd = [
            "ffmpeg",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-rtsp_transport", "tcp",
            "-i", self.rtsp_url,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-f", "flv",
            "-flvflags", "no_duration_filesize",
            self.full_rtmp_url,
        ]
        logger.info(f"[ffmpeg] Starting: {' '.join(cmd)}")
        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info(f"[ffmpeg] Started PID={self._process.pid}")
        except FileNotFoundError:
            logger.error("[ffmpeg] ffmpeg binary not found - is it installed?")
        except Exception as e:
            logger.error(f"[ffmpeg] Failed to start: {e}")

    async def _kill_ffmpeg(self):
        """Terminate FFmpeg subprocess."""
        if self._process and self._process.returncode is None:
            logger.info(f"[ffmpeg] Stopping PID={self._process.pid}")
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            except Exception as e:
                logger.error(f"[ffmpeg] Error stopping: {e}")
            self._process = None

    async def _crash_monitor(self):
        """Monitor FFmpeg and restart on crash."""
        try:
            while self._running:
                if self._process:
                    retcode = await self._process.wait()
                    if self._running:
                        logger.warning(f"[ffmpeg] Process exited with code {retcode}, restarting in 3s...")
                        await asyncio.sleep(3)
                        if self._running:
                            await self._start_ffmpeg()
                else:
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.debug("[ffmpeg] Crash monitor cancelled")


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
        playback_http: str = "http://127.0.0.1:8090",
        onvif_config: Optional[ONVIFConfig] = None,
        gps_manager: Optional[GPSManager] = None
    ):
        self.backend_url = backend_url
        self.device_id = device_id
        self.device_secret = device_secret
        self.go2rtc_http = go2rtc_http
        self.playback_http = playback_http

        # ONVIF PTZ Controllers (multi-camera support)
        self.onvif_controllers: dict[str, ONVIFController] = {}
        self.onvif_controller: Optional[ONVIFController] = None  # 向後相容
        if onvif_config:
            self.onvif_controllers["cam"] = ONVIFController(onvif_config)
            self.onvif_controller = self.onvif_controllers["cam"]
            logger.info(f"[onvif] PTZ control enabled for cam → {onvif_config.ip}")

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
        self.sysinfo_interval = 10

        self.http_url = self._get_http_url(backend_url)

        self._tasks: Set[asyncio.Task] = set()
        self._main_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._capabilities_task: Optional[asyncio.Task] = None
        self._go2rtc_health_task: Optional[asyncio.Task] = None
        self._sysinfo_task: Optional[asyncio.Task] = None
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._pending_proxy_tasks: Dict[str, asyncio.Task] = {}

        self._go2rtc_healthy = False
        self._last_go2rtc_check = None
        self._ws_send_lock = asyncio.Lock()
        self._time_sync_task: Optional[asyncio.Task] = None
        self.time_sync_interval = 3600  # 每小時同步一次

        # System monitor
        self._system_monitor = SystemMonitor()

        # LiveKit RTMP pusher (activated on server command)
        self._rtmp_pusher = FFmpegRTMPPusher(go2rtc_http)

        # RTSP watchdog state (per-camera)
        self._watchdog_fail_counts: Dict[str, int] = {}
        self._watchdog_last_reboot: Dict[str, datetime] = {}
        self._watchdog_recovering: Dict[str, bool] = {}
        self._watchdog_task: Optional[asyncio.Task] = None

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
                        ping_interval=10,
                        ping_timeout=30,
                        close_timeout=10,
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
        self._sysinfo_task = asyncio.create_task(self._sysinfo_loop())

        self._tasks.add(self._heartbeat_task)
        self._tasks.add(self._capabilities_task)
        self._tasks.add(self._sysinfo_task)

    def _stop_background_tasks(self):
        """Stop all background tasks"""
        for task in [self._heartbeat_task, self._capabilities_task, self._sysinfo_task]:
            if task and not task.done():
                task.cancel()

        self._heartbeat_task = None
        self._capabilities_task = None
        self._sysinfo_task = None

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

        elif msg_type == "control_command":
            asyncio.create_task(self._handle_control_command(payload))

        elif msg_type == "proxy_playback":
            # Proxy to local playback API (port 8090)
            task = asyncio.create_task(self._handle_proxy_playback(payload))
            rid = payload.get("rid")
            if rid:
                self._pending_proxy_tasks[rid] = task
                task.add_done_callback(lambda t: self._pending_proxy_tasks.pop(rid, None))

        elif msg_type == "livekit_ingress":
            # Server tells us to push RTMP to LiveKit
            rtmp_url = payload.get("rtmp_url")
            stream_key = payload.get("stream_key")
            if rtmp_url and stream_key:
                logger.info(f"[livekit] Received ingress info, starting RTMP push")
                await self._rtmp_pusher.start(rtmp_url, stream_key, "cam_sub")
            else:
                logger.error("[livekit] Missing rtmp_url or stream_key in livekit_ingress")

        elif msg_type == "livekit_switch_stream":
            # Server tells us to switch the RTSP source for the RTMP push
            stream_src = payload.get("stream_src", "cam_sub")
            logger.info(f"[livekit] Switch stream request: {stream_src}")
            await self._rtmp_pusher.switch_stream(stream_src)

        # Legacy WebRTC signaling messages (ignored in proxy mode)
        elif msg_type in ["watch_request", "signal_offer", "signal_answer", "signal_ice", "watch_ended"]:
            logger.debug(f"[ws] Ignoring legacy message: {msg_type} (Backend should use proxy_http in go2rtc mode)")

        else:
            logger.warning(f"[ws] ? Unknown message type: {msg_type}")

    async def _handle_ptz_control(self, payload: dict):
        """Handle PTZ control commands"""
        rid = payload.get("rid")
        action = payload.get("action")
        source = payload.get("source", "cam")

        logger.info(f"[ptz] Received command: {action} source={source} (rid={rid})")
        logger.info(f"[ptz] Payload: {payload}")

        # 選擇對應的 ONVIF controller
        controller = self.onvif_controllers.get(source, self.onvif_controller)
        if not controller:
            logger.error(f"[ptz] ONVIF controller not configured for {source}")
            result = {"success": False, "error": f"ONVIF not configured for {source}"}
            await self._send_ptz_response(rid, result)
            return

        logger.info(f"[ptz] Using ONVIF controller for {source}, executing {action}...")

        try:
            if action == "move":
                pan = float(payload.get("pan", 0))
                tilt = float(payload.get("tilt", 0))
                zoom = float(payload.get("zoom", 0))
                duration = float(payload.get("duration", 0.5))
                result = await controller.move(pan, tilt, zoom, duration)

            elif action == "continuous_start":
                # Non-blocking continuous move - use stop() to stop
                pan = float(payload.get("pan", 0))
                tilt = float(payload.get("tilt", 0))
                zoom = float(payload.get("zoom", 0))
                result = await controller.continuous_start(pan, tilt, zoom)

            elif action == "stop":
                result = await controller.stop()

            elif action == "focus":
                direction = payload.get("direction", "near")
                speed = float(payload.get("speed", 0.5))
                duration = float(payload.get("duration", 0.3))
                result = await controller.focus(direction, speed, duration)

            elif action == "auto_focus":
                result = await controller.auto_focus()

            elif action == "preset":
                preset_num = int(payload.get("preset", 1))
                result = await controller.go_to_preset(preset_num)

            elif action == "capabilities":
                result = await controller.get_capabilities()

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

    async def _handle_control_command(self, payload: dict):
        """Handle control commands from backend"""
        rid = payload.get("rid")
        command = payload.get("command")
        logger.info(f"[control] Received command: {command} (rid={rid})")

        try:
            if command == "restart_go2rtc":
                # Send response first, then restart
                await self._send_control_response(rid, True, "Restarting go2rtc...")
                await asyncio.sleep(0.5)
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "systemctl", "restart", "go2rtc",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.wait()
                logger.info(f"[control] go2rtc restart exit code: {proc.returncode}")

            elif command == "restart_stream":
                await self._send_control_response(rid, True, "Restarting stream...")
                await asyncio.sleep(0.5)
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "systemctl", "restart", "go2rtc",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.wait()
                logger.info(f"[control] stream restart exit code: {proc.returncode}")

            elif command == "restart_agent":
                # Send response first, then exit (systemd will restart)
                await self._send_control_response(rid, True, "Restarting agent...")
                await asyncio.sleep(1)
                logger.info("[control] Agent exiting for restart...")
                os._exit(0)

            elif command == "reboot":
                # Send response first, then reboot
                await self._send_control_response(rid, True, "Rebooting device...")
                await asyncio.sleep(1)
                logger.info("[control] Rebooting device...")
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "reboot",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.wait()

            elif command == "reboot_camera":
                source = payload.get("source", "cam")
                controller = self.onvif_controllers.get(source)
                if not controller:
                    await self._send_control_response(rid, False, f"ONVIF not configured for {source}")
                    return
                await self._send_control_response(rid, True, f"Rebooting camera {source}...")
                # 觸發完整 recovery（reboot → 等回來 → 同步時間 → 重啟 go2rtc）
                asyncio.create_task(self._recovery_sequence(source))

            else:
                await self._send_control_response(rid, False, f"Unknown command: {command}")

        except Exception as e:
            logger.error(f"[control] Error executing {command}: {e}", exc_info=True)
            await self._send_control_response(rid, False, str(e))

    async def _send_control_response(self, rid: str, success: bool, message: str):
        """Send control command response"""
        await self._send_message_safe({
            "type": "control_resp",
            "ts": datetime.utcnow().isoformat(),
            "payload": {
                "rid": rid,
                "success": success,
                "message": message
            }
        })

    # -------- RTSP Watchdog --------

    def _get_camera_rtsp_target(self, source: str) -> Optional[tuple]:
        """Get (ip, port) for a camera source's RTSP endpoint"""
        controller = self.onvif_controllers.get(source)
        if not controller:
            return None
        return (controller.config.ip, 554)

    async def _tcp_probe(self, host: str, port: int, timeout: float = 5.0) -> bool:
        """TCP connect probe to check if a port is reachable"""
        try:
            loop = asyncio.get_event_loop()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = await loop.run_in_executor(None, sock.connect_ex, (host, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    async def _rtsp_watchdog(self):
        """Background task: periodically probe camera RTSP ports and trigger recovery"""
        PROBE_INTERVAL = 30
        FAIL_THRESHOLD = 3
        COOLDOWN_SECONDS = 300  # 5 minutes

        try:
            # Wait for initial startup before starting watchdog
            await asyncio.sleep(60)
            logger.info("[watchdog] RTSP watchdog started")

            while self.running:
                for source, controller in self.onvif_controllers.items():
                    target = self._get_camera_rtsp_target(source)
                    if not target:
                        continue

                    host, port = target

                    # Skip if currently recovering
                    if self._watchdog_recovering.get(source, False):
                        continue

                    reachable = await self._tcp_probe(host, port)

                    if reachable:
                        if self._watchdog_fail_counts.get(source, 0) > 0:
                            logger.info(f"[watchdog] {source} ({host}:{port}) recovered, resetting fail count")
                        self._watchdog_fail_counts[source] = 0
                    else:
                        count = self._watchdog_fail_counts.get(source, 0) + 1
                        self._watchdog_fail_counts[source] = count
                        logger.warning(f"[watchdog] {source} ({host}:{port}) probe failed ({count}/{FAIL_THRESHOLD})")

                        if count >= FAIL_THRESHOLD:
                            # Check cooldown
                            last_reboot = self._watchdog_last_reboot.get(source)
                            if last_reboot and (datetime.utcnow() - last_reboot).total_seconds() < COOLDOWN_SECONDS:
                                remaining = COOLDOWN_SECONDS - (datetime.utcnow() - last_reboot).total_seconds()
                                logger.warning(f"[watchdog] {source} cooldown active, {remaining:.0f}s remaining")
                                continue

                            logger.error(f"[watchdog] {source} RTSP unreachable for {count * PROBE_INTERVAL}s, starting recovery")
                            self._watchdog_fail_counts[source] = 0
                            asyncio.create_task(self._recovery_sequence(source))

                await asyncio.sleep(PROBE_INTERVAL)

        except asyncio.CancelledError:
            logger.debug("[watchdog] RTSP watchdog cancelled")
        except Exception as e:
            logger.error(f"[watchdog] RTSP watchdog error: {e}", exc_info=True)

    async def _recovery_sequence(self, source: str):
        """Recovery sequence: ONVIF reboot → wait → restart go2rtc"""
        self._watchdog_recovering[source] = True
        self._watchdog_last_reboot[source] = datetime.utcnow()

        controller = self.onvif_controllers.get(source)
        if not controller:
            self._watchdog_recovering[source] = False
            return

        try:
            # Step 1: ONVIF reboot
            logger.info(f"[watchdog] Step 1: Rebooting camera {source} via ONVIF")
            result = await controller.reboot()
            if not result.get("success"):
                logger.error(f"[watchdog] ONVIF reboot failed for {source}: {result.get('error')}")
                self._watchdog_recovering[source] = False
                return

            # Step 2: Poll is_reachable, max 120 seconds
            logger.info(f"[watchdog] Step 2: Waiting for camera {source} to come back...")
            came_back = False
            for i in range(24):  # 24 * 5s = 120s
                await asyncio.sleep(5)
                if await controller.is_reachable():
                    logger.info(f"[watchdog] Camera {source} is reachable after {(i + 1) * 5}s")
                    came_back = True
                    break

            if not came_back:
                logger.error(f"[watchdog] Camera {source} did not come back within 120s")
                self._watchdog_recovering[source] = False
                await self._send_watchdog_event(source, "recovery_failed", "Camera did not come back after reboot")
                return

            # Step 3: Sync time (camera reboot resets clock)
            logger.info(f"[watchdog] Step 3: Syncing time to camera {source}")
            try:
                sync_result = await controller.sync_time()
                if sync_result.get("success"):
                    logger.info(f"[watchdog] Time sync OK for {source}")
                else:
                    logger.warning(f"[watchdog] Time sync failed for {source}: {sync_result.get('error')}")
            except Exception as e:
                logger.warning(f"[watchdog] Time sync error for {source}: {e}")

            # Step 4: Wait 10s then restart go2rtc
            logger.info(f"[watchdog] Step 4: Waiting 10s before restarting go2rtc")
            await asyncio.sleep(10)
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "restart", "go2rtc",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.wait()
            logger.info(f"[watchdog] go2rtc restart exit code: {proc.returncode}")

            # Step 5: Wait 10s and verify stream
            await asyncio.sleep(10)
            target = self._get_camera_rtsp_target(source)
            if target:
                stream_ok = await self._tcp_probe(target[0], target[1])
                logger.info(f"[watchdog] Stream verification for {source}: {'OK' if stream_ok else 'FAILED'}")
            else:
                stream_ok = False

            # Step 6: Notify backend
            status = "recovery_success" if stream_ok else "recovery_partial"
            message = f"Camera {source} rebooted and go2rtc restarted"
            if not stream_ok:
                message += " (stream verification failed)"
            await self._send_watchdog_event(source, status, message)
            logger.info(f"[watchdog] Recovery complete for {source}: {status}")

        except Exception as e:
            logger.error(f"[watchdog] Recovery error for {source}: {e}", exc_info=True)
            await self._send_watchdog_event(source, "recovery_error", str(e))
        finally:
            self._watchdog_recovering[source] = False

    async def _send_watchdog_event(self, source: str, status: str, message: str):
        """Send watchdog event notification to backend"""
        await self._send_message_safe({
            "type": "watchdog_event",
            "ts": datetime.utcnow().isoformat(),
            "payload": {
                "source": source,
                "status": status,
                "message": message
            }
        })

    async def _sysinfo_loop(self):
        """Send system info periodically"""
        try:
            while self.running and self.state == ConnectionState.CONNECTED:
                try:
                    sysinfo = self._system_monitor.collect()
                    sysinfo["go2rtc_healthy"] = self._go2rtc_healthy
                    await self._send_message_safe({
                        "type": "system_info",
                        "ts": datetime.utcnow().isoformat(),
                        "payload": sysinfo
                    })
                    logger.debug("[sysmon] System info sent")
                except Exception as e:
                    logger.error(f"[sysmon] Error collecting/sending sysinfo: {e}")
                await asyncio.sleep(self.sysinfo_interval)
        except asyncio.CancelledError:
            logger.debug("[sysmon] Sysinfo loop cancelled")

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
            connector = aiohttp.TCPConnector(force_close=False, limit=10, keepalive_timeout=30)

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

    # 大於此值的回應會分塊傳輸（5 MB raw → ~6.7 MB base64，在 WebSocket 10 MB 限制內）
    CHUNK_SIZE = 5 * 1024 * 1024

    async def _handle_proxy_playback(self, payload: dict):
        """Handle HTTP proxy request to local playback API"""
        rid = payload.get("rid")
        method = payload.get("method", "GET")
        path = payload.get("path", "/")
        headers = payload.get("headers", {})
        body_b64 = payload.get("body_b64")
        timeout_ms = payload.get("timeout_ms", 30000)

        logger.info(f"[playback] {method} {path} (rid={rid})")

        try:
            body = None
            if body_b64:
                try:
                    body = base64.b64decode(body_b64)
                except Exception as e:
                    logger.error(f"[playback] Failed to decode body: {e}")

            url = self.playback_http + path

            timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
            connector = aiohttp.TCPConnector(force_close=False, limit=10, keepalive_timeout=30)

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

                    if len(resp_body) <= self.CHUNK_SIZE:
                        # 小回應：直接傳送（原有行為）
                        resp_body_b64 = base64.b64encode(resp_body).decode('utf-8')
                        response_sent = await self._send_message_safe({
                            "type": "proxy_playback_resp",
                            "ts": datetime.utcnow().isoformat(),
                            "payload": {
                                "rid": rid,
                                "status": status,
                                "headers": resp_headers,
                                "body_b64": resp_body_b64
                            }
                        })
                    else:
                        # 大回應：分塊傳送
                        import math
                        total_chunks = math.ceil(len(resp_body) / self.CHUNK_SIZE)
                        logger.info(f"[playback] Large response ({len(resp_body)} bytes), sending in {total_chunks} chunks")

                        # 先傳 metadata
                        await self._send_message_safe({
                            "type": "proxy_playback_resp",
                            "ts": datetime.utcnow().isoformat(),
                            "payload": {
                                "rid": rid,
                                "status": status,
                                "headers": resp_headers,
                                "chunked": True,
                                "total_chunks": total_chunks,
                                "total_size": len(resp_body)
                            }
                        })

                        # 逐塊傳送
                        for i in range(total_chunks):
                            chunk = resp_body[i * self.CHUNK_SIZE:(i + 1) * self.CHUNK_SIZE]
                            chunk_b64 = base64.b64encode(chunk).decode('utf-8')
                            sent = await self._send_message_safe({
                                "type": "proxy_playback_chunk",
                                "ts": datetime.utcnow().isoformat(),
                                "payload": {
                                    "rid": rid,
                                    "chunk_index": i,
                                    "body_b64": chunk_b64
                                }
                            })
                            if sent:
                                logger.debug(f"[playback] Sent chunk {i+1}/{total_chunks}")
                            else:
                                logger.error(f"[playback] Failed to send chunk {i+1}/{total_chunks}")
                                break

                        response_sent = True

                    if response_sent:
                        logger.info(f"[playback] {method} {path} → {status} ({len(resp_body)} bytes) ✓")
                    else:
                        logger.error(f"[playback] {method} {path} → {status} ✗ Failed to send response")

        except asyncio.TimeoutError:
            logger.error(f"[playback] Timeout for rid={rid}")
            await self._send_message_safe({
                "type": "proxy_playback_resp",
                "ts": datetime.utcnow().isoformat(),
                "payload": {
                    "rid": rid,
                    "error": "Playback API timeout"
                }
            })
        except aiohttp.ClientConnectorError as e:
            logger.error(f"[playback] Connection error: {e}")
            await self._send_message_safe({
                "type": "proxy_playback_resp",
                "ts": datetime.utcnow().isoformat(),
                "payload": {
                    "rid": rid,
                    "error": f"Playback API unavailable: {str(e)}"
                }
            })
        except Exception as e:
            logger.error(f"[playback] Error handling request: {e}", exc_info=True)
            await self._send_message_safe({
                "type": "proxy_playback_resp",
                "ts": datetime.utcnow().isoformat(),
                "payload": {
                    "rid": rid,
                    "error": f"Internal Error: {str(e)}"
                }
            })

    async def _time_sync_loop(self):
        """定期將本機時間同步到所有 ONVIF 攝影機"""
        try:
            # 啟動後先等 30 秒再做第一次同步（等待 ONVIF 初始化）
            await asyncio.sleep(30)
            while self.running:
                for name, controller in self.onvif_controllers.items():
                    try:
                        result = await controller.sync_time()
                        if result.get("success"):
                            logger.info(f"[time-sync] {name} 時間同步成功")
                        else:
                            logger.warning(f"[time-sync] {name} 時間同步失敗：{result.get('error')}")
                    except Exception as e:
                        logger.error(f"[time-sync] {name} 時間同步異常：{e}")
                await asyncio.sleep(self.time_sync_interval)
        except asyncio.CancelledError:
            logger.debug("[time-sync] 時間同步任務已取消")
        except Exception as e:
            logger.error(f"[time-sync] 時間同步迴圈異常：{e}", exc_info=True)

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
                            logger.debug(f"[gps] Heartbeat GPS: status={gps_data.get('status')}, lat={gps_data.get('lat')}, lon={gps_data.get('lon')}")
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

        # Stop RTMP push on disconnect
        if self._rtmp_pusher._running:
            await self._rtmp_pusher.stop()
            logger.info("[livekit] RTMP push stopped on disconnect")

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
            logger.info(f"[gps] Manager started, polling GPS data...")

        self._go2rtc_health_task = asyncio.create_task(self.go2rtc_health_monitor())

        # 啟動攝影機時間同步（如有 ONVIF controller）
        if self.onvif_controllers:
            self._time_sync_task = asyncio.create_task(self._time_sync_loop())
            logger.info(f"[time-sync] 已啟動，每 {self.time_sync_interval} 秒同步一次")

        # 啟動 RTSP watchdog（如有 ONVIF controller）
        if self.onvif_controllers:
            self._watchdog_task = asyncio.create_task(self._rtsp_watchdog())
            logger.info(f"[watchdog] RTSP watchdog 已啟動，監控 {list(self.onvif_controllers.keys())}")

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

        if self._time_sync_task and not self._time_sync_task.done():
            self._time_sync_task.cancel()
            try:
                await self._time_sync_task
            except asyncio.CancelledError:
                pass

        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
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

    # 載入額外攝影機的 ONVIF 設定（cam2）
    onvif_ip_cam2 = os.getenv("ONVIF_IP_CAM2")
    if onvif_ip_cam2:
        cam2_config = ONVIFConfig(
            ip=onvif_ip_cam2,
            port=int(os.getenv("ONVIF_PORT_CAM2", "80")),
            username=os.getenv("ONVIF_USER_CAM2", args.onvif_user or "admin"),
            password=os.getenv("ONVIF_PASS_CAM2", args.onvif_pass or ""),
            wsdl_dir=args.onvif_wsdl_dir
        )
        agent.onvif_controllers["cam2"] = ONVIFController(cam2_config)
        logger.info(f"[onvif] PTZ control enabled for cam2 → {onvif_ip_cam2}")

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
