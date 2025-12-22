#!/usr/bin/env python3
"""
MuMu Camera Device Agent

This agent runs on camera devices and:
1. Connects to the central signaling server via WebSocket
2. Handles watch requests from viewers
3. Creates WebRTC peer connections to stream video
4. Implements robust reconnection with exponential backoff
"""

import asyncio
import json
import logging
import argparse
import signal
import sys
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
    Generates fake video frames for testing.
    Replace this with actual camera capture in production.
    """

    def __init__(self):
        super().__init__()
        self.counter = 0

    async def recv(self):
        """Generate a fake video frame"""
        pts, time_base = await self.next_timestamp()

        # Create a simple frame with a counter
        img = np.zeros((480, 640, 3), dtype=np.uint8)

        # Draw counter text (simple visualization)
        self.counter += 1
        # Create gradient pattern
        img[:, :, 0] = (self.counter % 255)  # Blue channel
        img[:, :, 1] = ((self.counter * 2) % 255)  # Green channel
        img[:, :, 2] = ((self.counter * 3) % 255)  # Red channel

        # Create frame
        frame = av.VideoFrame.from_ndarray(img, format='bgr24')
        frame.pts = pts
        frame.time_base = time_base

        return frame


class CameraDeviceAgent:
    """Main device agent class"""

    def __init__(self, backend_url: str, device_id: str):
        self.backend_url = backend_url
        self.device_id = device_id
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
        self.heartbeat_interval = 30

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

                logger.info(f"Connected as device: {self.device_id}")

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
                    logger.info(f"Reconnecting in {delay} seconds... (attempt {self.reconnect_attempts})")
                    await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def send_message(self, message: dict):
        """Send a message to the backend"""
        if self.ws and not self.ws.closed:
            try:
                await self.ws.send(json.dumps(message))
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

        logger.debug(f"Received message: {msg_type}")

        if msg_type == "hello_ack":
            logger.info(f"Server acknowledged connection")

        elif msg_type == "heartbeat_ack":
            logger.debug("Heartbeat acknowledged")

        elif msg_type == "watch_request":
            # Viewer wants to watch this device
            session_id = payload.get("session_id")
            user_id = payload.get("user_id")
            ice_servers = payload.get("ice_servers", [])

            logger.info(f"Watch request from user {user_id}, session {session_id}")
            await self.handle_watch_request(session_id, ice_servers)

        elif msg_type == "signal_offer":
            # Received SDP offer from viewer
            session_id = payload.get("session_id")
            sdp = payload.get("sdp")

            logger.info(f"Received SDP offer for session {session_id}")
            await self.handle_signal_offer(session_id, sdp)

        elif msg_type == "signal_ice":
            # Received ICE candidate from viewer
            session_id = payload.get("session_id")
            candidate = payload.get("candidate")

            logger.debug(f"Received ICE candidate for session {session_id}")
            await self.handle_signal_ice(session_id, candidate)

        elif msg_type == "watch_ended":
            # Watch session ended
            session_id = payload.get("session_id")
            reason = payload.get("reason")

            logger.info(f"Watch session {session_id} ended: {reason}")
            await self.close_peer_connection(session_id)

        else:
            logger.warning(f"Unknown message type: {msg_type}")

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
            video_track = FakeVideoTrack()
            pc.addTrack(video_track)

            logger.info(f"Created peer connection for session {session_id}")

            # Set up event handlers
            @pc.on("iceconnectionstatechange")
            async def on_ice_state_change():
                logger.info(f"ICE connection state: {pc.iceConnectionState}")
                if pc.iceConnectionState == "failed":
                    await self.close_peer_connection(session_id)

            @pc.on("connectionstatechange")
            async def on_connection_state_change():
                logger.info(f"Connection state: {pc.connectionState}")

            # Wait for viewer's offer (they will send it via signal_offer message)

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

            # Create answer
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

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

            logger.info(f"Sent SDP answer for session {session_id}")

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
                logger.debug(f"Added ICE candidate for session {session_id}")

        except Exception as e:
            logger.error(f"Error handling ICE candidate: {e}", exc_info=True)

    async def close_peer_connection(self, session_id: str):
        """Close a peer connection"""
        pc = self.peer_connections.pop(session_id, None)
        if pc:
            await pc.close()
            logger.info(f"Closed peer connection for session {session_id}")

    async def heartbeat_loop(self):
        """Send periodic heartbeats"""
        try:
            while self.running and self.ws and not self.ws.closed:
                await self.send_message({
                    "type": "heartbeat",
                    "ts": datetime.utcnow().isoformat(),
                    "payload": {}
                })
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
        logger.info(f"Starting device agent: {self.device_id}")
        await self.connect()

    async def stop(self):
        """Stop the agent"""
        logger.info("Stopping device agent...")
        self.running = False
        await self.handle_disconnect()


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="MuMu Camera Device Agent")
    parser.add_argument(
        "--backend",
        default="ws://localhost:8000/ws/device",
        help="Backend WebSocket URL"
    )
    parser.add_argument(
        "--device-id",
        default=f"device-{uuid.uuid4().hex[:8]}",
        help="Unique device identifier"
    )
    args = parser.parse_args()

    # Create agent
    agent = CameraDeviceAgent(args.backend, args.device_id)

    # Handle shutdown signals
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        asyncio.create_task(agent.stop())

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start agent
    try:
        await agent.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
