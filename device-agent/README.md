# MuMu Camera Device Agent / Simulator

Python-based camera agent using aiortc for WebRTC video streaming.

## 🎯 No Raspberry Pi? No Problem!

This agent can run in **simulator mode** with fake video, allowing you to test the complete system without any physical camera hardware.

## Features

- **WebRTC Video Streaming**: Uses aiortc to send video to viewers via peer-to-peer connections
- **Multiple Video Sources**:
  - `fake`: Animated moving box (no camera needed) ⭐ **Best for testing**
  - `webcam`: System webcam (requires opencv-python)
  - `camera`: Raspberry Pi camera (future support)
- **Robust Reconnection**: Exponential backoff reconnection strategy
- **WebSocket Signaling**: Connects to central server for signaling
- **TURN Support**: Uses dynamic TURN credentials from server for NAT traversal

## Prerequisites

### For Simulator Mode (fake video)
- Python 3.11+
- No camera required!
- No GPU required!

### For Webcam Mode
- Python 3.11+
- System webcam
- opencv-python package

### For Raspberry Pi
- Raspberry Pi with camera module
- Python 3.11+
- picamera2 (future)

## Installation

### Quick Start (Simulator Mode)

```bash
cd device-agent

# Install dependencies
pip install -r requirements.txt

# Run simulator with fake video
python agent.py

# Or with specific device ID
python agent.py --device-id kitchen-cam-001
```

That's it! No camera hardware needed.

### System Dependencies

**Linux/macOS**:
```bash
# For simulator mode (fake video)
sudo apt-get install -y \
    libavformat-dev \
    libavcodec-dev \
    libavdevice-dev \
    libavutil-dev \
    libswscale-dev \
    libopus-dev \
    libvpx-dev \
    pkg-config

# For webcam mode (additional)
sudo apt-get install -y libopencv-dev
```

**Windows**:
```bash
# Install Python packages (wheels available)
pip install -r requirements.txt

# For webcam mode (additional)
pip install opencv-python
```

## Usage

### 1. Simulator Mode (No Camera)

**Best for testing without hardware!**

```bash
# Basic usage (fake video with auto-generated device ID)
python agent.py

# With specific device ID
python agent.py --device-id living-room-cam

# With verbose logging
python agent.py --device-id test-cam-001 --verbose

# Connect to remote backend
python agent.py --backend wss://myserver.com/ws/device --device-id my-cam
```

**What you'll see**: Moving white box on colorful background with frame counter.

### 2. Webcam Mode

```bash
# Use system webcam
python agent.py --video-source webcam --device-id webcam-001

# Or with environment variable
export VIDEO_SOURCE=webcam
python agent.py --device-id webcam-001
```

### 3. Environment Variables

```bash
# Set environment variables
export BACKEND_URL=ws://localhost:8000/ws/device
export DEVICE_ID=my-unique-device-id
export VIDEO_SOURCE=fake

# Run agent
python agent.py
```

### 4. Using Docker Compose

**Start main services** (without device simulator):
```bash
docker-compose up -d
```

**Start with device simulator**:
```bash
# Single simulator
docker-compose --profile sim up -d device-sim

# Multiple simulators
docker-compose --profile sim up -d device-sim device-sim-2 device-sim-3

# Check logs
docker-compose logs -f device-sim
```

**Stop simulator**:
```bash
docker-compose --profile sim down
```

## Device Registration Flow

Before the device can be used, it must be **registered** and **paired** to a user:

### Step 1: Register Device

**Option A: Via Web UI** (after login):
- Go to Settings → Add Device
- Enter device ID
- Device will be registered

**Option B: Via API**:
```bash
curl -X POST http://localhost:8000/api/devices/register \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "my-camera-001",
    "device_name": "My Test Camera",
    "device_type": "camera"
  }'
```

### Step 2: Start Device Agent

```bash
# Start agent (it will connect to backend)
python agent.py --device-id my-camera-001
```

### Step 3: Pair Device to User

**Generate pairing code** (device-side):
```bash
# The agent will automatically request pairing code
# Or manually via API:
curl -X POST "http://localhost:8000/api/pairing/generate?device_id=my-camera-001"
```

**Enter code in web UI**:
- Login to web interface
- Click "Pair Device"
- Enter 6-digit code
- Device is now paired!

### Step 4: Watch Video

