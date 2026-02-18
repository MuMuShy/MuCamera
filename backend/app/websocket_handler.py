import asyncio
import json
import logging
from typing import Dict, Optional, Set
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.models import Device, WatchSession, User
from app.redis_client import redis_client
from app.turn_credentials import get_ice_servers
from app.config import settings
import uuid

# Lazy import for LiveKit service (only when enabled)
_livekit_service = None


def _get_livekit_service():
    global _livekit_service
    if _livekit_service is None:
        try:
            from app import livekit_service
            _livekit_service = livekit_service
        except Exception:
            pass
    return _livekit_service


async def _is_livekit_mode() -> bool:
    """Check if current streaming mode is LiveKit (Redis override > env var)."""
    redis_mode = await redis_client.get("config:streaming_mode")
    if redis_mode in ("p2p", "livekit"):
        return redis_mode == "livekit"
    return settings.LIVEKIT_ENABLED

logger = logging.getLogger("mumucam")


class ConnectionManager:
    """Manages WebSocket connections for devices and viewers"""

    def __init__(self):
        # Active WebSocket connections
        self.device_connections: Dict[str, WebSocket] = {}  # device_id -> WebSocket
        self.viewer_connections: Dict[str, WebSocket] = {}  # user_id -> WebSocket

        # Heartbeat tracking
        self.device_heartbeats: Dict[str, datetime] = {}
        self.viewer_heartbeats: Dict[str, datetime] = {}

    async def connect_device(self, device_id: str, websocket: WebSocket, db: AsyncSession):
        """Connect a device"""
        # Close stale connection if exists (prevents orphaned connections)
        old_ws = self.device_connections.get(device_id)
        if old_ws is not None and old_ws is not websocket:
            logger.warning(f"Device {device_id} already connected, closing old connection")
            try:
                await old_ws.close(code=1012, reason="Replaced by new connection")
            except Exception:
                pass

        self.device_connections[device_id] = websocket
        self.device_heartbeats[device_id] = datetime.utcnow()

        # Update device online status in DB
        await db.execute(
            update(Device)
            .where(Device.device_id == device_id)
            .values(is_online=True, last_seen=datetime.utcnow())
        )
        await db.commit()

        # Store in Redis for presence tracking
        await redis_client.hset("devices:online", device_id, {
            "connected_at": datetime.utcnow().isoformat(),
            "last_heartbeat": datetime.utcnow().isoformat()
        })

    async def disconnect_device(self, device_id: str, websocket: WebSocket, db: AsyncSession):
        """Disconnect a device (only if the websocket matches the active connection)"""
        current_ws = self.device_connections.get(device_id)
        if current_ws is not None and current_ws is not websocket:
            # A newer connection has replaced this one; don't touch it
            logger.info(f"Device {device_id} disconnect skipped: connection already replaced")
            return

        if device_id in self.device_connections:
            del self.device_connections[device_id]
        if device_id in self.device_heartbeats:
            del self.device_heartbeats[device_id]

        # LiveKit: clean up ingress on device disconnect
        # Check Redis for existing ingress regardless of static config
        # (mode may have been switched dynamically via POST /api/config/streaming-mode)
        try:
            ingress_id = await redis_client.get(f"device:livekit_ingress:{device_id}")
            if ingress_id:
                lk = _get_livekit_service()
                if lk:
                    await lk.delete_ingress(ingress_id)
                await redis_client.delete(f"device:livekit_ingress:{device_id}")
                logger.info(f"[livekit] Cleaned up ingress for {device_id}")
        except Exception as e:
            logger.error(f"[livekit] Error cleaning up ingress for {device_id}: {e}")

        # Update device offline status in DB
        await db.execute(
            update(Device)
            .where(Device.device_id == device_id)
            .values(is_online=False, last_seen=datetime.utcnow())
        )
        await db.commit()

        # Remove from Redis
        await redis_client.hdel("devices:online", device_id)

        # End all active watch sessions for this device
        result = await db.execute(
            select(WatchSession).where(
                WatchSession.device_id == (
                    select(Device.id).where(Device.device_id == device_id).scalar_subquery()
                ),
                WatchSession.status == "active"
            )
        )
        sessions = result.scalars().all()

        for session in sessions:
            session.status = "ended"
            session.ended_at = datetime.utcnow()
            session.ended_reason = "device_disconnected"

            # Notify viewer
            viewer_ws = self.viewer_connections.get(str(session.user_id))
            if viewer_ws:
                await self.send_to_viewer(str(session.user_id), {
                    "type": "watch_ended",
                    "ts": datetime.utcnow().isoformat(),
                    "payload": {
                        "session_id": session.session_id,
                        "reason": "device_disconnected"
                    }
                })

        await db.commit()

    async def connect_viewer(self, user_id: str, websocket: WebSocket):
        """Connect a viewer"""
        # websocket.accept() is called in main.py before this method
        self.viewer_connections[user_id] = websocket
        self.viewer_heartbeats[user_id] = datetime.utcnow()

    async def disconnect_viewer(self, user_id: str, db: AsyncSession):
        """Disconnect a viewer"""
        if user_id in self.viewer_connections:
            del self.viewer_connections[user_id]
        if user_id in self.viewer_heartbeats:
            del self.viewer_heartbeats[user_id]

        # End all active watch sessions for this viewer
        result = await db.execute(
            select(WatchSession).where(
                WatchSession.user_id == int(user_id),
                WatchSession.status == "active"
            )
        )
        sessions = result.scalars().all()

        for session in sessions:
            session.status = "ended"
            session.ended_at = datetime.utcnow()
            session.ended_reason = "viewer_disconnected"

            # Get device_id and notify device
            result_device = await db.execute(
                select(Device).where(Device.id == session.device_id)
            )
            device = result_device.scalar_one_or_none()
            if device:
                await self.send_to_device(device.device_id, {
                    "type": "watch_ended",
                    "ts": datetime.utcnow().isoformat(),
                    "payload": {
                        "session_id": session.session_id,
                        "reason": "viewer_disconnected"
                    }
                })

        await db.commit()

    async def send_to_device(self, device_id: str, message: dict):
        """Send message to a specific device"""
        websocket = self.device_connections.get(device_id)
        if websocket:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error(f"Error sending to device {device_id}: {e}")

    async def send_to_viewer(self, user_id: str, message: dict):
        """Send message to a specific viewer"""
        websocket = self.viewer_connections.get(user_id)
        if websocket:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error(f"Error sending to viewer {user_id}: {e}")

    def is_device_online(self, device_id: str) -> bool:
        """Check if device is currently connected"""
        return device_id in self.device_connections

    async def update_heartbeat(self, identifier: str, is_device: bool):
        """Update heartbeat timestamp"""
        if is_device:
            self.device_heartbeats[identifier] = datetime.utcnow()
            await redis_client.hset("devices:online", identifier, {
                "last_heartbeat": datetime.utcnow().isoformat()
            })
        else:
            self.viewer_heartbeats[identifier] = datetime.utcnow()

    def get_online_devices(self) -> Set[str]:
        """Get all online device IDs"""
        return set(self.device_connections.keys())


