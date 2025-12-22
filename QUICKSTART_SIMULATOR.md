# Quick Start Guide: Device Simulator

Test the complete MuMu Camera system **without any physical camera hardware** in 5 minutes!

## Prerequisites

- Docker & Docker Compose
- Python 3.11+ (for local simulator)
- Web browser (Chrome/Firefox/Edge)

## Option 1: Docker Compose (Easiest)

### Step 1: Start All Services

```bash
cd mumu-cam

# Copy environment variables
cp .env.example .env

# Start core services
docker-compose up -d

# Wait for services to be ready (~30 seconds)
docker-compose logs -f backend
# Wait until you see: "Application startup complete"
```

### Step 2: Initialize Database

```bash
docker-compose exec backend alembic upgrade head
```

### Step 3: Create User Account

Open browser: http://localhost:8080

1. Click "Register"
2. Fill in:
   - Username: `testuser`
   - Email: `test@example.com`
   - Password: `password123`
3. Click "Register"

You're now logged in!

### Step 4: Register Device

```bash
# Register a simulated device
curl -X POST http://localhost:8000/api/devices/register \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "simulator-001",
    "device_name": "Test Simulator",
    "device_type": "camera"
  }'
```

### Step 5: Start Device Simulator

```bash
# Start simulator with Docker Compose
docker-compose --profile sim up -d device-sim

# Check logs
docker-compose logs -f device-sim
```

You should see:
```
✓ Connected as device: simulator-001
✓ Server acknowledged connection
♥ Heartbeat sent
```

### Step 6: Pair Device

