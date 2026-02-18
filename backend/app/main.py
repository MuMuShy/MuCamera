from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
from pydantic import BaseModel
from typing import List, Optional
import secrets
import uuid
import base64
import asyncio
import json
import logging

logger = logging.getLogger("mumucam")

from app.config import settings
from app.database import get_db, engine
from app.models import User, Device, DeviceOwnership, PairingCode, WatchSession
from app.auth import (
    authenticate_user,
    create_access_token,
    get_password_hash,
    get_current_user,
)
from app.redis_client import redis_client
from app.turn_credentials import get_ice_servers
from app.websocket_handler import (
    manager,
    handle_device_message,
    handle_viewer_message,
)

# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Central signaling server for MuMu Camera System"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic models for API
class UserCreate(BaseModel):
    username: str
    email: str
    password: str


class UserLogin(BaseModel):
    username: str
    password: str


class DeviceRegister(BaseModel):
    device_id: str
    device_name: Optional[str] = None
    device_type: str = "camera"


class PairingRequest(BaseModel):
    pairing_code: str


class DeviceResponse(BaseModel):
    id: int
    device_id: str
    device_name: Optional[str]
    device_type: str
    is_online: bool
    last_seen: Optional[datetime]

    class Config:
        from_attributes = True


async def verify_device_ownership(db: AsyncSession, user: User, device_id: str) -> Device:
    """Verify that the user owns the device. Raises 403 if not."""
    result = await db.execute(
        select(Device).where(Device.device_id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    result = await db.execute(
        select(DeviceOwnership).where(
            DeviceOwnership.device_id == device.id,
            DeviceOwnership.user_id == user.id
        )
    )
    ownership = result.scalar_one_or_none()
    if not ownership:
        raise HTTPException(status_code=403, detail="You do not own this device")

    return device


# Redis health state (updated by background loop)
_redis_health: dict = {"healthy": False, "writable": False, "role": "unknown", "error": "Not checked yet"}


async def _redis_health_loop():
    """Background task: check Redis health every 30 seconds"""
    global _redis_health
    while True:
        try:
            _redis_health = await redis_client.health_check()
            if _redis_health["healthy"]:
                logger.debug(f"Redis health OK: role={_redis_health['role']}")
            else:
                logger.error(f"Redis health FAIL: {_redis_health}")
        except Exception as e:
            logger.error(f"Redis health check exception: {e}")
            _redis_health = {"healthy": False, "writable": False, "role": "unknown", "error": str(e)}
        await asyncio.sleep(30)


# Startup/Shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize connections on startup"""
    await redis_client.connect()
    logger.info("Redis connected")
    asyncio.create_task(_redis_health_loop())


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up connections on shutdown"""
    await redis_client.disconnect()
    logger.info("Redis disconnected")


# Health check
@app.get("/")
async def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running"
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    overall = "healthy" if _redis_health.get("healthy", False) else "degraded"
    return {
        "status": overall,
        "redis": {
            "healthy": _redis_health.get("healthy", False),
            "writable": _redis_health.get("writable", False),
            "role": _redis_health.get("role", "unknown"),
        },
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/turn/credentials")
async def turn_credentials(token: str, db: AsyncSession = Depends(get_db)):
    """Get TURN server credentials for WebRTC"""
    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    ice_servers = get_ice_servers(str(user.id), use_public_host=True)
    return {"ice_servers": ice_servers}


# User authentication endpoints
@app.post("/api/auth/register")
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    """Register a new user"""
    # Check if username exists
    result = await db.execute(select(User).where(User.username == user_data.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already exists")

    # Check if email exists
    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already exists")

    # Create user
    hashed_password = get_password_hash(user_data.password)
    user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hashed_password
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Create access token
    access_token = create_access_token({"user_id": user.id, "username": user.username})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email
        }
    }


@app.post("/api/auth/login")
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    """Login user"""
    user = await authenticate_user(db, credentials.username, credentials.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )

    access_token = create_access_token({"user_id": user.id, "username": user.username})

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email
        }
    }


# Device management endpoints
@app.post("/api/devices/register")
async def register_device(
    device_data: DeviceRegister,
    db: AsyncSession = Depends(get_db)
):
    """Register a new device (public endpoint for device initial setup)"""
    # Check if device already exists
    result = await db.execute(
        select(Device).where(Device.device_id == device_data.device_id)
    )
    device = result.scalar_one_or_none()

    if device:
        return {"device_id": device.device_id, "message": "Device already registered"}

    # Create new device
    device = Device(
        device_id=device_data.device_id,
        device_name=device_data.device_name,
        device_type=device_data.device_type
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)

    return {
        "device_id": device.device_id,
        "message": "Device registered successfully"
    }


@app.get("/api/devices", response_model=List[DeviceResponse])
async def get_user_devices(
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """Get all devices owned by the authenticated user"""
    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Get user's devices through ownership
    result = await db.execute(
        select(Device)
        .join(DeviceOwnership, DeviceOwnership.device_id == Device.id)
        .where(DeviceOwnership.user_id == user.id)
    )
    devices = result.scalars().all()

    # Override DB is_online with real-time WebSocket connection state
    for device in devices:
        device.is_online = manager.is_device_online(device.device_id)

    return devices


@app.post("/api/devices/pair")
async def pair_device(
    pairing_data: PairingRequest,
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """Pair a device to user using pairing code"""
    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Find valid pairing code
    result = await db.execute(
        select(PairingCode).where(
            PairingCode.code == pairing_data.pairing_code,
            PairingCode.is_used == False,
            PairingCode.expires_at > datetime.utcnow()
        )
    )
    pairing_code = result.scalar_one_or_none()

    if not pairing_code:
        raise HTTPException(status_code=404, detail="Invalid or expired pairing code")

    # Mark code as used
    pairing_code.is_used = True

    # Create ownership
    ownership = DeviceOwnership(
        user_id=user.id,
        device_id=pairing_code.device_id,
        role="owner"
    )
    db.add(ownership)
    await db.commit()

    # Get device info
    result = await db.execute(select(Device).where(Device.id == pairing_code.device_id))
    device = result.scalar_one()

    return {
        "message": "Device paired successfully",
        "device": {
            "device_id": device.device_id,
            "device_name": device.device_name
        }
    }


@app.get("/api/devices/{device_id}/status")
async def get_device_status(device_id: str, db: AsyncSession = Depends(get_db)):
    """Get device status"""
    result = await db.execute(select(Device).where(Device.device_id == device_id))
    device = result.scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    is_online = manager.is_device_online(device_id)

    return {
        "device_id": device.device_id,
        "is_online": is_online,
        "last_seen": device.last_seen.isoformat() if device.last_seen else None
    }


# WebSocket endpoints
@app.websocket("/ws/device")
async def device_websocket(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    """WebSocket endpoint for device connections"""
    device_id = None
    try:
        # Simple auth: expect first message to contain device_id
        await websocket.accept()

        # Wait for hello message
        data = await websocket.receive_json()
        if data.get("type") != "hello":
            await websocket.close(code=1008, reason="Expected hello message")
            return

        device_id = data.get("payload", {}).get("device_id")
        if not device_id:
            await websocket.close(code=1008, reason="Missing device_id")
            return

        # Verify device exists
        result = await db.execute(select(Device).where(Device.device_id == device_id))
        device = result.scalar_one_or_none()

        if not device:
            await websocket.close(code=1008, reason="Device not found")
            return

        # Connect device
        await manager.connect_device(device_id, websocket, db)

        # Send hello_ack
        await websocket.send_json({
            "type": "hello_ack",
            "ts": datetime.utcnow().isoformat(),
            "payload": {
                "device_id": device_id,
                "server_time": datetime.utcnow().isoformat()
            }
        })

        # LiveKit: create ingress and notify device to start RTMP push
        # Check Redis override first, fall back to static config
        _redis_mode = await redis_client.get("config:streaming_mode")
        _livekit_active = _redis_mode == "livekit" if _redis_mode in ("p2p", "livekit") else settings.LIVEKIT_ENABLED
        if _livekit_active:
            from app.websocket_handler import _get_livekit_service
            lk = _get_livekit_service()
            if lk:
                try:
                    ingress_info = await lk.create_ingress(device_id)
                    await redis_client.set(
                        f"device:livekit_ingress:{device_id}",
                        ingress_info["ingress_id"]
                    )
                    await websocket.send_json({
                        "type": "livekit_ingress",
                        "ts": datetime.utcnow().isoformat(),
                        "payload": {
                            "rtmp_url": ingress_info["rtmp_url"],
                            "stream_key": ingress_info["stream_key"],
                        }
                    })
                    print(f"[livekit] Sent ingress to {device_id}: {ingress_info['rtmp_url']}", flush=True)
                except Exception as e:
                    print(f"[livekit] Failed to create ingress for {device_id}: {e}", flush=True)

        # Handle messages
        while True:
            data = await websocket.receive_json()
            await handle_device_message(device_id, data, db)

    except WebSocketDisconnect:
        if device_id:
            await manager.disconnect_device(device_id, websocket, db)
    except Exception as e:
        logger.error(f"Device WebSocket error: {e}")
        if device_id:
            await manager.disconnect_device(device_id, websocket, db)


@app.websocket("/ws/viewer")
async def viewer_websocket(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    """WebSocket endpoint for viewer connections"""
    user_id = None
    try:
        await websocket.accept()

        # Wait for hello message with token
        data = await websocket.receive_json()
        if data.get("type") != "hello":
            await websocket.close(code=1008, reason="Expected hello message")
            return

        token = data.get("payload", {}).get("token")
        if not token:
            await websocket.close(code=1008, reason="Missing token")
            return

        # Verify user
        user = await get_current_user(db, token)
        if not user:
            await websocket.close(code=1008, reason="Invalid token")
            return

        user_id = str(user.id)

        # Connect viewer
        await manager.connect_viewer(user_id, websocket)

        # Send hello_ack
        await websocket.send_json({
            "type": "hello_ack",
            "ts": datetime.utcnow().isoformat(),
            "payload": {
                "user_id": user_id,
                "server_time": datetime.utcnow().isoformat()
            }
        })

        # Handle messages
        while True:
            data = await websocket.receive_json()
            logger.info(f"Viewer {user_id} sent message: {data.get('type')}")
            await handle_viewer_message(user_id, data, db)

    except WebSocketDisconnect:
        if user_id:
            await manager.disconnect_viewer(user_id, db)
    except Exception as e:
        logger.error(f"Viewer WebSocket error: {e}")
        if user_id:
            await manager.disconnect_viewer(user_id, db)


# Pairing code generation (called by device)
@app.post("/api/pairing/generate")
async def generate_pairing_code(
    device_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Generate a pairing code for a device"""
    # Verify device exists
    result = await db.execute(select(Device).where(Device.device_id == device_id))
    device = result.scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Generate random 6-digit code
    code = ''.join([str(secrets.randbelow(10)) for _ in range(settings.PAIRING_CODE_LENGTH)])

    # Ensure code is unique
    while True:
        result = await db.execute(select(PairingCode).where(PairingCode.code == code))
        if not result.scalar_one_or_none():
            break
        code = ''.join([str(secrets.randbelow(10)) for _ in range(settings.PAIRING_CODE_LENGTH)])

    # Create pairing code
    pairing_code = PairingCode(
        device_id=device.id,
        code=code,
        expires_at=datetime.utcnow() + timedelta(seconds=settings.PAIRING_CODE_TTL)
    )
    db.add(pairing_code)
    await db.commit()

    return {
        "code": code,
        "expires_at": pairing_code.expires_at.isoformat(),
        "ttl": settings.PAIRING_CODE_TTL
    }


class PTZControlRequest(BaseModel):
    action: str  # move, stop, focus, auto_focus, preset, capabilities
    pan: Optional[float] = 0
    tilt: Optional[float] = 0
    zoom: Optional[float] = 0
    duration: Optional[float] = 0.5
    direction: Optional[str] = "near"  # for focus: near/far
    speed: Optional[float] = 0.5
    preset: Optional[int] = 1
    source: Optional[str] = "cam"  # cam or cam2


@app.post("/api/devices/{device_id}/ptz")
async def ptz_control(
    device_id: str,
    ptz_request: PTZControlRequest,
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """Send PTZ control command to device"""
    import uuid

    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    # Check if device is online
    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    # Generate request ID
    rid = str(uuid.uuid4())

    # Send PTZ control request to device
    ptz_message = {
        "type": "ptz_control",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "action": ptz_request.action,
            "pan": ptz_request.pan,
            "tilt": ptz_request.tilt,
            "zoom": ptz_request.zoom,
            "duration": ptz_request.duration,
            "direction": ptz_request.direction,
            "speed": ptz_request.speed,
            "preset": ptz_request.preset,
            "source": ptz_request.source
        }
    }

    logger.info(f"[ptz] Sending {ptz_request.action} to device {device_id}, rid={rid}")
    await manager.send_to_device(device_id, ptz_message)

    # Wait for response (poll redis)
    from app.redis_client import redis_client
    import asyncio

    for attempt in range(20):  # Wait up to 10 seconds (20 * 0.5s)
        resp_data = await redis_client.get(f"ptz:response:{rid}")
        if resp_data:
            logger.info(f"[ptz] Got response for rid={rid}")
            await redis_client.delete(f"ptz:response:{rid}")
            return resp_data

        await asyncio.sleep(0.5)

    # Timeout - return success anyway since PTZ commands are fire-and-forget
    logger.info(f"[ptz] Timeout waiting for response rid={rid}, returning success")
    return {"success": True, "message": "Command sent (no ack)"}


@app.get("/api/devices/{device_id}/gps")
async def get_device_gps(
    device_id: str,
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """Get device GPS data"""
    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    from app.redis_client import redis_client

    # Get GPS data from Redis
    gps_data = await redis_client.get(f"device:gps:{device_id}")

    if gps_data:
        return gps_data

    # Return empty/disabled state if no GPS data
    return {
        "enabled": False,
        "status": "unavailable",
        "lat": None,
        "lon": None
    }


@app.get("/api/devices/{device_id}/sysinfo")
async def get_device_sysinfo(
    device_id: str,
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """Get device system information from Redis"""
    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    sysinfo = await redis_client.get(f"device:sysinfo:{device_id}")
    if sysinfo:
        return sysinfo

    return {"error": "No system info available", "online": manager.is_device_online(device_id)}


class ControlCommandRequest(BaseModel):
    command: str  # restart_agent, restart_go2rtc, reboot, restart_stream, reboot_camera
    source: Optional[str] = None  # camera source for reboot_camera (e.g. "cam", "cam2")


@app.post("/api/devices/{device_id}/control")
async def send_control_command(
    device_id: str,
    request_body: ControlCommandRequest,
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """Send control command to device"""
    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    allowed_commands = {"restart_agent", "restart_go2rtc", "reboot", "restart_stream", "reboot_camera"}
    if request_body.command not in allowed_commands:
        raise HTTPException(status_code=400, detail=f"Invalid command. Allowed: {', '.join(allowed_commands)}")

    rid = str(uuid.uuid4())

    payload = {
        "rid": rid,
        "command": request_body.command
    }
    if request_body.source:
        payload["source"] = request_body.source

    control_message = {
        "type": "control_command",
        "ts": datetime.utcnow().isoformat(),
        "payload": payload
    }

    logger.info(f"[control] Sending {request_body.command} to device {device_id}, rid={rid}")
    await manager.send_to_device(device_id, control_message)

    # Wait for response
    for attempt in range(20):  # 10 seconds
        resp_data = await redis_client.get(f"control:response:{rid}")
        if resp_data:
            await redis_client.delete(f"control:response:{rid}")
            return resp_data
        await asyncio.sleep(0.5)

    # For restart_agent, reboot, and reboot_camera, timeout is expected
    if request_body.command in ("restart_agent", "reboot", "reboot_camera"):
        return {"success": True, "message": f"Command '{request_body.command}' sent (device will disconnect)"}

    return {"success": True, "message": "Command sent (no ack)"}


@app.api_route("/api/devices/{device_id}/proxy/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_to_device(
    device_id: str,
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Proxy HTTP request to device's go2rtc instance"""
    import uuid
    import base64

    # Check if device is online
    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    # Generate request ID
    rid = str(uuid.uuid4())

    # Get request body
    body = await request.body()
    body_b64 = base64.b64encode(body).decode('utf-8') if body else None

    # Reconstruct full path with query string
    full_path = f"/{path}"
    if request.url.query:
        full_path = f"{full_path}?{request.url.query}"

    # Filter request headers - only forward safe headers to device/go2rtc
    # Forwarding browser headers like Accept-Encoding, Host etc. can cause issues
    safe_request_headers = {}
    skip_forward = {'host', 'accept-encoding', 'connection', 'keep-alive',
                    'upgrade', 'sec-fetch-dest', 'sec-fetch-mode', 'sec-fetch-site',
                    'sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform',
                    'origin', 'referer', 'cookie', 'authorization'}
    for k, v in request.headers.items():
        if k.lower() not in skip_forward:
            safe_request_headers[k] = v

    # Send proxy request to device
    proxy_request = {
        "type": "proxy_http",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": request.method,
            "path": full_path,
            "headers": safe_request_headers,
            "body_b64": body_b64,
            "timeout_ms": 30000
        }
    }

    logger.info(f"[proxy] Sending request to device {device_id}, rid={rid}, path={full_path}")
    await manager.send_to_device(device_id, proxy_request)

    # Wait for response (poll redis)
    from app.redis_client import redis_client
    import asyncio
    import json as json_module

    for attempt in range(60):  # Wait up to 30 seconds (60 * 0.5s)
        resp_data = await redis_client.get(f"proxy:response:{rid}")
        if resp_data:
            logger.info(f"[proxy] Got response from Redis for rid={rid} after {attempt * 0.5}s")
            # Parse response (already decoded by redis_client.get)
            status = resp_data.get("status", 500)
            resp_headers = resp_data.get("headers", {})
            resp_body_b64 = resp_data.get("body_b64", "")

            # Decode body
            resp_body = base64.b64decode(resp_body_b64) if resp_body_b64 else b""

            logger.debug(f"[proxy] Response body size: {len(resp_body)} bytes, b64 size: {len(resp_body_b64)}")
            if full_path.startswith("/api/webrtc"):
                logger.debug(f"[proxy] WebRTC response (first 200): {resp_body[:200]}")

            # Clean up
            await redis_client.delete(f"proxy:response:{rid}")

            # Normalize header names to lowercase and filter problematic headers
            # These headers will be handled by FastAPI/Starlette automatically
            # content-encoding must be skipped because aiohttp auto-decompresses
            # but keeps the header, causing browsers to double-decompress
            skip_headers = {'transfer-encoding', 'content-encoding', 'content-length', 'connection', 'keep-alive', 'date'}
            headers_filtered = {}
            for k, v in resp_headers.items():
                k_lower = k.lower()
                if k_lower not in skip_headers and k_lower not in headers_filtered:
                    headers_filtered[k_lower] = v

            logger.debug(f"[proxy] Returning response with filtered headers: {list(headers_filtered.keys())}")

            # Return response - let FastAPI handle content-length automatically
            return Response(
                content=resp_body,
                status_code=status,
                headers=headers_filtered
            )

        await asyncio.sleep(0.5)

    # Timeout
    logger.error(f"[proxy] Timeout waiting for response rid={rid} from device {device_id}")
    raise HTTPException(status_code=504, detail="Proxy timeout")


# ============ Recordings API ============

class RecordingListResponse(BaseModel):
    recordings: list
    total: int
    page: int
    limit: int


@app.get("/api/devices/{device_id}/recordings")
async def get_device_recordings(
    device_id: str,
    token: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    source: str = "cam",
    db: AsyncSession = Depends(get_db)
):
    """
    Get recordings list for a device.
    Proxies to device's local playback API.
    """
    import asyncio
    import base64

    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    # Check if device is online
    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    # Generate request ID
    rid = str(uuid.uuid4())

    # Build query string
    params = []
    if start_date:
        params.append(f"start_date={start_date}")
    if end_date:
        params.append(f"end_date={end_date}")
    params.append(f"page={page}")
    params.append(f"limit={limit}")
    params.append(f"source={source}")
    query_string = "&".join(params)

    # Send proxy request to device's playback API (port 8090)
    proxy_request = {
        "type": "proxy_playback",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": "GET",
            "path": f"/recordings?{query_string}",
            "headers": {},
            "body_b64": None,
            "timeout_ms": 10000
        }
    }

    logger.info(f"[recordings] Fetching list from device {device_id}, rid={rid}")
    await manager.send_to_device(device_id, proxy_request)

    # Wait for response
    for attempt in range(20):
        resp_data = await redis_client.get(f"playback:response:{rid}")
        if resp_data:
            logger.info(f"[recordings] Got response for rid={rid}")
            await redis_client.delete(f"playback:response:{rid}")

            # Check for error
            if "error" in resp_data:
                raise HTTPException(status_code=502, detail=resp_data["error"])

            # Decode body_b64 and return JSON content
            body_b64 = resp_data.get("body_b64", "")
            if body_b64:
                import json
                body = base64.b64decode(body_b64).decode('utf-8')
                return json.loads(body)
            return resp_data

        await asyncio.sleep(0.5)

    raise HTTPException(status_code=504, detail="Playback API timeout")


@app.get("/api/devices/{device_id}/recordings/stats")
async def get_device_recordings_stats(
    device_id: str,
    token: str,
    source: str = "cam",
    db: AsyncSession = Depends(get_db)
):
    """Get recording statistics for a device."""
    import asyncio

    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    rid = str(uuid.uuid4())

    proxy_request = {
        "type": "proxy_playback",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": "GET",
            "path": f"/recordings/stats?source={source}",
            "headers": {},
            "body_b64": None,
            "timeout_ms": 10000
        }
    }

    await manager.send_to_device(device_id, proxy_request)

    for attempt in range(20):
        resp_data = await redis_client.get(f"playback:response:{rid}")
        if resp_data:
            await redis_client.delete(f"playback:response:{rid}")
            if "error" in resp_data:
                raise HTTPException(status_code=502, detail=resp_data["error"])

            # Decode body_b64 and return JSON content
            body_b64 = resp_data.get("body_b64", "")
            if body_b64:
                import json
                body = base64.b64decode(body_b64).decode('utf-8')
                return json.loads(body)
            return resp_data
        await asyncio.sleep(0.5)

    raise HTTPException(status_code=504, detail="Playback API timeout")


@app.get("/api/devices/{device_id}/recordings/{filename}/hls/playlist.m3u8")
async def get_recording_hls_playlist(
    device_id: str,
    filename: str,
    token: str,
    source: str = "cam",
    db: AsyncSession = Depends(get_db)
):
    """Get HLS playlist for a recording (proxies to device)."""
    import asyncio
    import base64

    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    rid = str(uuid.uuid4())

    proxy_request = {
        "type": "proxy_playback",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": "GET",
            "path": f"/recordings/{filename}/hls/playlist.m3u8?source={source}",
            "headers": {},
            "body_b64": None,
            "timeout_ms": 60000  # HLS generation can take time
        }
    }

    logger.info(f"[recordings] Getting HLS playlist for {filename} from {device_id}")
    await manager.send_to_device(device_id, proxy_request)

    for attempt in range(120):  # 60 seconds timeout
        resp_data = await redis_client.get(f"playback:response:{rid}")
        if resp_data:
            await redis_client.delete(f"playback:response:{rid}")

            if "error" in resp_data:
                raise HTTPException(status_code=502, detail=resp_data["error"])

            # Return playlist content
            body_b64 = resp_data.get("body_b64", "")
            body = base64.b64decode(body_b64) if body_b64 else b""

            return Response(
                content=body,
                media_type="application/vnd.apple.mpegurl"
            )
        await asyncio.sleep(0.5)

    raise HTTPException(status_code=504, detail="HLS playlist timeout")


@app.get("/api/devices/{device_id}/recordings/{filename}/hls/{segment}")
async def get_recording_hls_segment(
    device_id: str,
    filename: str,
    segment: str,
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """Get HLS segment for a recording (proxies to device)."""
    import asyncio
    import base64

    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    rid = str(uuid.uuid4())

    proxy_request = {
        "type": "proxy_playback",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": "GET",
            "path": f"/recordings/{filename}/hls/{segment}",
            "headers": {},
            "body_b64": None,
            "timeout_ms": 30000
        }
    }

    await manager.send_to_device(device_id, proxy_request)

    for attempt in range(60):
        resp_data = await redis_client.get(f"playback:response:{rid}")
        if resp_data:
            await redis_client.delete(f"playback:response:{rid}")

            if "error" in resp_data:
                raise HTTPException(status_code=502, detail=resp_data["error"])

            body_b64 = resp_data.get("body_b64", "")
            body = base64.b64decode(body_b64) if body_b64 else b""

            return Response(
                content=body,
                media_type="video/mp2t"
            )
        await asyncio.sleep(0.5)

    raise HTTPException(status_code=504, detail="HLS segment timeout")


@app.get("/api/devices/{device_id}/recordings/{filename}/download")
async def download_recording(
    device_id: str,
    filename: str,
    token: str,
    source: str = "cam",
    format: str = "ts",
    db: AsyncSession = Depends(get_db)
):
    """
    Download a recording file.
    Supports format=mp4 for MP4 remux download.
    支援分塊傳輸：裝置分批傳送大檔案，後端邊收邊串流給瀏覽器。
    """
    import asyncio

    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    rid = str(uuid.uuid4())

    # 傳遞 format 參數到裝置
    download_path = f"/recordings/{filename}/download?source={source}"
    if format == "mp4":
        download_path += "&format=mp4"

    # MP4 轉換需要更長時間
    timeout_ms = 120000 if format == "mp4" else 60000

    proxy_request = {
        "type": "proxy_playback",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": "GET",
            "path": download_path,
            "headers": {},
            "body_b64": None,
            "timeout_ms": timeout_ms
        }
    }

    logger.info(f"[recordings] Download request for {filename} (format={format}) from {device_id}")
    await manager.send_to_device(device_id, proxy_request)

    dl_filename = filename.replace(".ts", ".mp4") if format == "mp4" else filename
    media_type = "video/mp4" if format == "mp4" else "video/mp2t"

    # 立即回傳 StreamingResponse，在 generator 裡等資料
    # 這樣 HTTP headers 馬上送出，Cloudflare 不會斷線
    async def generate_download():
        max_wait = 480 if format == "mp4" else 240  # metadata 最多等 240/120 秒

        for attempt in range(max_wait):
            resp_data = await redis_client.get(f"playback:response:{rid}")
            if resp_data:
                if "error" in resp_data:
                    await redis_client.delete(f"playback:response:{rid}")
                    logger.error(f"[download] Device error: {resp_data['error']}")
                    return

                if resp_data.get("chunked"):
                    # 分塊傳輸
                    total_chunks = resp_data["total_chunks"]
                    total_size = resp_data.get("total_size", 0)
                    logger.info(f"[download] Streaming {total_chunks} chunks ({total_size} bytes) for {filename}")

                    for i in range(total_chunks):
                        for _ in range(120):  # 每個 chunk 最多等 60 秒
                            chunk_b64 = await redis_client.get(f"playback:chunk:{rid}:{i}")
                            if chunk_b64:
                                await redis_client.delete(f"playback:chunk:{rid}:{i}")
                                yield base64.b64decode(chunk_b64)
                                break
                            await asyncio.sleep(0.5)
                        else:
                            logger.error(f"[download] Timeout waiting for chunk {i}/{total_chunks}")
                            break
                    await redis_client.delete(f"playback:response:{rid}")
                else:
                    # 普通回應（小檔案）
                    await redis_client.delete(f"playback:response:{rid}")
                    body_b64 = resp_data.get("body_b64", "")
                    if body_b64:
                        yield base64.b64decode(body_b64)
                return
            await asyncio.sleep(0.5)

        logger.error(f"[download] Timeout waiting for device response for {filename}")

    return StreamingResponse(
        generate_download(),
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename={dl_filename}"
        }
    )