# Global connection manager
manager = ConnectionManager()


async def handle_device_message(device_id: str, message: dict, db: AsyncSession):
    """Handle incoming message from device"""
    msg_type = message.get("type")
    payload = message.get("payload", {})
    request_id = message.get("request_id")

    if msg_type == "hello":
        # Device connected, send acknowledgment
        # Extract go2rtc info if present
        agent_version = payload.get("agent_version")
        go2rtc_http = payload.get("go2rtc_http")

        # Store go2rtc_http in redis for later use
        if go2rtc_http:
            await redis_client.hset(f"device:go2rtc:{device_id}", "http_url", go2rtc_http)

        response = {
            "type": "hello_ack",
            "request_id": request_id,
            "ts": datetime.utcnow().isoformat(),
            "payload": {
                "device_id": device_id,
                "server_time": datetime.utcnow().isoformat(),
                "agent_version": agent_version
            }
        }
        await manager.send_to_device(device_id, response)

        # Note: LiveKit ingress creation is handled in main.py after hello_ack,
        # since the hello message is consumed there before the message loop.

    elif msg_type == "heartbeat":
        # Update heartbeat
        await manager.update_heartbeat(device_id, is_device=True)

        # Store GPS data if present
        gps_data = payload.get("gps")
        if gps_data:
            await redis_client.setex(
                f"device:gps:{device_id}",
                60,  # 60 second TTL (expires if no heartbeat)
                gps_data
            )

        response = {
            "type": "heartbeat_ack",
            "request_id": request_id,
            "ts": datetime.utcnow().isoformat(),
            "payload": {}
        }
        await manager.send_to_device(device_id, response)

    elif msg_type == "capabilities":
        # Device reporting go2rtc stream capabilities
        streams = payload.get("streams", {})
        # Store in Redis
        await redis_client.hset(
            f"device:capabilities:{device_id}",
            "streams",
            streams
        )
        await redis_client.hset(
            f"device:capabilities:{device_id}",
            "last_updated",
            datetime.utcnow().isoformat()
        )
        logger.info(f"Device {device_id} reported {len(streams)} streams")

    elif msg_type == "proxy_http_resp":
        # HTTP proxy response from device
        rid = payload.get("rid")
        status = payload.get("status", "unknown")
        # Store response in redis with TTL for API endpoint to retrieve
        if rid:
            await redis_client.setex(
                f"proxy:response:{rid}",
                30,  # 30 second TTL
                payload  # setex will handle JSON encoding
            )
            logger.info(f"Stored proxy response for rid={rid}, status={status}, device={device_id}")
        else:
            logger.warning(f"Received proxy_http_resp without rid from device={device_id}")

    elif msg_type == "ptz_control_resp":
        # PTZ control response from device
        rid = payload.get("rid")
        success = payload.get("success", False)
        # Store response in redis with TTL for API endpoint to retrieve
        if rid:
            await redis_client.setex(
                f"ptz:response:{rid}",
                30,  # 30 second TTL
                payload
            )
            logger.info(f"Stored PTZ response for rid={rid}, success={success}, device={device_id}")
        else:
            logger.warning(f"Received ptz_control_resp without rid from device={device_id}")

    elif msg_type == "proxy_playback_resp":
        # Playback API response from device
        rid = payload.get("rid")
        status = payload.get("status", "unknown")
        error = payload.get("error")
        # Store response in redis with TTL for API endpoint to retrieve
        if rid:
            await redis_client.setex(
                f"playback:response:{rid}",
                120,  # 120 second TTL（分塊傳輸需要更長時間）
                payload
            )
            if error:
                logger.info(f"Stored playback response for rid={rid}, error={error}, device={device_id}")
            elif payload.get("chunked"):
                logger.info(f"Stored chunked playback metadata for rid={rid}, total_chunks={payload.get('total_chunks')}, device={device_id}")
            else:
                logger.info(f"Stored playback response for rid={rid}, status={status}, device={device_id}")
        else:
            logger.warning(f"Received proxy_playback_resp without rid from device={device_id}")

    elif msg_type == "proxy_playback_chunk":
        # 分塊傳輸的資料塊
        rid = payload.get("rid")
        chunk_index = payload.get("chunk_index")
        if rid is not None and chunk_index is not None:
            await redis_client.setex(
                f"playback:chunk:{rid}:{chunk_index}",
                120,
                payload.get("body_b64", "")
            )
            logger.debug(f"Stored playback chunk {chunk_index} for rid={rid}, device={device_id}")
        else:
            logger.warning(f"Received proxy_playback_chunk without rid/chunk_index from device={device_id}")

    elif msg_type == "system_info":
        # System info from device - store in Redis with TTL
        await redis_client.setex(
            f"device:sysinfo:{device_id}",
            30,  # 30 second TTL
            payload
        )

    elif msg_type == "control_resp":
        # Control command response from device
        rid = payload.get("rid")
        if rid:
            await redis_client.setex(
                f"control:response:{rid}",
                30,  # 30 second TTL
                payload
            )
            logger.info(f"Stored control response for rid={rid}, device={device_id}")

    elif msg_type == "signal_answer":
        # Forward SDP answer to viewer
        session_id = payload.get("session_id")
        sdp = payload.get("sdp")

        # Get session and forward to viewer
        result = await db.execute(
            select(WatchSession).where(WatchSession.session_id == session_id)
        )
        session = result.scalar_one_or_none()

        if session:
            await manager.send_to_viewer(str(session.user_id), {
                "type": "signal_answer",
                "ts": datetime.utcnow().isoformat(),
                "payload": {
                    "session_id": session_id,
                    "sdp": sdp
                }
            })

    elif msg_type == "signal_ice":
        # Forward ICE candidate to viewer
        session_id = payload.get("session_id")
        candidate = payload.get("candidate")

        result = await db.execute(
            select(WatchSession).where(WatchSession.session_id == session_id)
        )
        session = result.scalar_one_or_none()

        if session:
            await manager.send_to_viewer(str(session.user_id), {
                "type": "signal_ice",
                "ts": datetime.utcnow().isoformat(),
                "payload": {
                    "session_id": session_id,
                    "candidate": candidate
                }
            })

    elif msg_type == "device_presence":
        # Update presence information
        await redis_client.hset(f"device:presence:{device_id}", "status", payload)


