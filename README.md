# MuMu Camera System

A distributed WebRTC-based camera streaming system with peer-to-peer video transmission using TURN relay.

## Architecture Overview

- **Backend**: Central signaling server (FastAPI + WebSocket)
- **Device Agent**: Python-based camera agent with aiortc WebRTC implementation
- **Web Client**: Browser-based viewer with native WebRTC
- **TURN Server**: Coturn for NAT traversal
- **Database**: PostgreSQL for persistent data
- **Cache**: Redis for presence tracking and session management

**Key Design Principle**: The central server does NOT relay video streams. All media flows peer-to-peer through WebRTC (with TURN fallback).

## 🎯 No Raspberry Pi? No Problem!

You can test the **complete system** using the **Device Simulator** with fake video - no camera hardware needed!

**Quick test (5 minutes)**:
```bash
# Start services
docker-compose up -d
docker-compose exec backend alembic upgrade head

# Start device simulator
docker-compose --profile sim up -d device-sim

# Open browser: http://localhost:8080
# Register → Login → Pair Device → Watch!
```

See **[QUICKSTART_SIMULATOR.md](QUICKSTART_SIMULATOR.md)** for detailed instructions.

**Features**:
- ✅ Moving box animation (no camera needed)
- ✅ Full WebRTC peer-to-peer streaming
- ✅ Complete signaling flow (WebSocket, SDP, ICE)
- ✅ TURN server integration
- ✅ Works on Windows/Mac/Linux

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Git

### 1. Clone and Setup

```bash
git clone <your-repo>
cd mumu-cam
cp .env.example .env
# Edit .env with your configuration
```

### 2. Start All Services

```bash
docker-compose up -d
```

This starts:
- PostgreSQL (port 5432)
- Redis (port 6379)
- Coturn TURN server (port 3478)
- Backend API (port 8000)
- Nginx web server (port 8080)

### 3. Initialize Database

```bash
docker-compose exec backend alembic upgrade head
```

### 4. Access the System

- **Web Interface**: http://localhost:8080
- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs

### 5. Run Device Agent (Outside Docker)

```bash
cd device-agent
pip install -r requirements.txt
python agent.py --backend ws://localhost:8000/ws/device
```

## Project Structure

```
mumu-cam/
├── backend/          # Central signaling server
├── device-agent/     # Camera device agent
├── web/              # Web client interface
├── nginx/            # Reverse proxy configuration
├── coturn/           # TURN server configuration
├── docs/             # Comprehensive documentation
└── docker-compose.yml
```

## Development Workflow

### Backend Development

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Run Database Migrations

```bash
cd backend
alembic revision --autogenerate -m "Your migration message"
alembic upgrade head
```

### Device Agent Development

```bash
cd device-agent
pip install -r requirements.txt
python agent.py --backend ws://localhost:8000/ws/device --device-id test-device-001
```

### Web Client Development

Simply edit files in `web/` directory. Nginx auto-serves them.

## Testing

### Test WebSocket Connection

```bash
# Install wscat
npm install -g wscat

# Test device connection
wscat -c ws://localhost:8000/ws/device

# Test viewer connection
wscat -c ws://localhost:8000/ws/viewer
```

### Test TURN Server

```bash
docker-compose exec coturn turnutils_uclient -v coturn 3478
```

## Documentation

See the `docs/` directory for comprehensive documentation:

- [Architecture](docs/architecture.md) - System design and component interaction
- [API Reference](docs/api.md) - REST API endpoints
- [WebSocket Protocol](docs/websocket.md) - Message formats and flows
- [Database Schema](docs/database.md) - Tables and relationships
- [Redis Keys](docs/redis.md) - Cache structure
- [TURN Configuration](docs/turn.md) - NAT traversal setup
- [Security](docs/security.md) - Authentication and authorization

## Production Deployment

1. **Update secrets** in `.env`:
   - Change `TURN_SECRET`
   - Change `JWT_SECRET`
   - Use strong `POSTGRES_PASSWORD`

2. **Enable HTTPS** on nginx

3. **Configure TURN** for your public IP

4. **Scale services** as needed:
   ```bash
   docker-compose up -d --scale backend=3
   ```

## Troubleshooting

### WebRTC Connection Issues

1. Check TURN server is reachable: `telnet <public-ip> 3478`
2. Verify ICE servers in browser console
3. Check firewall rules for UDP ports 49152-65535

### Backend Connection Issues

1. Check logs: `docker-compose logs backend`
2. Verify Redis: `docker-compose exec redis redis-cli ping`
3. Verify PostgreSQL: `docker-compose exec postgres pg_isready`

## License

MIT

## Contributing

Please read the documentation in `docs/` before contributing.