# ============ Timeline API ============

@app.get("/api/devices/{device_id}/recordings/timeline")
async def get_device_timeline(
    device_id: str,
    date: str,
    token: str,
    source: str = "cam",
    db: AsyncSession = Depends(get_db)
):
    """
    取得裝置在指定日期的錄影時間軸。
    回傳該日所有錄影片段的起止時間。
    """
    import asyncio
    import base64

    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    rid = str(uuid.uuid4())

    proxy_request = {
        "type": "proxy_playback",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": "GET",
            "path": f"/recordings/timeline?date={date}&source={source}",
            "headers": {},
            "body_b64": None,
            "timeout_ms": 10000
        }
    }

    logger.info(f"[timeline] Fetching timeline for {date} from {device_id}")
    await manager.send_to_device(device_id, proxy_request)

    for attempt in range(20):
        resp_data = await redis_client.get(f"playback:response:{rid}")
        if resp_data:
            await redis_client.delete(f"playback:response:{rid}")
            if "error" in resp_data:
                raise HTTPException(status_code=502, detail=resp_data["error"])

            # Decode body_b64 and return JSON content
            body_b64 = resp_data.get("body_b64", "")
            if body_b64:
                import json
                body = base64.b64decode(body_b64).decode('utf-8')
                return json.loads(body)
            return resp_data
        await asyncio.sleep(0.5)

    raise HTTPException(status_code=504, detail="Timeline API timeout")


