# MuMu Camera Project Structure

Complete directory structure of the mumu-cam monorepo.

```
mumu-cam/
├── README.md                       # Main project documentation
├── .env.example                    # Environment variables template
├── docker-compose.yml              # Docker Compose configuration
├── PROJECT_STRUCTURE.md            # This file
│
├── backend/                        # Central signaling server
│   ├── Dockerfile                  # Backend container build
│   ├── requirements.txt            # Python dependencies
│   ├── README.md                   # Backend documentation
│   ├── alembic.ini                 # Alembic configuration
│   │
│   ├── alembic/                    # Database migrations
│   │   ├── env.py                  # Migration environment
│   │   ├── script.py.mako          # Migration template
│   │   └── versions/
│   │       └── 001_initial_schema.py  # Initial DB schema
│   │
│   └── app/                        # Application code
│       ├── __init__.py
│       ├── main.py                 # FastAPI application entry
│       ├── config.py               # Configuration settings
│       ├── database.py             # Database connection & session
│       ├── models.py               # SQLAlchemy models
│       ├── auth.py                 # Authentication (JWT, bcrypt)
│       ├── redis_client.py         # Redis client wrapper
│       ├── turn_credentials.py     # TURN credential generation
│       └── websocket_handler.py    # WebSocket message handling
│
├── device-agent/                   # Camera device agent
│   ├── Dockerfile                  # Agent container build
│   ├── requirements.txt            # Python dependencies
│   ├── README.md                   # Agent documentation
│   └── agent.py                    # Main agent application
│
├── web/                            # Web client (viewer)
│   ├── index.html                  # Main application page
│   ├── login.html                  # Login/register page
│   ├── app.js                      # Application logic
│   ├── webrtc.js                   # WebRTC connection handling
│   ├── styles.css                  # Styling
│   └── README.md                   # Web client documentation
│
├── nginx/                          # Reverse proxy
│   ├── Dockerfile                  # Nginx container build
│   ├── nginx.conf                  # Nginx configuration
│   └── README.md                   # Nginx documentation
│
├── coturn/                         # TURN server
│   ├── turnserver.conf             # Coturn configuration
│   └── README.md                   # TURN server documentation
│
└── docs/                           # Comprehensive documentation
    ├── architecture.md             # System architecture & design
    ├── api.md                      # REST API reference
    ├── websocket.md                # WebSocket protocol spec
    ├── database.md                 # Database schema & queries
    ├── redis.md                    # Redis keys & data structures
    ├── turn.md                     # TURN server configuration
    └── security.md                 # Security best practices
```

## Component Breakdown

### Backend (FastAPI + PostgreSQL + Redis)

**Purpose**: Central signaling server for WebSocket communication, authentication, and session management.

**Key Files**:
- `app/main.py`: REST API endpoints, WebSocket endpoints
- `app/websocket_handler.py`: Message routing between devices and viewers
- `app/models.py`: Database models (users, devices, sessions, etc.)
- `app/turn_credentials.py`: Dynamic TURN credential generation

**Dependencies**:
- FastAPI: Web framework
- SQLAlchemy: ORM
- Alembic: Database migrations
- Redis: Presence tracking
- JWT: Authentication

### Device Agent (Python + aiortc)

**Purpose**: Runs on camera devices, captures video, creates WebRTC connections to stream to viewers.

**Key Files**:
- `agent.py`: Complete agent implementation
  - WebSocket client with reconnection
  - WebRTC peer connection management
  - Fake video source (replace with real camera)

**Dependencies**:
- aiortc: WebRTC implementation
- websockets: WebSocket client
- opencv-python: Video processing

### Web Client (Vanilla JavaScript)

**Purpose**: Browser-based viewer interface for watching camera streams.

**Key Files**:
- `index.html`: Main app (device list, video player)
- `login.html`: Authentication UI
- `app.js`: Device management, authentication
- `webrtc.js`: WebRTC connection handling
- `styles.css`: Responsive design

**Dependencies**: None (vanilla JS)

### Nginx

**Purpose**: Reverse proxy for serving web client and proxying API/WebSocket requests.

**Key Files**:
- `nginx.conf`: Complete configuration
  - Static file serving
  - API proxying
  - WebSocket proxying
  - Security headers