- Device will show as "Online" in web UI
- Click "Watch" button
- Video stream will appear

## How It Works

### Connection Flow

```
1. Device Agent starts
2. Agent → Backend: WebSocket connect /ws/device
3. Agent → Backend: {"type": "hello", "payload": {"device_id": "..."}}
4. Backend → Database: Verify device exists
5. Backend → Redis: Mark device online
6. Backend → Agent: {"type": "hello_ack"}
7. Agent starts heartbeat loop (every 15s)
```

### Watch Session Flow

```
1. Viewer clicks "Watch" in web UI
2. Viewer → Backend: {"type": "watch_request", "payload": {"device_id": "..."}}
3. Backend → Agent: {"type": "watch_request", "payload": {"session_id": "...", "ice_servers": [...]}}
4. Agent creates RTCPeerConnection
5. Agent adds video track (fake/webcam)
6. Viewer → Agent: SDP offer (via Backend)
7. Agent → Viewer: SDP answer (via Backend)
8. ICE candidates exchanged
9. WebRTC connection established (P2P or via TURN)
10. Video streaming begins! 🎥
```

## Simulator vs Real Camera

| Feature | Simulator (fake) | Webcam | Raspberry Pi |
|---------|-----------------|--------|--------------|
| Camera required | ❌ No | ✅ Yes | ✅ Yes |
| GPU required | ❌ No | ❌ No | ❌ No |
| Video content | Moving box animation | Real webcam feed | Real camera feed |
| CPU usage | Low | Medium | Low-Medium |
| Use case | Testing, development | Desktop testing | Production |
| Installation | Simple | Simple | Requires setup |

## Troubleshooting

### WebSocket Connection Fails

**Problem**: `Connection refused` or `Failed to connect`

**Solutions**:
1. Check backend is running:
   ```bash
   curl http://localhost:8000/health
   ```

2. Verify WebSocket URL:
   ```bash
   # Should be ws:// (not http://)
   python agent.py --backend ws://localhost:8000/ws/device
   ```

3. Check firewall:
   ```bash
   # Allow port 8000
   sudo ufw allow 8000/tcp
   ```

### Device Not Appearing in Web UI

**Problem**: Device connects but doesn't show in device list

**Solutions**:
1. **Register device first**:
   ```bash
   curl -X POST http://localhost:8000/api/devices/register \
     -H "Content-Type: application/json" \
     -d '{"device_id":"my-cam-001"}'
   ```

2. **Pair device to user**:
   - Generate pairing code
   - Enter code in web UI

3. **Check device is online**:
   ```bash
   # Check logs
   docker-compose logs backend | grep device-id
   ```

### No Video Showing in Browser

**Problem**: Watch session starts but no video appears

**Solutions**:
1. **Check ICE connection state** (browser console):
   ```javascript
   // Should show "connected"
   console.log(peerConnection.iceConnectionState);
   ```

2. **Verify video track** (agent logs):
   ```
   # Should see:
   FakeVideoTrack initialized (moving box animation)
   ✓ Created peer connection for session...
   ✓ ICE connected for session...
   ```

3. **Check TURN server**:
   ```bash
   # Test TURN connectivity
   telnet localhost 3478
   ```

4. **Firewall rules** (for TURN):
   ```bash
   # Allow TURN ports
   sudo ufw allow 3478/tcp
   sudo ufw allow 3478/udp
   sudo ufw allow 49152:65535/udp
   ```

### ICE Connection Failed

**Problem**: `ICE connection state: failed`

**Solutions**:
1. **Check TURN credentials** (backend logs):
   ```bash
   docker-compose logs backend | grep TURN
   ```

2. **Verify TURN server is running**:
   ```bash
   docker-compose ps coturn
   ```

3. **Test TURN connectivity**:
   ```bash
   # Install turnutils
   sudo apt-get install coturn

   # Test TURN
   turnutils_uclient -v localhost 3478
   ```

4. **Check network connectivity**:
   - Ensure device and viewer can reach TURN server
   - Check NAT/firewall settings

### Webcam Not Working

**Problem**: `Failed to open camera` or `opencv not installed`

**Solutions**:
1. **Install OpenCV**:
   ```bash
   pip install opencv-python
   ```

2. **Check webcam availability**:
   ```bash
   # Linux
   ls /dev/video*

   # Should show /dev/video0, /dev/video1, etc.
   ```