async def _prefetch_segments(device_id: str, start: str, segment_names: list):
    """背景預抓 HLS segments 並快取到 Redis（最多 12 個，約 2 分鐘）"""
    prefetch_limit = min(len(segment_names), 12)
    for seg_name in segment_names[:prefetch_limit]:
        cache_key = f"stream:seg:{device_id}:{start}:{seg_name}"
        fetch_key = f"stream:fetching:{device_id}:{start}:{seg_name}"

        # 已快取或正在抓取就跳過
        if await redis_client.exists(cache_key) or await redis_client.exists(fetch_key):
            continue

        # 標記為正在抓取（防止 HLS.js 重複請求）
        await redis_client.set(fetch_key, "1", ex=60)

        rid = str(uuid.uuid4())
        proxy_request = {
            "type": "proxy_playback",
            "ts": datetime.utcnow().isoformat(),
            "payload": {
                "rid": rid,
                "method": "GET",
                "path": f"/recordings/stream/{seg_name}?start={start}",
                "headers": {},
                "body_b64": None,
                "timeout_ms": 60000
            }
        }

        try:
            await manager.send_to_device(device_id, proxy_request)
            for _ in range(120):  # 60 秒
                resp_data = await redis_client.get(f"playback:response:{rid}")
                if resp_data:
                    await redis_client.delete(f"playback:response:{rid}")
                    if "error" not in resp_data:
                        body_b64 = resp_data.get("body_b64", "")
                        await redis_client.set(cache_key, body_b64, ex=300)
                        logger.info(f"[prefetch] Cached {seg_name} for {device_id}")
                    else:
                        logger.warning(f"[prefetch] Error fetching {seg_name}: {resp_data.get('error')}")
                    break
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"[prefetch] Failed to prefetch {seg_name}: {e}")
        finally:
            await redis_client.delete(fetch_key)


