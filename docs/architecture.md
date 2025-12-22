# MuMu Camera System Architecture

## Overview

MuMu Camera is a distributed WebRTC-based camera streaming system designed for peer-to-peer video transmission with centralized signaling and device management.

## Core Design Principles

1. **No Video Relay Through Central Server**: The backend does NOT relay video streams. All media flows peer-to-peer via WebRTC.
2. **Signaling Only**: Central server only handles WebSocket signaling, authentication, and session coordination.
3. **NAT Traversal**: TURN server provides relay fallback when direct P2P fails.
4. **Scalable**: Stateless backend design allows horizontal scaling.
5. **Robust**: Automatic reconnection with exponential backoff on all components.

## System Components

### 1. Backend (Central Server)

**Technology**: FastAPI + WebSocket + PostgreSQL + Redis

**Responsibilities**:
- User authentication (JWT)
- Device registration and pairing
- WebSocket signaling between devices and viewers
- Watch session lifecycle management
- Dynamic TURN credential generation
- Device presence tracking

**NOT Responsible For**:
- Video/audio relay
- Media transcoding
- Recording storage

### 2. Device Agent

**Technology**: Python + aiortc + WebRTC

**Responsibilities**:
- Connect to backend via WebSocket
- Capture video from camera
- Create WebRTC peer connections
- Send video tracks to viewers
- Handle reconnection on network failures

### 3. Web Client

**Technology**: HTML5 + Vanilla JavaScript + WebRTC

**Responsibilities**:
- User authentication UI
- Device management interface
- Watch session initiation
- WebRTC peer connection (receiver side)
- Video playback

### 4. TURN Server

**Technology**: Coturn

**Responsibilities**:
- STUN (NAT type detection)
- TURN (media relay when P2P fails)
- Dynamic credential validation

### 5. PostgreSQL Database

**Responsibilities**:
- User accounts
- Device registry
- Device ownership
- Pairing codes
- Watch session history

### 6. Redis

**Responsibilities**:
- Online device tracking
- Active session metadata
- Temporary data caching

### 7. Nginx

**Responsibilities**:
- Static file serving (web client)
- Reverse proxy (API requests)
- WebSocket proxy
- Load balancing (optional)

## Architecture Diagram

```
┌─────────────┐                                    ┌─────────────┐
│   Device    │                                    │   Viewer    │
│   Agent     │                                    │ Web Client  │
└──────┬──────┘                                    └──────┬──────┘
       │                                                  │
       │ WebSocket                                        │ WebSocket
       │ (Signaling)                                      │ (Signaling)
       │                                                  │
       ├──────────────────────┬───────────────────────────┤
       │                      │                           │
       │              ┌───────▼────────┐                  │
       │              │     Nginx      │                  │
       │              │ Reverse Proxy  │                  │
       │              └───────┬────────┘                  │
       │                      │                           │
       │              ┌───────▼────────┐                  │
       │              │    Backend     │                  │
       │              │   (FastAPI)    │                  │
       │              └───┬────────┬───┘                  │
       │                  │        │                      │
       │          ┌───────▼──┐  ┌──▼────────┐            │
       │          │PostgreSQL│  │   Redis   │            │
       │          │    DB    │  │  Cache    │            │
       │          └──────────┘  └───────────┘            │
       │                                                  │
       │                                                  │
       │        WebRTC Media (Direct P2P)                │
       ├──────────────────────────────────────────────────┤
       │                                                  │
       │   Or via TURN if P2P fails                      │
       │              ┌───────────┐                       │
       └──────────────►   TURN   ◄───────────────────────┘
                      │  Server  │
                      │ (Coturn) │
                      └───────────┘
```

## Data Flow

### Device Registration Flow

```
1. Device Agent starts
2. Agent → Backend: HTTP POST /api/devices/register
3. Backend → Database: Create device record
4. Backend → Agent: Return device_id confirmation
```

### Device Connection Flow