async def handle_viewer_message(user_id: str, message: dict, db: AsyncSession):
    """Handle incoming message from viewer"""
    msg_type = message.get("type")
    payload = message.get("payload", {})
    request_id = message.get("request_id")

    if msg_type == "hello":
        # Viewer connected
        response = {
            "type": "hello_ack",
            "request_id": request_id,
            "ts": datetime.utcnow().isoformat(),
            "payload": {
                "user_id": user_id,
                "server_time": datetime.utcnow().isoformat()
            }
        }
        await manager.send_to_viewer(user_id, response)

    elif msg_type == "heartbeat":
        # Update heartbeat
        await manager.update_heartbeat(user_id, is_device=False)
        response = {
            "type": "heartbeat_ack",
            "request_id": request_id,
            "ts": datetime.utcnow().isoformat(),
            "payload": {}
        }
        await manager.send_to_viewer(user_id, response)

    elif msg_type == "watch_request":
        # Start watch session
        device_id = payload.get("device_id")

        # Check if device exists and is online
        result = await db.execute(
            select(Device).where(Device.device_id == device_id)
        )
        device = result.scalar_one_or_none()

        if not device:
            await manager.send_to_viewer(user_id, {
                "type": "error",
                "request_id": request_id,
                "ts": datetime.utcnow().isoformat(),
                "payload": {"message": "Device not found"}
            })
            return

        if not manager.is_device_online(device_id):
            await manager.send_to_viewer(user_id, {
                "type": "error",
                "request_id": request_id,
                "ts": datetime.utcnow().isoformat(),
                "payload": {"message": "Device is offline"}
            })
            return

        # Create watch session
        session_id = str(uuid.uuid4())
        session = WatchSession(
            session_id=session_id,
            user_id=int(user_id),
            device_id=device.id,
            status="pending"
        )
        db.add(session)
        await db.commit()

        # Store session in Redis
        await redis_client.hset(f"session:{session_id}", "data", {
            "user_id": user_id,
            "device_id": device_id,
            "started_at": datetime.utcnow().isoformat()
        })

        # LiveKit mode: return token + URL instead of ICE servers
        if await _is_livekit_mode():
            lk = _get_livekit_service()
            if lk:
                try:
                    lk_token = lk.generate_viewer_token(device_id, user_id)
                    await manager.send_to_viewer(user_id, {
                        "type": "watch_ready",
                        "request_id": request_id,
                        "ts": datetime.utcnow().isoformat(),
                        "payload": {
                            "session_id": session_id,
                            "mode": "livekit",
                            "livekit_token": lk_token,
                            "livekit_url": settings.LIVEKIT_PUBLIC_URL,
                        }
                    })
                    logger.info(f"[livekit] Sent viewer token for {device_id} to user {user_id}")
                    return
                except Exception as e:
                    logger.error(f"[livekit] Failed to generate viewer token: {e}")
                    # Fall through to P2P mode as fallback

        # P2P mode: return ICE servers
        # Get ICE servers for viewer (use public host for browser access)
        ice_servers = get_ice_servers(f"viewer_{user_id}_{session_id}", use_public_host=True)

        # Send to viewer
        await manager.send_to_viewer(user_id, {
            "type": "watch_ready",
            "request_id": request_id,
            "ts": datetime.utcnow().isoformat(),
            "payload": {
                "session_id": session_id,
                "mode": "p2p",
                "ice_servers": ice_servers
            }
        })

        # Notify device (use internal Docker host)
        device_ice_servers = get_ice_servers(f"device_{device_id}_{session_id}", use_public_host=False)
        await manager.send_to_device(device_id, {
            "type": "watch_request",
            "ts": datetime.utcnow().isoformat(),
            "payload": {
                "session_id": session_id,
                "user_id": user_id,
                "ice_servers": device_ice_servers
            }
        })

    elif msg_type == "signal_offer":
        # Forward SDP offer to device
        session_id = payload.get("session_id")
        sdp = payload.get("sdp")
        logger.info(f"Handling signal_offer for session {session_id}")

        result = await db.execute(
            select(WatchSession).where(WatchSession.session_id == session_id)
        )
        session = result.scalar_one_or_none()

        if session:
            logger.info(f"Session found: {session.session_id}, device_id: {session.device_id}")
            # Update session status
            session.status = "active"
            await db.commit()

            # Get device
            result_device = await db.execute(
                select(Device).where(Device.id == session.device_id)
            )
            device = result_device.scalar_one_or_none()

            if device:
                logger.info(f"Forwarding offer to device {device.device_id}")
                await manager.send_to_device(device.device_id, {
                    "type": "signal_offer",
                    "ts": datetime.utcnow().isoformat(),
                    "payload": {
                        "session_id": session_id,
                        "sdp": sdp
                    }
                })
            else:
                logger.warning(f"Device not found for session {session_id}")
        else:
            logger.warning(f"Session not found: {session_id}")

    elif msg_type == "signal_ice":
        # Forward ICE candidate to device
        session_id = payload.get("session_id")
        candidate = payload.get("candidate")

        result = await db.execute(
            select(WatchSession).where(WatchSession.session_id == session_id)
        )
        session = result.scalar_one_or_none()

        if session:
            result_device = await db.execute(
                select(Device).where(Device.id == session.device_id)
            )
            device = result_device.scalar_one_or_none()

            if device:
                await manager.send_to_device(device.device_id, {
                    "type": "signal_ice",
                    "ts": datetime.utcnow().isoformat(),
                    "payload": {
                        "session_id": session_id,
                        "candidate": candidate
                    }
                })

    elif msg_type == "end_watch":
        # End watch session
        session_id = payload.get("session_id")

        result = await db.execute(
            select(WatchSession).where(WatchSession.session_id == session_id)
        )
        session = result.scalar_one_or_none()

        if session:
            session.status = "ended"
            session.ended_at = datetime.utcnow()
            session.ended_reason = "user_ended"
            await db.commit()

            # Notify device
            result_device = await db.execute(
                select(Device).where(Device.id == session.device_id)
            )
            device = result_device.scalar_one_or_none()

            if device:
                await manager.send_to_device(device.device_id, {
                    "type": "watch_ended",
                    "ts": datetime.utcnow().isoformat(),
                    "payload": {
                        "session_id": session_id,
                        "reason": "user_ended"
                    }
                })

            # Clean up Redis
            await redis_client.delete(f"session:{session_id}")
