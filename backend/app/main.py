from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
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


# Startup/Shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize connections on startup"""
    await redis_client.connect()
    print("Redis connected")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up connections on shutdown"""
    await redis_client.disconnect()
    print("Redis disconnected")


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
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


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

        # Handle messages
        while True:
            data = await websocket.receive_json()
            await handle_device_message(device_id, data, db)

    except WebSocketDisconnect:
        if device_id:
            await manager.disconnect_device(device_id, db)
    except Exception as e:
        print(f"Device WebSocket error: {e}")
        if device_id:
            await manager.disconnect_device(device_id, db)


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
            print(f"Viewer {user_id} sent message: {data.get('type')}")
            await handle_viewer_message(user_id, data, db)

    except WebSocketDisconnect:
        if user_id:
            await manager.disconnect_viewer(user_id, db)
    except Exception as e:
        print(f"Viewer WebSocket error: {e}")
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


@app.post("/api/devices/{device_id}/ptz")
async def ptz_control(
    device_id: str,
    ptz_request: PTZControlRequest,
    db: AsyncSession = Depends(get_db)
):
    """Send PTZ control command to device"""
    import uuid

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
            "preset": ptz_request.preset
        }
    }

    print(f"[ptz] Sending {ptz_request.action} to device {device_id}, rid={rid}")
    await manager.send_to_device(device_id, ptz_message)

    # Wait for response (poll redis)
    from app.redis_client import redis_client
    import asyncio

    for attempt in range(20):  # Wait up to 10 seconds (20 * 0.5s)
        resp_data = await redis_client.get(f"ptz:response:{rid}")
        if resp_data:
            print(f"[ptz] Got response for rid={rid}")
            await redis_client.delete(f"ptz:response:{rid}")
            return resp_data

        await asyncio.sleep(0.5)

    # Timeout - return success anyway since PTZ commands are fire-and-forget
    print(f"[ptz] Timeout waiting for response rid={rid}, returning success")
    return {"success": True, "message": "Command sent (no ack)"}


@app.get("/api/devices/{device_id}/gps")
async def get_device_gps(
    device_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get device GPS data"""
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

    # Send proxy request to device
    proxy_request = {
        "type": "proxy_http",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": request.method,
            "path": full_path,
            "headers": dict(request.headers),
            "body_b64": body_b64,
            "timeout_ms": 30000
        }
    }

    print(f"[proxy] Sending request to device {device_id}, rid={rid}, path={full_path}")
    await manager.send_to_device(device_id, proxy_request)

    # Wait for response (poll redis)
    from app.redis_client import redis_client
    import asyncio
    import json as json_module

    for attempt in range(60):  # Wait up to 30 seconds (60 * 0.5s)
        resp_data = await redis_client.get(f"proxy:response:{rid}")
        if resp_data:
            print(f"[proxy] Got response from Redis for rid={rid} after {attempt * 0.5}s")
            # Parse response (already decoded by redis_client.get)
            status = resp_data.get("status", 500)
            resp_headers = resp_data.get("headers", {})
            resp_body_b64 = resp_data.get("body_b64", "")

            # Decode body
            resp_body = base64.b64decode(resp_body_b64) if resp_body_b64 else b""

            # Clean up
            await redis_client.delete(f"proxy:response:{rid}")

            # Normalize header names to lowercase and filter problematic headers
            # These headers will be handled by FastAPI/Starlette automatically
            skip_headers = {'transfer-encoding', 'content-length', 'connection', 'keep-alive', 'date'}
            headers_filtered = {}
            for k, v in resp_headers.items():
                k_lower = k.lower()
                if k_lower not in skip_headers and k_lower not in headers_filtered:
                    headers_filtered[k_lower] = v

            print(f"[proxy] Returning response with filtered headers: {list(headers_filtered.keys())}")

            # Return response - let FastAPI handle content-length automatically
            return Response(
                content=resp_body,
                status_code=status,
                headers=headers_filtered
            )

        await asyncio.sleep(0.5)

    # Timeout
    print(f"[proxy] ✗ Timeout waiting for response rid={rid} from device {device_id}")
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
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """
    Get recordings list for a device.
    Proxies to device's local playback API.
    """
    import asyncio
    import base64

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

    print(f"[recordings] Fetching list from device {device_id}, rid={rid}")
    await manager.send_to_device(device_id, proxy_request)

    # Wait for response
    for attempt in range(20):
        resp_data = await redis_client.get(f"playback:response:{rid}")
        if resp_data:
            print(f"[recordings] Got response for rid={rid}")
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
    db: AsyncSession = Depends(get_db)
):
    """Get recording statistics for a device."""
    import asyncio

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    rid = str(uuid.uuid4())

    proxy_request = {
        "type": "proxy_playback",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": "GET",
            "path": "/recordings/stats",
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
    db: AsyncSession = Depends(get_db)
):
    """Get HLS playlist for a recording (proxies to device)."""
    import asyncio
    import base64

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    rid = str(uuid.uuid4())

    proxy_request = {
        "type": "proxy_playback",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": "GET",
            "path": f"/recordings/{filename}/hls/playlist.m3u8",
            "headers": {},
            "body_b64": None,
            "timeout_ms": 60000  # HLS generation can take time
        }
    }

    print(f"[recordings] Getting HLS playlist for {filename} from {device_id}")
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
    db: AsyncSession = Depends(get_db)
):
    """Get HLS segment for a recording (proxies to device)."""
    import asyncio
    import base64

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
    db: AsyncSession = Depends(get_db)
):
    """
    Download a recording file.
    Note: For large files, consider implementing streaming or direct device connection.
    """
    import asyncio
    import base64

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    rid = str(uuid.uuid4())

    proxy_request = {
        "type": "proxy_playback",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": "GET",
            "path": f"/recordings/{filename}/download",
            "headers": {},
            "body_b64": None,
            "timeout_ms": 60000
        }
    }

    print(f"[recordings] Download request for {filename} from {device_id}")
    await manager.send_to_device(device_id, proxy_request)

    for attempt in range(120):
        resp_data = await redis_client.get(f"playback:response:{rid}")
        if resp_data:
            await redis_client.delete(f"playback:response:{rid}")

            if "error" in resp_data:
                raise HTTPException(status_code=502, detail=resp_data["error"])

            body_b64 = resp_data.get("body_b64", "")
            body = base64.b64decode(body_b64) if body_b64 else b""

            return Response(
                content=body,
                media_type="video/mp2t",
                headers={
                    "Content-Disposition": f"attachment; filename={filename}"
                }
            )
        await asyncio.sleep(0.5)

    raise HTTPException(status_code=504, detail="Download timeout")


