# MuMu Camera Device Agent - go2rtc Proxy Mode

Lightweight device agent that proxies HTTP requests to local go2rtc instance.

## Prerequisites

- go2rtc running on local machine (default: http://127.0.0.1:1984)
- Python 3.11+

## Quick Start

### Run Locally

```bash
# Basic usage
DEVICE_ID=pi-cam-001 python agent.py

# With custom backend
BACKEND_URL=ws://myserver.com:8000/ws/device DEVICE_ID=pi-cam-001 python agent.py

# With device secret for authentication
DEVICE_ID=pi-cam-001 DEVICE_SECRET=mysecret123 python agent.py

# With custom go2rtc URL
DEVICE_ID=pi-cam-001 GO2RTC_HTTP=http://localhost:1984 python agent.py

# Verbose logging
DEVICE_ID=pi-cam-001 python agent.py --verbose
```

### Run with Docker

```bash
# Build image
docker build -t mumu-device-agent .

# Run with environment variables
docker run -d \
  --name mumu-agent \
  --network host \
  -e DEVICE_ID=pi-cam-001 \
  -e BACKEND_URL=ws://backend.example.com/ws/device \
  -e GO2RTC_HTTP=http://127.0.0.1:1984 \
  mumu-device-agent

# View logs
docker logs -f mumu-agent
```

### Run with Docker Compose

```yaml
version: '3.8'
services:
  agent:
    build: .
    network_mode: host
    environment:
      - DEVICE_ID=pi-cam-001
      - BACKEND_URL=ws://backend.example.com/ws/device
      - GO2RTC_HTTP=http://127.0.0.1:1984
      - DEVICE_SECRET=optional_secret
    restart: unless-stopped
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DEVICE_ID` | Yes | - | Unique device identifier |
| `BACKEND_URL` | No | `ws://localhost:8000/ws/device` | Backend WebSocket URL |
| `DEVICE_SECRET` | No | - | Optional device authentication secret |
| `GO2RTC_HTTP` | No | `http://127.0.0.1:1984` | go2rtc HTTP API URL |

## Features

- **Device Registration**: Automatically registers with backend on startup
- **WebSocket Connection**: Maintains persistent connection with backend
- **Capabilities Reporting**: Reports go2rtc stream info every 30 seconds
- **HTTP Proxy**: Proxies HTTP requests from backend to go2rtc
- **Reconnection**: Automatic reconnection with exponential backoff (1s → 30s max)
- **Resource Cleanup**: Properly handles disconnections and cancels background tasks

## Architecture

```
Backend Server          Device Agent              go2rtc
     |                       |                       |
     |-- proxy_http -------->|                       |
     |   (rid, method,       |-- HTTP Request ------>|
     |    path, headers)     |                       |
     |                       |<-- HTTP Response -----|
     |<-- proxy_http_resp ---|                       |
     |   (rid, status,       |                       |
     |    headers, body)     |                       |
```

## Logs

All logs are tagged with prefixes:
- `[ws]` - WebSocket connection events
- `[go2rtc]` - go2rtc API interactions
- `[proxy]` - HTTP proxy requests

Example:
```
2024-01-01 12:00:00 - __main__ - INFO - [ws] ✓ Connected as device: pi-cam-001
2024-01-01 12:00:15 - __main__ - DEBUG - [ws] ♥ Heartbeat sent
2024-01-01 12:00:30 - __main__ - DEBUG - [go2rtc] ✓ Reported capabilities (2 streams)
2024-01-01 12:01:00 - __main__ - INFO - [proxy] GET /api/streams (rid=abc123)
2024-01-01 12:01:00 - __main__ - INFO - [proxy] GET /api/streams → 200 (1234 bytes)
```

## Troubleshooting

### Agent won't start
- Ensure `DEVICE_ID` is set
- Check go2rtc is running: `curl http://127.0.0.1:1984/api/streams`

### Can't connect to backend
- Verify `BACKEND_URL` is correct
- Check network connectivity
- Check backend logs for errors

### Proxy requests failing
- Ensure go2rtc is accessible at `GO2RTC_HTTP`
- Check go2rtc logs: `docker logs -f go2rtc` (if using Docker)

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run with verbose logging
python agent.py --verbose --device-id test-001

# Test proxy functionality
# (send proxy_http message via backend WebSocket)
```