```
1. Agent → Backend: WebSocket connect /ws/device
2. Agent → Backend: {"type": "hello", "payload": {"device_id": "..."}}
3. Backend → Database: Verify device exists
4. Backend → Redis: Mark device online
5. Backend → Agent: {"type": "hello_ack"}
6. Agent starts heartbeat loop (every 30s)
```

### Device Pairing Flow

```
1. Agent → Backend: HTTP POST /api/pairing/generate?device_id=X
2. Backend → Database: Create pairing code (6 digits, 5 min TTL)
3. Backend → Agent: Return pairing code
4. Agent displays code to user

5. User enters code in web client
6. Web → Backend: HTTP POST /api/devices/pair
7. Backend → Database: Verify code, create ownership
8. Backend → Web: Success response
```

### Watch Session Flow

```
1. Viewer → Backend: WS {"type": "watch_request", "payload": {"device_id": "X"}}
2. Backend → Database: Create watch session
3. Backend → Redis: Store session metadata
4. Backend → Viewer: {"type": "watch_ready", "payload": {"session_id": "...", "ice_servers": [...]}}
5. Backend → Device: {"type": "watch_request", "payload": {"session_id": "...", "ice_servers": [...]}}

6. Viewer creates RTCPeerConnection
7. Viewer → Backend: {"type": "signal_offer", "payload": {"sdp": {...}}}
8. Backend → Device: Forward SDP offer
9. Device creates answer
10. Device → Backend: {"type": "signal_answer", "payload": {"sdp": {...}}}
11. Backend → Viewer: Forward SDP answer

12. ICE candidates exchanged via Backend (multiple signal_ice messages)

13. WebRTC connection established (P2P or via TURN)
14. Video streaming begins (NOT through backend)

15. Either party ends session
16. Backend → Both: {"type": "watch_ended"}
17. Backend → Database: Update session status
18. Backend → Redis: Clean up session data
```

## Signaling vs Media Paths

**Signaling Path** (through Backend):
```
Device ↔ Backend ↔ Viewer
```

**Media Path** (direct or via TURN):
```
Device ↔ Viewer  (preferred)
Device ↔ TURN ↔ Viewer  (fallback)
```

## State Management

### Backend State
- **Database**: Persistent state (users, devices, sessions)
- **Redis**: Transient state (online devices, active sessions)
- **In-Memory**: WebSocket connections (manager object)

### Device Agent State
- **In-Memory**: Active peer connections, session IDs
- **Persistent**: Device credentials (if implemented)

### Web Client State
- **localStorage**: JWT token, user info
- **In-Memory**: Active peer connection, session

## Scalability Considerations

### Horizontal Scaling

**Backend**:
- Stateless design allows multiple instances
- WebSocket connections distributed via load balancer
- Redis provides shared state
- Sticky sessions NOT required (Redis tracks everything)

**TURN Server**:
- Multiple TURN servers can be deployed
- Backend returns multiple TURN URLs
- Client tries them in order

**Database**:
- Read replicas for scaling reads
- Connection pooling in backend

### Vertical Scaling

**Backend**:
- Increase worker processes/threads
- Tune database connection pool

**TURN Server**:
- High bandwidth, moderate CPU
- More RAM for many concurrent sessions

## Failure Handling

### Device Disconnection
1. Backend detects WebSocket close
2. Mark device offline in DB and Redis
3. End all active watch sessions for that device
4. Notify viewers

### Viewer Disconnection
1. Backend detects WebSocket close
2. End watch sessions for that viewer
3. Notify devices

