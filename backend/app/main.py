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
        resp_json = await redis_client.get(f"proxy:response:{rid}")
        if resp_json:
            print(f"[proxy] Got response from Redis for rid={rid} after {attempt * 0.5}s")
            # Parse response
            resp_data = json_module.loads(resp_json)
            status = resp_data.get("status", 500)
            resp_headers = resp_data.get("headers", {})
            resp_body_b64 = resp_data.get("body_b64", "")

            # Decode body
            resp_body = base64.b64decode(resp_body_b64) if resp_body_b64 else b""

            # Clean up
            await redis_client.delete(f"proxy:response:{rid}")

            # Return response
            return Response(
                content=resp_body,
                status_code=status,
                headers=resp_headers
            )

        await asyncio.sleep(0.5)

    # Timeout
    print(f"[proxy] ✗ Timeout waiting for response rid={rid} from device {device_id}")
    raise HTTPException(status_code=504, detail="Proxy timeout")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
