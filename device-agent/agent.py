#!/usr/bin/env python3
"""
MuMu Camera Device Agent / Simulator

This agent runs on camera devices (or simulates them) and:
1. Connects to the central signaling server via WebSocket
2. Handles watch requests from viewers
3. Creates WebRTC peer connections to stream video
4. Implements robust reconnection with exponential backoff

Supports multiple video sources:
- fake: Moving box animation (for testing without camera)
- webcam: System webcam (requires opencv-python)
- camera: Raspberry Pi camera (requires picamera2)
"""

import asyncio
import json
import logging
import argparse
import signal
import sys
import os
from datetime import datetime
from typing import Optional, Dict
import uuid

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaBlackhole
import websockets
from websockets.exceptions import ConnectionClosed
import av
import numpy as np

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class FakeVideoTrack(VideoStreamTrack):
    """
    Generates animated fake video frames for testing.
    Creates a moving box with color bars background.
    """

    def __init__(self):
        super().__init__()
        self.counter = 0
        self.width = 640
        self.height = 480

        # Moving box parameters
        self.box_size = 80
        self.box_x = 0
        self.box_y = 0
        self.box_dx = 3
        self.box_dy = 2

        logger.info("FakeVideoTrack initialized (moving box animation)")

    async def recv(self):
        """Generate an animated video frame"""
        pts, time_base = await self.next_timestamp()

        # Create frame with color bars background
        img = self._create_color_bars()

        # Draw moving box
        self._draw_moving_box(img)

        # Draw frame counter
        self._draw_counter(img)

        # Update position
        self._update_box_position()

        self.counter += 1

        # Create frame
        frame = av.VideoFrame.from_ndarray(img, format='bgr24')
        frame.pts = pts
        frame.time_base = time_base

        return frame

    def _create_color_bars(self):
        """Create color bars background"""
        img = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        bar_width = self.width // 7
        colors = [
            (255, 255, 255),  # White
            (0, 255, 255),    # Yellow
            (255, 255, 0),    # Cyan
            (0, 255, 0),      # Green
            (255, 0, 255),    # Magenta
            (0, 0, 255),      # Red
            (255, 0, 0),      # Blue
        ]

        for i, color in enumerate(colors):
            x_start = i * bar_width
            x_end = (i + 1) * bar_width if i < 6 else self.width
            img[:, x_start:x_end] = color

        return img

    def _draw_moving_box(self, img):
        """Draw a moving white box"""
        x1 = int(self.box_x)
        y1 = int(self.box_y)
        x2 = int(self.box_x + self.box_size)
        y2 = int(self.box_y + self.box_size)

        # Clamp to image bounds
        x1 = max(0, min(x1, self.width - 1))
        y1 = max(0, min(y1, self.height - 1))
        x2 = max(0, min(x2, self.width))
        y2 = max(0, min(y2, self.height))

        # Draw white box with black border
        img[y1:y2, x1:x2] = (255, 255, 255)
        border = 3
        img[y1:y1+border, x1:x2] = (0, 0, 0)
        img[y2-border:y2, x1:x2] = (0, 0, 0)
        img[y1:y2, x1:x1+border] = (0, 0, 0)
        img[y1:y2, x2-border:x2] = (0, 0, 0)

    def _draw_counter(self, img):
        """Draw frame counter (simple text visualization)"""
        # Draw counter as a small indicator in top-left
        counter_display = self.counter % 100
        bar_height = 20
        bar_width = int((counter_display / 100) * 200)

        # Background
        img[10:10+bar_height, 10:210] = (50, 50, 50)
        # Progress bar
        img[10:10+bar_height, 10:10+bar_width] = (0, 255, 0)

    def _update_box_position(self):
        """Update box position (bouncing)"""
        self.box_x += self.box_dx
        self.box_y += self.box_dy

        # Bounce off edges
        if self.box_x <= 0 or self.box_x + self.box_size >= self.width:
            self.box_dx = -self.box_dx
        if self.box_y <= 0 or self.box_y + self.box_size >= self.height:
            self.box_dy = -self.box_dy

        # Clamp position
        self.box_x = max(0, min(self.box_x, self.width - self.box_size))
        self.box_y = max(0, min(self.box_y, self.height - self.box_size))