### Coturn (TURN Server)

**Purpose**: NAT traversal server for WebRTC connections when direct P2P fails.

**Key Files**:
- `turnserver.conf`: Complete TURN configuration
  - Dynamic credential support
  - Port range settings
  - Security settings

### Documentation

**Purpose**: Comprehensive technical documentation.

**Files**:
- `architecture.md`: System design, data flows, diagrams
- `api.md`: REST API reference with examples
- `websocket.md`: WebSocket protocol specification
- `database.md`: Database schema, migrations, queries
- `redis.md`: Redis keys, data structures, operations
- `turn.md`: TURN server setup, credentials, testing
- `security.md`: Security model, best practices, checklist

## Quick Start

```bash
# 1. Clone and setup
cd mumu-cam
cp .env.example .env
# Edit .env with your settings

# 2. Start all services
docker-compose up -d

# 3. Initialize database
docker-compose exec backend alembic upgrade head

# 4. Access
# - Web UI: http://localhost:8080
# - API Docs: http://localhost:8000/docs

# 5. Run device agent (outside Docker)
cd device-agent
pip install -r requirements.txt
python agent.py --backend ws://localhost:8000/ws/device
```

## File Count Summary

- **Backend**: 12 files (app code, migrations, config)
- **Device Agent**: 3 files (agent, Dockerfile, requirements)
- **Web Client**: 6 files (HTML, JS, CSS)
- **Nginx**: 3 files (Dockerfile, config, README)
- **Coturn**: 2 files (config, README)
- **Docs**: 7 files (architecture, API, WebSocket, DB, Redis, TURN, security)
- **Root**: 4 files (README, .env.example, docker-compose.yml, this file)

**Total**: ~37 files

## Lines of Code (Approximate)

- **Backend Python**: ~1,500 lines
- **Device Agent Python**: ~400 lines
- **Web Client JS**: ~600 lines
- **Web Client HTML/CSS**: ~400 lines
- **Configuration**: ~300 lines
- **Documentation**: ~3,500 lines

**Total**: ~6,700 lines

## Technology Stack Summary

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI, Python 3.11, asyncio |
| Database | PostgreSQL 15 |
| Cache | Redis 7 |
| Device Agent | Python 3.11, aiortc, websockets |
| Web Client | HTML5, Vanilla JS, WebRTC API |
| Reverse Proxy | Nginx (Alpine) |
| TURN Server | Coturn |
| Container | Docker, Docker Compose |
| Migrations | Alembic |

## Network Ports

| Service | Port | Protocol | Purpose |
|---------|------|----------|---------|
| Nginx | 8080 | HTTP | Web UI, API proxy |
| Backend | 8000 | HTTP/WS | API, WebSocket |
| PostgreSQL | 5432 | TCP | Database |
| Redis | 6379 | TCP | Cache |
| Coturn | 3478 | UDP/TCP | TURN/STUN |
| Coturn | 5349 | TCP | TURNS (TLS) |
| Coturn | 49152-65535 | UDP | Media relay |

## Environment Variables

See `.env.example` for complete list. Key variables:

- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`: Database credentials
- `REDIS_URL`: Redis connection string
- `TURN_HOST`, `TURN_PORT`, `TURN_SECRET`: TURN server settings
- `JWT_SECRET`: JWT signing key
- `BACKEND_CORS_ORIGINS`: Allowed CORS origins

## Development vs Production

### Development (Current Setup)

- HTTP (port 8080)
- WS (not WSS)
- Default passwords
- Verbose logging
- Auto-reload enabled

### Production Requirements

- HTTPS with valid certificate
- WSS (secure WebSocket)
- Strong secrets (generate with `openssl rand -hex 32`)
- Error-only logging
- No auto-reload
- Firewall configured
- Rate limiting enabled
- Regular backups

## Next Steps

1. **Test the system**: Follow Quick Start guide
2. **Customize**: Update `.env` with your settings
3. **Deploy**: Follow production checklist in `docs/security.md`
4. **Integrate camera**: Replace fake video source in device-agent
5. **Scale**: Add multiple backend instances, TURN servers

## Support

- See component README files for detailed documentation
- Check `docs/` for comprehensive guides
- Review `docker-compose logs` for troubleshooting
- Open issues on GitHub (if applicable)
