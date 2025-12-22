# MuMu Camera Backend

Central signaling server for the MuMu Camera system. Handles WebSocket signaling, user authentication, device management, and watch session coordination.

## Features

- **WebSocket Signaling**: Separate endpoints for devices (`/ws/device`) and viewers (`/ws/viewer`)
- **User Authentication**: JWT-based authentication with bcrypt password hashing
- **Device Management**: Device registration, pairing codes, and ownership tracking
- **Session Management**: Watch session lifecycle management
- **TURN Credentials**: Dynamic TURN credential generation using HMAC-SHA1
- **Presence Tracking**: Redis-based online/offline status tracking
- **Database**: PostgreSQL with async SQLAlchemy and Alembic migrations

## Architecture

The backend does NOT relay video streams. It only:
- Manages WebSocket signaling between devices and viewers
- Authenticates users and devices
- Tracks device presence and online status
- Generates dynamic TURN credentials for NAT traversal
- Coordinates watch session lifecycle

All video data flows peer-to-peer via WebRTC (using TURN when necessary).

## Prerequisites

- Python 3.11+
- PostgreSQL 15+
- Redis 7+

## Installation

### Development Setup

```bash
cd backend
pip install -r requirements.txt
```

### Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
DATABASE_URL=postgresql+asyncpg://mumucam:mumucam123@localhost:5432/mumucam
REDIS_URL=redis://localhost:6379/0
TURN_HOST=coturn
TURN_PORT=3478
TURN_SECRET=your_turn_secret_key
JWT_SECRET=your_jwt_secret_key
```

### Database Migrations

Initialize database schema:

```bash
alembic upgrade head
```

Create a new migration (after model changes):

```bash
alembic revision --autogenerate -m "Description of changes"
alembic upgrade head
```

## Running

### Development

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Production (with Docker)

```bash
docker-compose up backend
```

## API Endpoints

### Authentication

- `POST /api/auth/register` - Register new user
- `POST /api/auth/login` - Login user

### Device Management

- `POST /api/devices/register` - Register new device
- `GET /api/devices?token={token}` - Get user's devices
- `POST /api/devices/pair` - Pair device using pairing code
- `GET /api/devices/{device_id}/status` - Get device online status

### Pairing

- `POST /api/pairing/generate?device_id={device_id}` - Generate pairing code

### WebSocket

- `WS /ws/device` - Device WebSocket endpoint
- `WS /ws/viewer` - Viewer WebSocket endpoint

## WebSocket Protocol

### Message Format

All messages follow this structure:

```json
{
  "type": "message_type",
  "request_id": "optional_request_id",
  "ts": "2025-01-01T00:00:00.000000",
  "payload": {}
}
```

### Device Messages

**Hello** (initial connection):
```json
{
  "type": "hello",
  "ts": "...",
  "payload": {
    "device_id": "device-001"
  }
}
```

**Heartbeat**:
```json
{
  "type": "heartbeat",
  "ts": "...",
  "payload": {}
}
```

**Signal Answer** (SDP):
```json
{
  "type": "signal_answer",
  "ts": "...",
  "payload": {
    "session_id": "uuid",
    "sdp": "..."
  }
}
```

**Signal ICE**:
```json
{
  "type": "signal_ice",
  "ts": "...",
  "payload": {
    "session_id": "uuid",
    "candidate": {...}
  }
}
```

### Viewer Messages

**Hello**:
```json
{
  "type": "hello",
  "ts": "...",
  "payload": {
    "token": "jwt_token"
  }
}
```

**Watch Request**:
```json
{
  "type": "watch_request",
  "ts": "...",
  "payload": {
    "device_id": "device-001"
  }
}
```

**Signal Offer**:
```json
{
  "type": "signal_offer",
  "ts": "...",
  "payload": {
    "session_id": "uuid",
    "sdp": "..."
  }
}
```

**End Watch**:
```json
{
  "type": "end_watch",
  "ts": "...",
  "payload": {
    "session_id": "uuid"
  }
}
```

## Testing

### Test WebSocket Connection

Using `wscat`:

```bash
# Device connection
wscat -c ws://localhost:8000/ws/device
> {"type":"hello","ts":"2025-01-01T00:00:00","payload":{"device_id":"test-001"}}

# Viewer connection
wscat -c ws://localhost:8000/ws/viewer
> {"type":"hello","ts":"2025-01-01T00:00:00","payload":{"token":"your_jwt_token"}}
```

### Test REST API

```bash
# Register user
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","email":"test@example.com","password":"password123"}'

# Login
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","password":"password123"}'
```

## Database Schema

### Tables

- **users**: User accounts
- **devices**: Registered devices
- **device_tokens**: Device authentication tokens
- **device_ownership**: User-device ownership mapping
- **pairing_codes**: Temporary pairing codes for device setup
- **watch_sessions**: Active and historical watch sessions

See `docs/database.md` for detailed schema.

## Redis Keys

### Presence Tracking

- `devices:online` (hash): Online devices with timestamps
- `device:presence:{device_id}` (hash): Device presence data

### Session Data

- `session:{session_id}` (hash): Active session metadata

See `docs/redis.md` for detailed Redis structure.

## Troubleshooting

### Database Connection Issues

```bash
# Test PostgreSQL connection
docker-compose exec postgres pg_isready -U mumucam

# Check logs
docker-compose logs backend
```

### Redis Connection Issues

```bash
# Test Redis connection
docker-compose exec redis redis-cli ping

# Check if Redis is enabled
# Set REDIS_ENABLED=false in .env to use in-memory fallback
```

### Migration Issues

```bash
# Reset migrations (WARNING: destroys data)
docker-compose exec backend alembic downgrade base
docker-compose exec backend alembic upgrade head

# Check current migration version
docker-compose exec backend alembic current
```

## Security Considerations

1. **Change secrets in production**:
   - `JWT_SECRET`
   - `TURN_SECRET`
   - `POSTGRES_PASSWORD`

2. **Enable HTTPS** when deploying to production

3. **Use strong passwords** for user accounts

4. **Implement rate limiting** for API endpoints (consider using nginx or middleware)

5. **Regular security updates** for dependencies

## Performance Tuning

### Database Connection Pool

Adjust in `app/database.py`:

```python
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,  # Increase for more concurrent connections
    max_overflow=40
)
```

### Redis Connection Pool

Adjust in `app/redis_client.py` if needed.

### WebSocket Heartbeat

Configure in `.env`:

```
WS_HEARTBEAT_INTERVAL=30
WS_HEARTBEAT_TIMEOUT=90
```

## License

MIT