class WebcamTrack(VideoStreamTrack):
    """
    Captures video from system webcam using OpenCV.
    Requires opencv-python to be installed.
    """

    def __init__(self, camera_index=0):
        super().__init__()
        try:
            import cv2
            self.cv2 = cv2
            self.cap = cv2.VideoCapture(camera_index)

            if not self.cap.isOpened():
                raise RuntimeError(f"Failed to open camera {camera_index}")

            # Set resolution
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            logger.info(f"WebcamTrack initialized (camera {camera_index})")
        except ImportError:
            logger.error("OpenCV not installed. Install with: pip install opencv-python")
            raise
        except Exception as e:
            logger.error(f"Failed to initialize webcam: {e}")
            raise

    async def recv(self):
        """Capture frame from webcam"""
        pts, time_base = await self.next_timestamp()

        ret, frame_bgr = self.cap.read()
        if not ret:
            logger.error("Failed to read frame from webcam")
            # Return black frame on error
            frame_bgr = np.zeros((480, 640, 3), dtype=np.uint8)

        # Convert to av.VideoFrame
        frame = av.VideoFrame.from_ndarray(frame_bgr, format='bgr24')
        frame.pts = pts
        frame.time_base = time_base

        return frame

    def __del__(self):
        """Release camera on cleanup"""
        if hasattr(self, 'cap'):
            self.cap.release()