@app.get("/api/devices/{device_id}/recordings/stream")
async def get_device_stream_playlist(
    device_id: str,
    start: str,
    token: str,
    end: Optional[str] = None,
    source: str = "cam",
    db: AsyncSession = Depends(get_db)
):
    """
    取得時間範圍的連續 HLS 播放清單。
    實現跨檔連續播放功能。
    """
    import asyncio
    from urllib.parse import quote

    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    rid = str(uuid.uuid4())

    # Build query string
    query = f"start={start}"
    if end:
        query += f"&end={end}"
    query += f"&source={source}"

    proxy_request = {
        "type": "proxy_playback",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": "GET",
            "path": f"/recordings/stream?{query}",
            "headers": {},
            "body_b64": None,
            "timeout_ms": 120000  # 合併 HLS 可能需要較長時間
        }
    }

    logger.info(f"[stream] Fetching stream playlist from {start} to {end} for {device_id}")
    await manager.send_to_device(device_id, proxy_request)

    for attempt in range(240):  # 最多等待 120 秒
        resp_data = await redis_client.get(f"playback:response:{rid}")
        if resp_data:
            await redis_client.delete(f"playback:response:{rid}")

            if "error" in resp_data:
                raise HTTPException(status_code=502, detail=resp_data["error"])

            # 回傳 m3u8 播放清單內容
            body_b64 = resp_data.get("body_b64", "")
            body = base64.b64decode(body_b64).decode('utf-8') if body_b64 else ""

            # 解析 segment 名稱，用於背景預抓
            segment_names = []
            encoded_start = quote(start, safe='')
            lines = body.split('\n')
            fixed_lines = []
            for line in lines:
                if line.strip().endswith('.ts') and not line.startswith('#'):
                    segment_name = line.strip()
                    segment_names.append(segment_name)
                    fixed_lines.append(f"stream/{segment_name}?start={encoded_start}&token={token}")
                else:
                    fixed_lines.append(line)
            body = '\n'.join(fixed_lines)

            # 背景預抓所有 segments（不阻塞回應）
            if segment_names:
                logger.info(f"[stream] Starting prefetch of {len(segment_names)} segments for {device_id}")
                asyncio.create_task(_prefetch_segments(device_id, start, segment_names))

            return Response(
                content=body,
                media_type="application/vnd.apple.mpegurl"
            )
        await asyncio.sleep(0.5)

    raise HTTPException(status_code=504, detail="Stream playlist timeout")


