# MuMu Camera TURN Server (Coturn)

Coturn TURN/STUN server for NAT traversal in WebRTC connections.

## What is TURN?

TURN (Traversal Using Relays around NAT) is a protocol that helps WebRTC connections work when direct peer-to-peer connections fail due to:

- Symmetric NATs
- Restrictive firewalls
- Corporate proxies
- Other network obstacles

## How It Works in MuMu Camera

1. **Dynamic Credentials**: Backend generates time-limited TURN credentials using HMAC-SHA1
2. **Credential Sharing**: Credentials sent to both device and viewer via WebSocket
3. **ICE Negotiation**: WebRTC uses STUN first, falls back to TURN if needed
4. **Relay Traffic**: If direct connection fails, media flows through TURN server

## Configuration

The TURN server is configured via `turnserver.conf`.

### Key Settings

**Authentication**:
```
use-auth-secret
static-auth-secret=mumucam_turn_secret_key
```

Must match `TURN_SECRET` in backend `.env`.

**Ports**:
```
listening-port=3478
min-port=49152
max-port=65535
```

Port 3478 for TURN signaling, 49152-65535 for media relay.

**Realm**:
```
realm=mumucam
```

Must match backend's TURN credential generation.

## Usage

### Docker Compose (Recommended)

```bash
docker-compose up coturn
```

### Standalone Docker

```bash
docker run -d \
  -p 3478:3478/udp \
  -p 3478:3478/tcp \
  -p 49152-65535:49152-65535/udp \
  -v $(pwd)/turnserver.conf:/etc/coturn/turnserver.conf \
  coturn/coturn -c /etc/coturn/turnserver.conf
```

### Direct Installation

```bash
# Install coturn
sudo apt-get install coturn

# Copy config
sudo cp turnserver.conf /etc/turnserver.conf

# Start service
sudo systemctl start coturn
sudo systemctl enable coturn
```

## Port Requirements

Open these ports in your firewall:

- **TCP/UDP 3478**: TURN/STUN signaling
- **UDP 49152-65535**: Media relay (can restrict range if needed)

## Production Deployment

### 1. Set External IP

Edit `turnserver.conf`:

```
external-ip=YOUR.PUBLIC.IP.ADDRESS
```

Or use DNS:

```
external-ip=turn.yourdomain.com
```

### 2. Enable TLS (TURNS)

```
tls-listening-port=5349
cert=/etc/coturn/cert.pem
pkey=/etc/coturn/privkey.pem
```

Get certificates from Let's Encrypt:

```bash
certbot certonly --standalone -d turn.yourdomain.com
ln -s /etc/letsencrypt/live/turn.yourdomain.com/fullchain.pem /etc/coturn/cert.pem
ln -s /etc/letsencrypt/live/turn.yourdomain.com/privkey.pem /etc/coturn/privkey.pem
```

Update backend to include TURNS URLs:

```python
return {
    "urls": [
        f"turn:{settings.TURN_HOST}:{settings.TURN_PORT}?transport=udp",
        f"turn:{settings.TURN_HOST}:{settings.TURN_PORT}?transport=tcp",
        f"turns:{settings.TURN_HOST}:5349?transport=tcp",
    ],
    # ...
}
```

### 3. Change Secret

Update both `turnserver.conf` and backend `.env`:

```bash
# Generate strong secret
openssl rand -hex 32

# Update turnserver.conf
static-auth-secret=YOUR_GENERATED_SECRET

# Update .env
TURN_SECRET=YOUR_GENERATED_SECRET
```

### 4. Disable Verbose Logging

Remove or comment out:

```
# verbose
```

### 5. Set Resource Limits

```
user-quota=100
total-quota=1000
max-bps=0
```

## Testing TURN Server

### Test with turnutils

```bash
# Test STUN
turnutils_stunclient YOUR.PUBLIC.IP

# Test TURN
turnutils_uclient -v YOUR.PUBLIC.IP
```

### Test with WebRTC

Use Trickle ICE test page:
https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/

Enter your TURN server:
- URI: `turn:YOUR.PUBLIC.IP:3478`
- Username: (get from backend)
- Password: (get from backend)

### Test Credential Generation

```bash
# From backend container
docker-compose exec backend python -c "
from app.turn_credentials import generate_turn_credentials
import json
print(json.dumps(generate_turn_credentials('test'), indent=2))
"
```

## Monitoring

### Check Logs

```bash
# Docker
docker-compose logs coturn

# System service
sudo journalctl -u coturn -f
```

### Check Active Sessions

```bash
# Connect to TURN admin CLI (if enabled)
telnet localhost 5766
```

### Prometheus Metrics

If coturn compiled with Prometheus support, metrics available at port 9641.

## Troubleshooting

### TURN Connection Fails

1. **Check firewall**:
   ```bash
   # Allow TURN ports
   sudo ufw allow 3478/tcp
   sudo ufw allow 3478/udp
   sudo ufw allow 49152:65535/udp
   ```

2. **Verify external IP**:
   ```bash
   # Should return your public IP
   curl ifconfig.me
   ```

3. **Test connectivity**:
   ```bash
   nc -u YOUR.PUBLIC.IP 3478
   ```

### Invalid Credentials

1. **Check secret matches** between turnserver.conf and backend
2. **Verify timestamp** in credential (should be future timestamp)
3. **Check HMAC generation** in backend

### High CPU/Memory Usage

1. **Limit concurrent sessions**:
   ```
   max-allocate-count=100
   ```

2. **Reduce port range**:
   ```
   min-port=49152
   max-port=49252
   ```

3. **Enable TCP-only** if UDP causes issues:
   ```
   no-udp-relay
   ```

### Docker Container Won't Start

Check port conflicts:

```bash
# See what's using port 3478
sudo lsof -i :3478

# Kill conflicting process
sudo kill -9 PID
```

## Security Considerations

1. **Use strong secret**: Generate with `openssl rand -hex 32`
2. **Enable TLS**: Use TURNS (port 5349) in production
3. **Restrict IP ranges**: Use `allowed-peer-ip` if possible
4. **Rate limiting**: Set `user-quota` and `total-quota`
5. **Disable CLI**: Set `no-cli` or use strong `cli-password`
6. **Firewall rules**: Only allow necessary ports
7. **Regular updates**: Keep coturn updated

## Performance Tuning

### For High Traffic

```
# Increase worker threads
proc-user=turnserver
proc-group=turnserver

# Optimize buffer sizes
udp-recv-buf-size=1048576
udp-send-buf-size=1048576

# Enable mobility
mobility
```

### For Low Latency

```
# Prefer UDP
no-tcp-relay

# Disable unnecessary features
stale-nonce=0
```

## Bandwidth Considerations

TURN relay uses significant bandwidth. For 10 concurrent HD streams:

- Upstream: ~50-100 Mbps
- Downstream: ~50-100 Mbps

Plan server bandwidth accordingly.

## Distributed TURN Servers

For high availability, run multiple TURN servers:

1. **DNS Round Robin**: Use same DNS name for multiple IPs
2. **Backend Config**: Return multiple TURN URLs
3. **Redis Sync**: Share credentials across servers

Example backend config:

```python
turn_servers = [
    {"host": "turn1.yourdomain.com", "port": 3478},
    {"host": "turn2.yourdomain.com", "port": 3478},
]

ice_servers = []
for server in turn_servers:
    ice_servers.append(generate_turn_credentials(username, server))
```

## License

Coturn is licensed under BSD-3-Clause.