Back in the browser (http://localhost:8080):

1. Click "Pair Device"
2. Generate pairing code:
   ```bash
   curl -X POST "http://localhost:8000/api/pairing/generate?device_id=simulator-001"
   ```
3. Copy the 6-digit code from the response
4. Enter code in web UI
5. Click "Pair"

Device should now appear as "Online"!

### Step 7: Watch Video

1. Find "Test Simulator" in your device list
2. Click "Watch" button
3. Wait 2-3 seconds for WebRTC connection
4. **You should see moving white box on colorful background!** 🎥

### Step 8: Test Multiple Simulators

```bash
# Start 3 simulators
docker-compose --profile sim up -d device-sim device-sim-2 device-sim-3

# Each needs to be registered and paired
curl -X POST http://localhost:8000/api/devices/register \
  -H "Content-Type: application/json" \
  -d '{"device_id":"simulator-002","device_name":"Simulator 2"}'

curl -X POST http://localhost:8000/api/devices/register \
  -H "Content-Type: application/json" \
  -d '{"device_id":"simulator-003","device_name":"Simulator 3"}'

# Generate pairing codes and pair each device in web UI
```

---

## Option 2: Local Python Simulator

### Step 1-3: Same as Option 1

Follow steps 1-3 from Option 1 to start services and create user.

### Step 4: Register Device

```bash
curl -X POST http://localhost:8000/api/devices/register \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "local-sim-001",
    "device_name": "Local Simulator",
    "device_type": "camera"
  }'
```

### Step 5: Install Python Dependencies

```bash
cd device-agent
pip install -r requirements.txt
```

### Step 6: Run Simulator

```bash
# Run with specific device ID
python agent.py --device-id local-sim-001 --verbose

# Or use environment variables
export DEVICE_ID=local-sim-001
export VIDEO_SOURCE=fake
python agent.py
```

You should see:
```
=== MuMu Camera Device Agent ===
Device ID: local-sim-001
Video Source: fake
Backend: ws://localhost:8000/ws/device
================================
Connecting to ws://localhost:8000/ws/device...
✓ Connected as device: local-sim-001
FakeVideoTrack initialized (moving box animation)
```

### Step 7: Pair and Watch

Same as Option 1, steps 6-7.

---

## Troubleshooting

### Device Not Connecting

**Check backend is running:**
```bash
curl http://localhost:8000/health
# Should return: {"status":"healthy",...}
```

**Check logs:**
```bash
docker-compose logs backend
docker-compose logs device-sim
```

### Device Online but No Video

**Check browser console** (F12):
- Look for WebRTC errors
- Check ICE connection state

**Check TURN server:**
```bash
docker-compose ps coturn
# Should show "Up"
```

**Restart simulator:**
```bash
docker-compose --profile sim restart device-sim
docker-compose logs -f device-sim
```

### "Device not found" Error

**Ensure device is registered:**
```bash
curl -X POST http://localhost:8000/api/devices/register \
  -H "Content-Type: application/json" \
  -d '{"device_id":"simulator-001"}'
```

### Pairing Code Invalid

**Generate new code:**
```bash
curl -X POST "http://localhost:8000/api/pairing/generate?device_id=simulator-001"
```

Codes expire after 5 minutes!

---

## Expected Behavior

### Device Logs (Success)

```
2025-01-01 12:00:00 - INFO - === MuMu Camera Device Agent ===
2025-01-01 12:00:00 - INFO - Device ID: simulator-001
2025-01-01 12:00:00 - INFO - Video Source: fake
2025-01-01 12:00:00 - INFO - Connecting to ws://backend:8000/ws/device...
2025-01-01 12:00:01 - INFO - ✓ Connected as device: simulator-001
2025-01-01 12:00:01 - INFO - ✓ Server acknowledged connection
2025-01-01 12:00:01 - INFO - FakeVideoTrack initialized (moving box animation)
2025-01-01 12:00:15 - DEBUG - ♥ Heartbeat sent
2025-01-01 12:00:30 - INFO - ▶ Watch request from user 1, session abc123
2025-01-01 12:00:30 - INFO - ✓ Created peer connection for session abc123
2025-01-01 12:00:31 - INFO - ⇄ Received SDP offer for session abc123
2025-01-01 12:00:31 - INFO - ✓ Set remote description for session abc123
2025-01-01 12:00:31 - INFO - ✓ Created answer for session abc123
2025-01-01 12:00:31 - INFO - ✓ Sent SDP answer for session abc123
2025-01-01 12:00:32 - INFO - ICE state: checking
2025-01-01 12:00:33 - INFO - ICE state: connected
2025-01-01 12:00:33 - INFO - ✓ ICE connected for session abc123
2025-01-01 12:00:33 - INFO - Connection state: connected
2025-01-01 12:00:33 - INFO - ✓ Peer connection established for session abc123
```

### Browser Console (Success)

```
Received message: watch_ready
Creating peer connection with ICE servers: ...
Creating offer
Set local description
Sent offer
Received answer
Set remote description
ICE connection state: checking
ICE connection state: connected
Connection state: connected
```

### What You'll See in Browser

- **Before connection**: "Connecting..." overlay on black video
- **During connection**: ICE state changing in session info
- **After connection**:
  - Moving white box bouncing on colorful vertical bars
  - Green progress bar in top-left corner
  - Connection state: "connected"
  - ICE state: "connected"

---

## Next Steps

✅ **Simulator working?** Great!

Try these next:

1. **Test reconnection**: Stop/start device-sim, watch auto-reconnect
2. **Multiple devices**: Start 3 simulators, watch them all
3. **Webcam mode**: `python agent.py --video-source webcam`
4. **Check documentation**: See `device-agent/README.md` for details
5. **Read architecture**: See `docs/architecture.md` for how it works

---

## Clean Up

```bash
# Stop simulators
docker-compose --profile sim down

# Stop all services
docker-compose down

# Remove volumes (deletes database)
docker-compose down -v
```

---

## Success Checklist

- [ ] Services started: `docker-compose up -d`
- [ ] Database migrated: `alembic upgrade head`
- [ ] User registered in web UI
- [ ] Device registered via API
- [ ] Simulator started and connected
- [ ] Device paired with pairing code
- [ ] Device shows "Online" in web UI
- [ ] Watch button clicked
- [ ] Video playing (moving box visible)
- [ ] Connection stable (no errors in logs)

**All checked?** Congratulations! 🎉

Your MuMu Camera system is working perfectly in simulator mode!

---

## Quick Reference

```bash
# Start everything
docker-compose up -d
docker-compose exec backend alembic upgrade head

# Register device
curl -X POST http://localhost:8000/api/devices/register \
  -H "Content-Type: application/json" \
  -d '{"device_id":"sim-001","device_name":"Simulator 1"}'

# Start simulator (Docker)
docker-compose --profile sim up -d device-sim

# Start simulator (Python)
cd device-agent && python agent.py --device-id sim-001

# Generate pairing code
curl -X POST "http://localhost:8000/api/pairing/generate?device_id=sim-001"

# View logs
docker-compose logs -f device-sim
docker-compose logs -f backend

# Stop everything
docker-compose --profile sim down
docker-compose down
```

---

## Support

**Problem?** Check:
1. `docker-compose logs backend` - Backend errors
2. `docker-compose logs device-sim` - Simulator errors
3. Browser console (F12) - WebRTC errors
4. `device-agent/README.md` - Detailed troubleshooting

**Still stuck?** Open an issue with:
- Error messages from logs
- Browser console output
- Steps to reproduce