@app.get("/api/devices/{device_id}/recordings/stream/{segment}")
async def get_device_stream_segment(
    device_id: str,
    segment: str,
    start: str,
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """取得合併串流的分段檔案"""
    import asyncio

    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    # 先檢查 Redis 快取
    cache_key = f"stream:seg:{device_id}:{start}:{segment}"
    fetch_key = f"stream:fetching:{device_id}:{start}:{segment}"

    cached = await redis_client.get(cache_key)
    if cached:
        logger.info(f"[stream] Cache hit for {segment}")
        body = base64.b64decode(cached) if cached else b""
        return Response(content=body, media_type="video/mp2t")

    # 如果預抓正在進行，等它完成而不是發新請求
    if await redis_client.exists(fetch_key):
        logger.info(f"[stream] Waiting for prefetch of {segment}")
        for _ in range(120):  # 最多等 60 秒
            cached = await redis_client.get(cache_key)
            if cached:
                logger.info(f"[stream] Prefetch completed for {segment}")
                body = base64.b64decode(cached)
                return Response(content=body, media_type="video/mp2t")
            await asyncio.sleep(0.5)
        # 預抓超時，繼續走正常流程
        logger.warning(f"[stream] Prefetch timeout for {segment}, falling through")

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    # 標記正在抓取
    await redis_client.set(fetch_key, "1", ex=60)

    rid = str(uuid.uuid4())
    proxy_request = {
        "type": "proxy_playback",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": "GET",
            "path": f"/recordings/stream/{segment}?start={start}",
            "headers": {},
            "body_b64": None,
            "timeout_ms": 60000
        }
    }

    await manager.send_to_device(device_id, proxy_request)

    for attempt in range(120):  # 60 秒 timeout
        resp_data = await redis_client.get(f"playback:response:{rid}")
        if resp_data:
            await redis_client.delete(f"playback:response:{rid}")
            await redis_client.delete(fetch_key)

            if "error" in resp_data:
                raise HTTPException(status_code=502, detail=resp_data["error"])

            body_b64 = resp_data.get("body_b64", "")
            body = base64.b64decode(body_b64) if body_b64 else b""

            # 快取到 Redis（5 分鐘 TTL）
            await redis_client.set(cache_key, body_b64, ex=300)

            return Response(
                content=body,
                media_type="video/mp2t"
            )
        await asyncio.sleep(0.5)

    await redis_client.delete(fetch_key)
    raise HTTPException(status_code=504, detail="Stream segment timeout")