### Backend Failure
1. Devices reconnect (exponential backoff)
2. Viewers reconnect
3. Existing WebRTC connections continue (they're P2P!)
4. New signaling messages queued until reconnect

### TURN Server Failure
1. WebRTC attempts other ICE candidates
2. Falls back to direct P2P if possible
3. Connection fails only if both direct and TURN fail

### Database Failure
1. Backend continues serving existing connections
2. New connections fail gracefully
3. Redis provides temporary state
4. Recover when DB comes back online

## Security Model

### Authentication
- Users: JWT tokens (HTTP and WebSocket)
- Devices: Device ID + optional token (future)

### Authorization
- Device ownership checked before watch
- Session IDs prevent unauthorized access

### Network Security
- HTTPS/WSS in production
- TURN credentials time-limited (HMAC)
- Firewall on database and Redis

### Data Privacy
- Video never stored on backend
- End-to-end encryption possible (future: DTLS-SRTP)

## Performance Characteristics

### Backend
- **Latency**: <50ms for signaling
- **Throughput**: Thousands of concurrent WebSocket connections
- **Bottleneck**: Database writes (can be optimized with batching)

### Device Agent
- **CPU**: Moderate (video encoding)
- **Memory**: Low (<100MB)
- **Network**: Upstream bandwidth = video bitrate

### Web Client
- **CPU**: Low (hardware-accelerated decoding)
- **Memory**: Moderate
- **Network**: Downstream bandwidth = video bitrate

### TURN Server
- **CPU**: Low
- **Memory**: Moderate
- **Network**: High (relays all media if P2P fails)
- **Bottleneck**: Bandwidth

## Monitoring and Observability

### Backend Metrics
- WebSocket connection count
- Active watch sessions
- Database query latency
- Redis cache hit rate

### Device Agent Metrics
- Connection state
- Reconnection attempts
- WebRTC peer connection state

### TURN Server Metrics
- Active allocations
- Relayed bytes
- Connection failures

### Recommended Tools
- Prometheus + Grafana (metrics)
- Sentry (error tracking)
- ELK Stack (log aggregation)

## Device Simulator Mode

### Why It Exists

The Device Simulator allows testing the complete MuMu Camera system **without any physical camera hardware**. This is crucial for:

- **Development**: Test backend/frontend changes without Raspberry Pi
- **CI/CD**: Automated testing in continuous integration
- **Demos**: Show system capabilities without setup
- **Learning**: Understand WebRTC flows without hardware complexity

### Video Source Modes

The device agent supports three video source modes:

| Mode | Hardware | Use Case | Video Content |
|------|----------|----------|---------------|
| `fake` | None | Development, testing, CI/CD | Moving box animation on color bars |
| `webcam` | System webcam | Desktop testing | Real webcam feed |
| `camera` | Raspberry Pi camera | Production | Real camera feed (future) |

### Fake Video Generation

The `FakeVideoTrack` class generates animated test patterns:

**Features**:
- 640x480 resolution at 30fps
- Color bar background (7 vertical bars)
- White box that bounces around the frame
- Frame counter progress bar in top-left
- Pure CPU rendering (no GPU needed)
- ~5-10% CPU usage on modern hardware

**Implementation**:
```python
class FakeVideoTrack(VideoStreamTrack):
    async def recv(self):
        # Generate color bars background
        img = self._create_color_bars()

        # Draw bouncing box
        self._draw_moving_box(img)

        # Add frame counter
        self._draw_counter(img)

        # Return as av.VideoFrame
        return av.VideoFrame.from_ndarray(img, format='bgr24')
```

### Switching Between Modes

**Command Line**:
```bash
# Simulator mode (no hardware)
python agent.py --video-source fake

# Webcam mode
python agent.py --video-source webcam

# Raspberry Pi camera (future)
python agent.py --video-source camera
```

**Environment Variables**:
```bash
export VIDEO_SOURCE=fake
python agent.py
```

**Docker Compose**:
```yaml
device-sim:
  environment:
    VIDEO_SOURCE: fake  # or webcam
```

### Differences from Real Camera

#### Similarities (Simulator Behaves Identically)
- WebSocket connection and signaling
- WebRTC peer connection setup
- SDP offer/answer exchange
- ICE candidate gathering
- TURN server usage (if needed)
- Session lifecycle management
- Reconnection logic

#### Differences
- Video source only (no audio)
- Synthetic video content
- No hardware initialization
- No camera permissions needed
- Consistent performance (no light/focus issues)

### Deployment Patterns

#### 1. Local Development

```bash
# Terminal 1: Start services
docker-compose up -d

# Terminal 2: Run simulator
cd device-agent
python agent.py --device-id dev-cam-001
```

#### 2. Multiple Simulators

```bash
# Start 3 simulated devices
python agent.py --device-id sim-001 &
python agent.py --device-id sim-002 &
python agent.py --device-id sim-003 &
```

#### 3. Docker Compose with Simulators

```bash
# Start all services + 3 simulators
docker-compose --profile sim up -d
```

#### 4. CI/CD Integration

```yaml
# .github/workflows/e2e-test.yml
- name: Start services
  run: docker-compose up -d

- name: Start simulator
  run: docker-compose --profile sim up -d device-sim

- name: Run E2E tests
  run: npm run test:e2e

- name: Check video streaming
  run: ./test-scripts/verify-webrtc.sh
```

### Migration Path: Simulator → Real Camera

**Phase 1: Development (Simulator)**
```bash
python agent.py --video-source fake
```

**Phase 2: Desktop Testing (Webcam)**
```bash
pip install opencv-python
python agent.py --video-source webcam
```

**Phase 3: Production (Raspberry Pi)**
```bash
# On Raspberry Pi
sudo apt-get install python3-picamera2
python agent.py --video-source camera
```

Only `--video-source` changes. All other code remains the same.

### Performance Characteristics

#### Simulator (fake)
- CPU: 5-10% (single core)
- Memory: ~50-80 MB
- Network: ~1-2 Mbps upstream
- Latency: 50-100ms

#### Webcam
- CPU: 15-25% (encoding)
- Memory: ~80-120 MB
- Network: ~1-3 Mbps upstream
- Latency: 100-200ms

#### Real Camera (Raspberry Pi)
- CPU: 10-20% (hardware encoding)
- Memory: ~60-100 MB
- Network: ~1-2 Mbps upstream
- Latency: 80-150ms

### Testing Recommendations

1. **Always test with simulator first**: Verify signaling, ICE, TURN before hardware
2. **Use multiple simulators**: Test concurrent sessions, load
3. **Test reconnection**: Stop/start backend, verify agent reconnects
4. **Test WebRTC states**: Monitor ICE connection states, peer connection states
5. **Verify video playback**: Check browser console for errors, dropped frames

### Known Limitations

1. **No audio**: Simulator only generates video
2. **Fixed resolution**: 640x480 (can be modified in code)
3. **Synthetic content**: Not suitable for ML/AI training
4. **No camera controls**: Can't adjust brightness, zoom, etc.

### Extending the Simulator

**Custom video patterns**:
```python
class CustomVideoTrack(VideoStreamTrack):
    async def recv(self):
        # Your custom video generation
        img = generate_test_pattern()
        return av.VideoFrame.from_ndarray(img, format='bgr24')
```

**Load from video file**:
```python
from aiortc.contrib.media import MediaPlayer
player = MediaPlayer('test-video.mp4')
pc.addTrack(player.video)
```

**RTSP stream relay**:
```python
player = MediaPlayer('rtsp://camera-ip/stream')
pc.addTrack(player.video)
```

## Future Enhancements

1. **Audio Support**: Two-way audio communication
2. **Recording**: Store streams in cloud storage
3. **Multi-viewer**: Multiple viewers per device
4. **SFU Mode**: Selective Forwarding Unit for broadcast
5. **Edge Computing**: Deploy agents on edge devices
6. **AI Features**: Motion detection, object recognition
7. **Mobile Apps**: Native iOS/Android clients
8. **E2E Encryption**: Additional security layer

## Conclusion

This architecture prioritizes:
- **Simplicity**: Clear separation of concerns
- **Scalability**: Stateless backend, P2P media
- **Reliability**: Robust reconnection, multiple fallbacks
- **Performance**: Direct P2P when possible, minimal latency
- **Security**: Authentication, authorization, encryption ready