3. **Test webcam manually**:
   ```python
   import cv2
   cap = cv2.VideoCapture(0)
   ret, frame = cap.read()
   print(f"Camera opened: {ret}")
   cap.release()
   ```

4. **Use different camera index**:
   ```python
   # Modify agent.py
   return WebcamTrack(camera_index=1)  # Try 1, 2, etc.
   ```

## Development Tips

### Testing Reconnection

```bash
# Start agent
python agent.py --device-id test-001 --verbose

# Stop backend (in another terminal)
docker-compose stop backend

# Watch agent reconnect with exponential backoff
# Restart backend
docker-compose start backend

# Agent should reconnect automatically
```

### Testing Multiple Devices

```bash
# Terminal 1
python agent.py --device-id cam-001 --video-source fake

# Terminal 2
python agent.py --device-id cam-002 --video-source fake

# Terminal 3
python agent.py --device-id cam-003 --video-source webcam
```

### Custom Video Source

Replace `FakeVideoTrack` with your own:

```python
class CustomVideoTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        # Your initialization

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        # Your video generation logic
        img = your_generate_frame_function()

        frame = av.VideoFrame.from_ndarray(img, format='bgr24')
        frame.pts = pts
        frame.time_base = time_base
        return frame

# Modify agent.py _create_video_track():
def _create_video_track(self):
    if self.video_source == "custom":
        return CustomVideoTrack()
    # ...
```

## Production Considerations

### For Real Deployment

1. **Use device tokens** (not just device ID):
   - Implement device authentication
   - Store tokens securely

2. **Monitor connection health**:
   - Log reconnection events
   - Alert on repeated failures

3. **Resource management**:
   - Limit concurrent sessions
   - Monitor CPU/memory usage

4. **Security**:
   - Use WSS (not WS) in production
   - Validate all inputs
   - Rate limit connections

5. **Logging**:
   - Use structured logging
   - Log to file + stdout
   - Rotate logs regularly

### Raspberry Pi Setup (Future)

```bash
# Install system packages
sudo apt-get update
sudo apt-get install -y python3-pip python3-opencv

# Install picamera2
pip3 install picamera2

# Run agent on boot (systemd)
sudo systemctl enable mumucam-agent
sudo systemctl start mumucam-agent
```

## Command Reference

```bash
# Basic usage
python agent.py

# With options
python agent.py \
  --backend ws://localhost:8000/ws/device \
  --device-id my-camera-001 \
  --video-source fake \
  --verbose

# Environment variables
export BACKEND_URL=ws://backend:8000/ws/device
export DEVICE_ID=cam-001
export VIDEO_SOURCE=fake
python agent.py

# Docker Compose
docker-compose --profile sim up -d device-sim
docker-compose --profile sim logs -f device-sim
docker-compose --profile sim down
```

## FAQ

**Q: Can I run multiple simulators on one machine?**
A: Yes! Just use different device IDs:
```bash
python agent.py --device-id cam-001 &
python agent.py --device-id cam-002 &
python agent.py --device-id cam-003 &
```

**Q: Does simulator mode use GPU?**
A: No, everything runs on CPU. Very lightweight.

**Q: Can I replace fake video with RTSP stream?**
A: Yes! Use aiortc's `MediaPlayer`:
```python
from aiortc.contrib.media import MediaPlayer
player = MediaPlayer('rtsp://camera-ip/stream')
pc.addTrack(player.video)
```

**Q: How do I test without web UI?**
A: Use `wscat` to test WebSocket:
```bash
npm install -g wscat
wscat -c ws://localhost:8000/ws/device
> {"type":"hello","ts":"2025-01-01T00:00:00","payload":{"device_id":"test"}}
```

**Q: Can I run simulator in Docker?**
A: Yes! See "Using Docker Compose" section above.

**Q: How to switch from simulator to real camera?**
A: Just change `--video-source`:
```bash
# Simulator
python agent.py --video-source fake

# Webcam
python agent.py --video-source webcam

# Raspberry Pi (future)
python agent.py --video-source camera
```

## License

MIT

## Support

- Check logs: `python agent.py --verbose`
- Backend logs: `docker-compose logs backend`
- WebRTC issues: Check browser console
- Connection issues: Verify firewall/network settings