# ============ LiveKit SFU Endpoints ============

@app.get("/api/config/streaming-mode")
async def get_streaming_mode():
    """Return current streaming mode so the web client knows which path to use."""
    # Priority: Redis override > static env var
    redis_mode = await redis_client.get("config:streaming_mode")
    if redis_mode in ("p2p", "livekit"):
        return {"mode": redis_mode}
    return {"mode": "livekit" if settings.LIVEKIT_ENABLED else "p2p"}


class StreamingModeRequest(BaseModel):
    mode: str  # "p2p" or "livekit"


@app.post("/api/config/streaming-mode")
async def set_streaming_mode(
    req: StreamingModeRequest,
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """Switch streaming mode between P2P and LiveKit (requires login)."""
    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if req.mode not in ("p2p", "livekit"):
        raise HTTPException(status_code=400, detail="mode must be 'p2p' or 'livekit'")

    # Store in Redis (persists across restarts if Redis has persistence)
    await redis_client.set("config:streaming_mode", req.mode)
    logger.info(f"[config] Streaming mode switched to '{req.mode}' by user {user.username}")

    # Apply to all online devices
    online_devices = manager.get_online_devices()

    if req.mode == "livekit":
        # Switch to LiveKit: create ingress for each online device
        from app.websocket_handler import _get_livekit_service
        lk = _get_livekit_service()
        if lk:
            for device_id in online_devices:
                try:
                    ingress_info = await lk.create_ingress(device_id)
                    await redis_client.set(
                        f"device:livekit_ingress:{device_id}",
                        ingress_info["ingress_id"]
                    )
                    await manager.send_to_device(device_id, {
                        "type": "livekit_ingress",
                        "ts": datetime.utcnow().isoformat(),
                        "payload": {
                            "rtmp_url": ingress_info["rtmp_url"],
                            "stream_key": ingress_info["stream_key"],
                        }
                    })
                    logger.info(f"[config] Created ingress for {device_id}")
                except Exception as e:
                    logger.error(f"[config] Failed to create ingress for {device_id}: {e}")
        else:
            logger.warning("[config] LiveKit service not available")

    elif req.mode == "p2p":
        # Switch to P2P: stop RTMP on all devices, delete ingresses
        from app.websocket_handler import _get_livekit_service
        lk = _get_livekit_service()
        for device_id in online_devices:
            # Tell device to stop RTMP push
            await manager.send_to_device(device_id, {
                "type": "stop_rtmp",
                "ts": datetime.utcnow().isoformat(),
                "payload": {}
            })
            # Clean up ingress
            if lk:
                try:
                    ingress_id = await redis_client.get(f"device:livekit_ingress:{device_id}")
                    if ingress_id:
                        await lk.delete_ingress(ingress_id)
                        await redis_client.delete(f"device:livekit_ingress:{device_id}")
                        logger.info(f"[config] Deleted ingress for {device_id}")
                except Exception as e:
                    logger.error(f"[config] Failed to delete ingress for {device_id}: {e}")

    return {"success": True, "mode": req.mode}


@app.get("/api/devices/{device_id}/livekit-token")
async def get_livekit_token(
    device_id: str,
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a LiveKit viewer token for a device. Only available when LIVEKIT_ENABLED=true."""
    if not settings.LIVEKIT_ENABLED:
        raise HTTPException(status_code=404, detail="LiveKit not enabled")

    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    from app.livekit_service import generate_viewer_token

    lk_token = generate_viewer_token(device_id, str(user.id))
    return {
        "token": lk_token,
        "url": settings.LIVEKIT_PUBLIC_URL,
        "room": f"device-{device_id}",
    }


class SwitchStreamRequest(BaseModel):
    stream_src: str  # e.g. cam, cam_sub, cam2, cam2_sub


@app.post("/api/devices/{device_id}/livekit-switch-stream")
async def livekit_switch_stream(
    device_id: str,
    req: SwitchStreamRequest,
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Notify the device to switch its RTMP push to a different RTSP source.
    This affects all viewers since there is a single ingress per device.
    """
    if not settings.LIVEKIT_ENABLED:
        raise HTTPException(status_code=404, detail="LiveKit not enabled")

    user = await get_current_user(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    await verify_device_ownership(db, user, device_id)

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    await manager.send_to_device(device_id, {
        "type": "livekit_switch_stream",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "stream_src": req.stream_src
        }
    })

    return {"success": True, "message": f"Switching to {req.stream_src}"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