# ============ Timeline API ============

@app.get("/api/devices/{device_id}/recordings/timeline")
async def get_device_timeline(
    device_id: str,
    date: str,
    db: AsyncSession = Depends(get_db)
):
    """
    取得裝置在指定日期的錄影時間軸。
    回傳該日所有錄影片段的起止時間。
    """
    import asyncio
    import base64

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    rid = str(uuid.uuid4())

    proxy_request = {
        "type": "proxy_playback",
        "ts": datetime.utcnow().isoformat(),
        "payload": {
            "rid": rid,
            "method": "GET",
            "path": f"/recordings/timeline?date={date}",
            "headers": {},
            "body_b64": None,
            "timeout_ms": 10000
        }
    }

    print(f"[timeline] Fetching timeline for {date} from {device_id}")
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


@app.get("/api/devices/{device_id}/recordings/stream")
async def get_device_stream_playlist(
    device_id: str,
    start: str,
    end: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    取得時間範圍的連續 HLS 播放清單。
    實現跨檔連續播放功能。
    """
    import asyncio
    import re
    from urllib.parse import quote

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

    rid = str(uuid.uuid4())

    # Build query string
    query = f"start={start}"
    if end:
        query += f"&end={end}"

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

    print(f"[stream] Fetching stream playlist from {start} to {end} for {device_id}")
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

            # 修正片段 URL 路徑
            # 將 seg_XXXX.ts 改為 stream/seg_XXXX.ts?start=<原始start參數>
            # 這樣 HLS.js 才能正確解析相對路徑
            encoded_start = quote(start, safe='')
            lines = body.split('\n')
            fixed_lines = []
            for line in lines:
                if line.strip().endswith('.ts') and not line.startswith('#'):
                    # 這是片段檔案名稱，加上正確的路徑前綴和參數
                    segment_name = line.strip()
                    fixed_lines.append(f"stream/{segment_name}?start={encoded_start}")
                else:
                    fixed_lines.append(line)
            body = '\n'.join(fixed_lines)

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
    db: AsyncSession = Depends(get_db)
):
    """取得合併串流的分段檔案"""
    import asyncio

    if not manager.is_device_online(device_id):
        raise HTTPException(status_code=503, detail="Device offline")

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

    raise HTTPException(status_code=504, detail="Stream segment timeout")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
