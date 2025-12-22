# MuMu Camera Device Agent

Python-based camera agent using aiortc for WebRTC video streaming.

## Features

- **WebRTC Video Streaming**: Uses aiortc to send video to viewers via peer-to-peer connections
- **Robust Reconnection**: Exponential backoff reconnection strategy
- **Fake Video Source**: Generates test video frames (replace with actual camera in production)
- **WebSocket Signaling**: Connects to central server for signaling
- **TURN Support**: Uses dynamic TURN credentials from server for NAT traversal

## Prerequisites

- Python 3.11+
- System libraries for video processing (see Dockerfile)

## Installation

### Linux/macOS

```bash
cd device-agent

# Install system dependencies (Ubuntu/Debian)
sudo apt-get install -y \
    libavformat-dev \
    libavcodec-dev \
    libavdevice-dev \
    libavutil-dev \
    libswscale-dev \
    libopus-dev \
    libvpx-dev \
    pkg-config

# Install Python dependencies
pip install -r requirements.txt
```

### Windows

On Windows, you may need to install pre-built wheels for `av` and `aiortc`.

```bash
pip install -r requirements.txt
```

## Usage

### Basic Usage

```bash
python agent.py --backend ws://localhost:8000/ws/device --device-id my-camera-001
```

### Command-line Arguments

- `--backend`: Backend WebSocket URL (default: `ws://localhost:8000/ws/device`)
- `--device-id`: Unique device identifier (default: auto-generated)

### Example

```bash
# Connect to local backend
python agent.py --device-id kitchen-camera

# Connect to remote backend
python agent.py --backend wss://myserver.com/ws/device --device-id bedroom-camera
```

## Device Registration

Before running the agent, register the device with the backend:

```bash
curl -X POST http://localhost:8000/api/devices/register \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "my-camera-001",
    "device_name": "Kitchen Camera",
    "device_type": "camera"
  }'
```

## How It Works

1. **Connection**: Agent connects to backend via WebSocket and sends `hello` message
2. **Registration**: Backend verifies device exists in database
3. **Heartbeat**: Agent sends periodic heartbeats to maintain connection
4. **Watch Request**: When a viewer wants to watch, backend sends `watch_request`
5. **WebRTC Setup**: Agent creates RTCPeerConnection and adds video track
6. **Signaling**: SDP offer/answer and ICE candidates exchanged via WebSocket
7. **Streaming**: Video flows peer-to-peer (or via TURN) to viewer
8. **Cleanup**: On disconnect or session end, peer connections are closed

## Message Flow

### Device Connection

```
Device -> Backend: {"type": "hello", "payload": {"device_id": "..."}}
Backend -> Device: {"type": "hello_ack", "payload": {...}}
```

### Watch Session

```
Viewer -> Backend: {"type": "watch_request", "payload": {"device_id": "..."}}
Backend -> Device: {"type": "watch_request", "payload": {"session_id": "...", "ice_servers": [...]}}
Device creates RTCPeerConnection and adds video track
Viewer -> Backend: {"type": "signal_offer", "payload": {"sdp": {...}}}
Backend -> Device: {"type": "signal_offer", "payload": {"sdp": {...}}}
Device -> Backend: {"type": "signal_answer", "payload": {"sdp": {...}}}
Backend -> Viewer: {"type": "signal_answer", "payload": {"sdp": {...}}}
ICE candidates exchanged...
Video streaming begins (peer-to-peer)
```

## Replacing Fake Video with Real Camera

Replace `FakeVideoTrack` with actual camera capture:

```python
from aiortc.contrib.media import MediaPlayer

# For webcam
player = MediaPlayer('/dev/video0', format='v4l2')
pc.addTrack(player.video)

# For RTSP camera
player = MediaPlayer('rtsp://camera-ip/stream')
pc.addTrack(player.video)
```

Or use OpenCV:

```python
import cv2
from aiortc import VideoStreamTrack
import av

class CameraTrack(VideoStreamTrack):
    def __init__(self, camera_index=0):
        super().__init__()
        self.cap = cv2.VideoCapture(camera_index)

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        ret, frame = self.cap.read()

        if not ret:
            # Handle error
            pass

        frame = av.VideoFrame.from_ndarray(frame, format='bgr24')
        frame.pts = pts
        frame.time_base = time_base
        return frame

    def __del__(self):
        self.cap.release()

# Use it
video_track = CameraTrack(camera_index=0)
pc.addTrack(video_track)
```

## Reconnection Strategy

The agent implements exponential backoff for reconnection:

- Initial delay: 1 second
- Max delay: 60 seconds
- Formula: `delay * (2 ** attempts)` capped at max delay

This ensures robust operation even with intermittent network issues.

## Troubleshooting

### Connection Refused

Ensure backend is running:

```bash
curl http://localhost:8000/health
```

### WebRTC Connection Failed

1. Check TURN server is accessible
2. Verify firewall allows UDP traffic on ports 49152-65535
3. Check ICE server configuration in logs

### Video Not Streaming

1. Verify peer connection state in logs
2. Check ICE connection state (should reach "connected")
3. Ensure viewer's WebRTC implementation is correct

### Import Errors (aiortc)

Make sure all system dependencies are installed:

```bash
# Ubuntu/Debian
sudo apt-get install -y libavformat-dev libavcodec-dev libavdevice-dev

# macOS
brew install ffmpeg opus libvpx
```

## Running with Docker

```bash
docker build -t mumu-device-agent .
docker run -it --rm \
  -e BACKEND_URL=ws://host.docker.internal:8000/ws/device \
  -e DEVICE_ID=docker-camera-001 \
  mumu-device-agent
```

## Production Considerations

1. **Auto-start**: Use systemd or supervisord to auto-start agent on boot
2. **Logging**: Configure proper log rotation
3. **Monitoring**: Monitor connection status and stream health
4. **Security**: Use WSS (WebSocket Secure) in production
5. **Camera Access**: Ensure proper permissions for camera device
6. **Resource Management**: Monitor CPU/memory usage for encoding

## License

MIT