class CameraDeviceAgent:
    """Main device agent class"""

    def __init__(self, backend_url: str, device_id: str, video_source: str = "fake"):
        self.backend_url = backend_url
        self.device_id = device_id
        self.video_source = video_source
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False

        # WebRTC connections: session_id -> RTCPeerConnection
        self.peer_connections: Dict[str, RTCPeerConnection] = {}

        # Reconnection settings
        self.reconnect_delay = 1  # Start with 1 second
        self.max_reconnect_delay = 60  # Max 60 seconds
        self.reconnect_attempts = 0

        # Heartbeat
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.heartbeat_interval = 15  # 15 seconds

        logger.info(f"Device Agent initialized: {device_id}, video source: {video_source}")

    async def connect(self):
        """Connect to backend WebSocket with exponential backoff"""
        while self.running:
            try:
                logger.info(f"Connecting to {self.backend_url}...")
                self.ws = await websockets.connect(
                    self.backend_url,
                    ping_interval=20,
                    ping_timeout=10
                )

                # Send hello message
                await self.send_message({
                    "type": "hello",
                    "ts": datetime.utcnow().isoformat(),
                    "payload": {
                        "device_id": self.device_id
                    }
                })

                logger.info(f"✓ Connected as device: {self.device_id}")

                # Reset reconnection settings on successful connection
                self.reconnect_delay = 1
                self.reconnect_attempts = 0

                # Start heartbeat
                if self.heartbeat_task:
                    self.heartbeat_task.cancel()
                self.heartbeat_task = asyncio.create_task(self.heartbeat_loop())

                # Handle messages
                await self.message_loop()

            except (ConnectionClosed, ConnectionRefusedError, OSError) as e:
                logger.error(f"Connection error: {e}")
                await self.handle_disconnect()

                if self.running:
                    # Exponential backoff
                    self.reconnect_attempts += 1
                    delay = min(
                        self.reconnect_delay * (2 ** self.reconnect_attempts),
                        self.max_reconnect_delay
                    )
                    logger.info(f"⟳ Reconnecting in {delay}s... (attempt {self.reconnect_attempts})")
                    await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def send_message(self, message: dict):
        """Send a message to the backend"""
        if self.ws and not self.ws.closed:
            try:
                await self.ws.send(json.dumps(message))
                logger.debug(f"→ Sent: {message['type']}")
            except Exception as e:
                logger.error(f"Error sending message: {e}")

    async def message_loop(self):
        """Main message handling loop"""
        async for message in self.ws:
            try:
                data = json.loads(message)
                await self.handle_message(data)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON: {e}")
            except Exception as e:
                logger.error(f"Error handling message: {e}", exc_info=True)

    async def handle_message(self, message: dict):
        """Handle incoming message from backend"""
        msg_type = message.get("type")
        payload = message.get("payload", {})

        logger.debug(f"← Received: {msg_type}")

        if msg_type == "hello_ack":
            logger.info(f"✓ Server acknowledged connection")

        elif msg_type == "heartbeat_ack":
            logger.debug("♥ Heartbeat acknowledged")

        elif msg_type == "watch_request":
            # Viewer wants to watch this device
            session_id = payload.get("session_id")
            user_id = payload.get("user_id")
            ice_servers = payload.get("ice_servers", [])

            logger.info(f"▶ Watch request from user {user_id}, session {session_id}")
            await self.handle_watch_request(session_id, ice_servers)

        elif msg_type == "signal_offer":
            # Received SDP offer from viewer
            session_id = payload.get("session_id")
            sdp = payload.get("sdp")

            logger.info(f"⇄ Received SDP offer for session {session_id}")
            await self.handle_signal_offer(session_id, sdp)

        elif msg_type == "signal_ice":
            # Received ICE candidate from viewer
            session_id = payload.get("session_id")
            candidate = payload.get("candidate")

            logger.debug(f"⇄ Received ICE candidate for session {session_id}")
            await self.handle_signal_ice(session_id, candidate)

        elif msg_type == "watch_ended":
            # Watch session ended
            session_id = payload.get("session_id")
            reason = payload.get("reason")

            logger.info(f"■ Watch session {session_id} ended: {reason}")
            await self.close_peer_connection(session_id)

        else:
            logger.warning(f"? Unknown message type: {msg_type}")

    def _create_video_track(self):
        """Create video track based on configured source"""
        if self.video_source == "fake":
            return FakeVideoTrack()
        elif self.video_source == "webcam":
            return WebcamTrack(camera_index=0)
        elif self.video_source == "camera":
            # Raspberry Pi camera support (future)
            logger.error("Raspberry Pi camera not yet implemented")
            logger.info("Falling back to fake video source")
            return FakeVideoTrack()
        else:
            logger.warning(f"Unknown video source: {self.video_source}, using fake")
            return FakeVideoTrack()

    async def handle_watch_request(self, session_id: str, ice_servers: list):
        """Handle watch request by creating a peer connection"""
        try:
            # Create RTCConfiguration with ICE servers
            ice_server_configs = []
            for server in ice_servers:
                if isinstance(server.get("urls"), list):
                    urls = server["urls"]
                else:
                    urls = [server["urls"]]

                ice_server_configs.append(RTCIceServer(
                    urls=urls,
                    username=server.get("username"),
                    credential=server.get("credential")
                ))

            configuration = RTCConfiguration(iceServers=ice_server_configs)

            # Create peer connection
            pc = RTCPeerConnection(configuration=configuration)
            self.peer_connections[session_id] = pc

            # Add video track
            video_track = self._create_video_track()
            pc.addTrack(video_track)

            logger.info(f"✓ Created peer connection for session {session_id}")

            # Set up event handlers
            @pc.on("iceconnectionstatechange")
            async def on_ice_state_change():
                logger.info(f"ICE state: {pc.iceConnectionState}")
                if pc.iceConnectionState == "failed":
                    logger.error(f"ICE connection failed for session {session_id}")
                    await self.close_peer_connection(session_id)
                elif pc.iceConnectionState == "connected":
                    logger.info(f"✓ ICE connected for session {session_id}")

            @pc.on("connectionstatechange")
            async def on_connection_state_change():
                logger.info(f"Connection state: {pc.connectionState}")
                if pc.connectionState == "connected":
                    logger.info(f"✓ Peer connection established for session {session_id}")
                elif pc.connectionState == "failed":
                    logger.error(f"Peer connection failed for session {session_id}")

            # ICE candidate handler
            @pc.on("icecandidate")
            async def on_icecandidate(candidate):
                if candidate:
                    await self.send_message({
                        "type": "signal_ice",
                        "ts": datetime.utcnow().isoformat(),
                        "payload": {
                            "session_id": session_id,
                            "candidate": {
                                "candidate": candidate.candidate,
                                "sdpMid": candidate.sdpMid,
                                "sdpMLineIndex": candidate.sdpMLineIndex
                            }
                        }
                    })

        except Exception as e:
            logger.error(f"Error handling watch request: {e}", exc_info=True)

    async def handle_signal_offer(self, session_id: str, offer_data: dict):
        """Handle SDP offer from viewer"""
        try:
            pc = self.peer_connections.get(session_id)
            if not pc:
                logger.error(f"No peer connection for session {session_id}")
                return

            # Set remote description
            offer = RTCSessionDescription(
                sdp=offer_data["sdp"],
                type=offer_data["type"]
            )
            await pc.setRemoteDescription(offer)
            logger.info(f"✓ Set remote description for session {session_id}")

            # Create answer
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            logger.info(f"✓ Created answer for session {session_id}")

            # Send answer to backend
            await self.send_message({
                "type": "signal_answer",
                "ts": datetime.utcnow().isoformat(),
                "payload": {
                    "session_id": session_id,
                    "sdp": {
                        "sdp": pc.localDescription.sdp,
                        "type": pc.localDescription.type
                    }
                }
            })

            logger.info(f"✓ Sent SDP answer for session {session_id}")

        except Exception as e:
            logger.error(f"Error handling SDP offer: {e}", exc_info=True)

    async def handle_signal_ice(self, session_id: str, candidate_data: dict):
        """Handle ICE candidate from viewer"""
        try:
            pc = self.peer_connections.get(session_id)
            if not pc:
                logger.error(f"No peer connection for session {session_id}")
                return

            if candidate_data:
                from aiortc import RTCIceCandidate
                candidate = RTCIceCandidate(
                    sdpMid=candidate_data.get("sdpMid"),
                    sdpMLineIndex=candidate_data.get("sdpMLineIndex"),
                    candidate=candidate_data.get("candidate")
                )
                await pc.addIceCandidate(candidate)
                logger.debug(f"✓ Added ICE candidate for session {session_id}")

        except Exception as e:
            logger.error(f"Error handling ICE candidate: {e}", exc_info=True)

    async def close_peer_connection(self, session_id: str):
        """Close a peer connection"""
        pc = self.peer_connections.pop(session_id, None)
        if pc:
            await pc.close()
            logger.info(f"✓ Closed peer connection for session {session_id}")

    async def heartbeat_loop(self):
        """Send periodic heartbeats"""
        try:
            while self.running and self.ws and not self.ws.closed:
                await self.send_message({
                    "type": "heartbeat",
                    "ts": datetime.utcnow().isoformat(),
                    "payload": {}
                })
                logger.debug(f"♥ Heartbeat sent")
                await asyncio.sleep(self.heartbeat_interval)
        except asyncio.CancelledError:
            logger.debug("Heartbeat loop cancelled")
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")

    async def handle_disconnect(self):
        """Handle disconnection - clean up resources"""
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            self.heartbeat_task = None

        # Close all peer connections
        for session_id in list(self.peer_connections.keys()):
            await self.close_peer_connection(session_id)

        if self.ws:
            await self.ws.close()
            self.ws = None

        logger.info("Disconnected from backend")

    async def start(self):
        """Start the agent"""
        self.running = True
        logger.info(f"=== MuMu Camera Device Agent ===")
        logger.info(f"Device ID: {self.device_id}")
        logger.info(f"Video Source: {self.video_source}")
        logger.info(f"Backend: {self.backend_url}")
        logger.info(f"================================")
        await self.connect()

    async def stop(self):
        """Stop the agent"""
        logger.info("Stopping device agent...")
        self.running = False
        await self.handle_disconnect()


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="MuMu Camera Device Agent / Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Video Source Options:
  fake      - Animated test pattern (moving box) - no camera needed
  webcam    - System webcam (requires opencv-python)
  camera    - Raspberry Pi camera (future support)

Environment Variables:
  BACKEND_URL     - WebSocket backend URL (default: ws://localhost:8000/ws/device)
  DEVICE_ID       - Unique device identifier (default: auto-generated)
  VIDEO_SOURCE    - Video source type (default: fake)

Examples:
  # Run with fake video (simulator mode)
  python agent.py

  # Run with specific device ID
  python agent.py --device-id kitchen-cam-001

  # Run with webcam
  python agent.py --video-source webcam

  # Connect to remote backend
  python agent.py --backend wss://myserver.com/ws/device
        """
    )
    parser.add_argument(
        "--backend",
        default=os.getenv("BACKEND_URL", "ws://localhost:8000/ws/device"),
        help="Backend WebSocket URL"
    )
    parser.add_argument(
        "--device-id",
        default=os.getenv("DEVICE_ID", f"device-{uuid.uuid4().hex[:8]}"),
        help="Unique device identifier"
    )
    parser.add_argument(
        "--video-source",
        default=os.getenv("VIDEO_SOURCE", "fake"),
        choices=["fake", "webcam", "camera"],
        help="Video source type"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    args = parser.parse_args()

    # Set log level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Create agent
    agent = CameraDeviceAgent(args.backend, args.device_id, args.video_source)

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
