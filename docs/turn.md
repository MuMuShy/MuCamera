# TURN Server Configuration and Usage

TURN (Traversal Using Relays around NAT) server setup for WebRTC NAT traversal.

## What is TURN?

TURN is a protocol that relays media when direct peer-to-peer connections fail.

### When is TURN Needed?

**WebRTC Connection Order**:
1. **Direct P2P** (preferred): Device ↔ Viewer directly
2. **STUN-assisted P2P**: Using public IPs discovered via STUN
3. **TURN relay** (fallback): Device ↔ TURN ↔ Viewer

**TURN is required when**:
- Symmetric NATs prevent direct connection
- Firewalls block P2P traffic
- Corporate networks restrict ports
- Mobile networks use carrier-grade NAT

**Statistics**: ~8-15% of WebRTC connections require TURN.

## Architecture

```
┌─────────────┐                                    ┌─────────────┐
│   Device    │                                    │   Viewer    │
│  (Behind    │                                    │  (Behind    │
│   NAT 1)    │                                    │   NAT 2)    │
└──────┬──────┘                                    └──────┬──────┘
       │                                                  │
       │ Direct P2P fails due to NATs                    │
       │                                                  │
       │               ┌──────────────┐                  │
       └──────────────►│ TURN Server  │◄─────────────────┘
        Allocate port  │  (Public IP) │   Allocate port
                       └──────┬───────┘
                              │
                       Relays media
                       (UDP or TCP)
```

## Coturn Server

We use Coturn, an open-source TURN/STUN server.

### Installation

**Docker (Recommended)**:
```bash
docker run -d \
  -p 3478:3478/udp \
  -p 3478:3478/tcp \
  -p 49152-65535:49152-65535/udp \
  -v $(pwd)/turnserver.conf:/etc/coturn/turnserver.conf \
  coturn/coturn -c /etc/coturn/turnserver.conf
```

**System Package**:
```bash
# Ubuntu/Debian
sudo apt-get install coturn

# Enable service
sudo systemctl enable coturn
sudo systemctl start coturn
```

### Configuration

File: `coturn/turnserver.conf`

**Key Settings**:

```ini
# Listening port
listening-port=3478

# External IP (CRITICAL for NAT traversal)
external-ip=YOUR.PUBLIC.IP.ADDRESS

# Relay port range
min-port=49152
max-port=65535

# Authentication
use-auth-secret
static-auth-secret=mumucam_turn_secret_key

# Realm
realm=mumucam
```

## Dynamic Credentials

Backend generates time-limited credentials using HMAC-SHA1.

### Algorithm

**Username Format**:
```
{timestamp}:{identifier}
```

Example:
```
1735740000:viewer_123_550e8400
```

**Credential Generation**:
```python
import hmac
import hashlib
import time

def generate_turn_credentials(username: str, secret: str) -> dict:
    # Timestamp: current time + TTL
    timestamp = int(time.time()) + 86400  # 24 hours

    # Time-limited username
    turn_username = f"{timestamp}:{username}"

    # HMAC-SHA1 credential
    turn_password = hmac.new(
        secret.encode('utf-8'),
        turn_username.encode('utf-8'),
        hashlib.sha1
    ).digest().hex()

    return {
        "urls": [
            f"turn:turn.example.com:3478?transport=udp",
            f"turn:turn.example.com:3478?transport=tcp",
        ],
        "username": turn_username,
        "credential": turn_password,
        "credentialType": "password"
    }
```

### Backend Implementation

File: `backend/app/turn_credentials.py`

```python
def generate_turn_credentials(username: str) -> Dict[str, any]:
    """Generate dynamic TURN credentials"""
    timestamp = int(time.time()) + settings.TURN_TTL
    turn_username = f"{timestamp}:{username}"

    turn_password = hmac.new(
        settings.TURN_SECRET.encode('utf-8'),
        turn_username.encode('utf-8'),
        hashlib.sha1
    ).digest().hex()

    return {
        "urls": [
            f"turn:{settings.TURN_HOST}:{settings.TURN_PORT}?transport=udp",
            f"turn:{settings.TURN_HOST}:{settings.TURN_PORT}?transport=tcp",
        ],
        "username": turn_username,
        "credential": turn_password,
        "credentialType": "password"
    }
```

### Client Usage

**Device Agent**:
```python
# Receive ICE servers from backend
ice_servers = message["payload"]["ice_servers"]

# Create peer connection
configuration = RTCConfiguration(iceServers=[
    RTCIceServer(
        urls=server["urls"],
        username=server.get("username"),
        credential=server.get("credential")
    )
    for server in ice_servers
])

pc = RTCPeerConnection(configuration=configuration)
```

**Web Client**:
```javascript
// Receive ICE servers from backend
const iceServers = message.payload.ice_servers;

// Create peer connection
const configuration = {
  iceServers: iceServers.map(server => ({
    urls: Array.isArray(server.urls) ? server.urls : [server.urls],
    username: server.username,
    credential: server.credential
  }))
};

const peerConnection = new RTCPeerConnection(configuration);
```

## ICE Server Configuration

Complete ICE servers list sent to clients:

```json
{
  "ice_servers": [
    {
      "urls": "stun:stun.l.google.com:19302"
    },
    {
      "urls": "stun:stun1.l.google.com:19302"
    },
    {
      "urls": [
        "turn:turn.example.com:3478?transport=udp",
        "turn:turn.example.com:3478?transport=tcp"
      ],
      "username": "1735740000:viewer_123_abc123",
      "credential": "a1b2c3d4e5f6789...",
      "credentialType": "password"
    }
  ]
}
```

**Priority Order**:
1. Try STUN first (lightweight, discover public IP)
2. Try direct P2P with discovered IPs
3. Fall back to TURN if P2P fails

## Deployment

### Single Server

For small deployments:

```yaml
# docker-compose.yml
coturn:
  image: coturn/coturn:latest
  ports:
    - "3478:3478/udp"
    - "3478:3478/tcp"
    - "49152-65535:49152-65535/udp"
  volumes:
    - ./coturn/turnserver.conf:/etc/coturn/turnserver.conf
```

### External IP Detection

**Static IP**:
```ini
external-ip=203.0.113.1
```

**Dynamic IP (AWS/GCP)**:
```bash
# Get instance public IP
EXTERNAL_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)

# Update config
sed -i "s/external-ip=.*/external-ip=$EXTERNAL_IP/" turnserver.conf
```

**DNS**:
```ini
external-ip=turn.yourdomain.com
```

### TURNS (TLS)

For encrypted TURN:

```ini
# Enable TLS
tls-listening-port=5349

# Certificates
cert=/etc/coturn/cert.pem
pkey=/etc/coturn/privkey.pem

# Ciphers
cipher-list="ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512"
```

**Get Certificates**:
```bash
# Let's Encrypt
certbot certonly --standalone -d turn.yourdomain.com

# Link certificates
ln -s /etc/letsencrypt/live/turn.yourdomain.com/fullchain.pem /etc/coturn/cert.pem
ln -s /etc/letsencrypt/live/turn.yourdomain.com/privkey.pem /etc/coturn/privkey.pem
```

**Update Backend**:
```python
return {
    "urls": [
        f"turn:{settings.TURN_HOST}:{settings.TURN_PORT}?transport=udp",
        f"turns:{settings.TURN_HOST}:5349?transport=tcp",  # TURNS
    ],
    # ...
}
```

### Multiple TURN Servers

**Geographic Distribution**:
```python
# Backend config
TURN_SERVERS = [
    {"host": "turn-us.example.com", "port": 3478, "region": "us"},
    {"host": "turn-eu.example.com", "port": 3478, "region": "eu"},
    {"host": "turn-asia.example.com", "port": 3478, "region": "asia"},
]

def get_ice_servers(username: str, region: str = "us") -> List[Dict]:
    ice_servers = [{"urls": "stun:stun.l.google.com:19302"}]

    # Add all TURN servers (client will try in order)
    for turn_server in TURN_SERVERS:
        ice_servers.append(
            generate_turn_credentials(username, turn_server)
        )

    return ice_servers
```

## Firewall Configuration

### Required Ports

**Inbound**:
- TCP/UDP 3478: TURN signaling
- TCP 5349: TURNS (TLS)
- UDP 49152-65535: Media relay

**Ubuntu UFW**:
```bash
sudo ufw allow 3478/tcp
sudo ufw allow 3478/udp
sudo ufw allow 5349/tcp
sudo ufw allow 49152:65535/udp
```

**iptables**:
```bash
iptables -A INPUT -p udp --dport 3478 -j ACCEPT
iptables -A INPUT -p tcp --dport 3478 -j ACCEPT
iptables -A INPUT -p tcp --dport 5349 -j ACCEPT
iptables -A INPUT -p udp --dport 49152:65535 -j ACCEPT
```

**AWS Security Group**:
- Type: Custom UDP, Port: 3478, Source: 0.0.0.0/0
- Type: Custom TCP, Port: 3478, Source: 0.0.0.0/0
- Type: Custom TCP, Port: 5349, Source: 0.0.0.0/0
- Type: Custom UDP, Port Range: 49152-65535, Source: 0.0.0.0/0

### Port Range Optimization

For high-traffic servers, restrict range:

```ini
min-port=50000
max-port=50100
```

100 ports supports ~50 concurrent sessions (2 ports per session).

## Monitoring

### Coturn Logs

**Docker**:
```bash
docker logs -f mumu-coturn
```

**System Service**:
```bash
journalctl -u coturn -f
```

### Metrics

**Active Allocations**:
```bash
# Connect to admin interface
telnet localhost 5766

# View sessions
ps
```

**Prometheus** (if compiled with support):
```
http://turn.example.com:9641/metrics
```

### Test Connectivity

**STUN Test**:
```bash
# Install turnutils
apt-get install coturn

# Test STUN
turnutils_stunclient turn.example.com
```

**TURN Test**:
```bash
# Test TURN with credentials
turnutils_uclient -v \
  -u "1735740000:test" \
  -w "generated_password" \
  turn.example.com
```

**Online Test**:
- Trickle ICE: https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/
- Enter TURN URL, username, credential
- Should see "relay" candidates

## Performance

### Resource Usage

**Per Session**:
- CPU: ~1-2%
- Memory: ~10-20 MB
- Bandwidth: 2x video bitrate (upstream + downstream)

**Example** (100 concurrent HD streams):
- CPU: ~100-200%
- Memory: ~2 GB
- Bandwidth: ~500 Mbps (assuming 2.5 Mbps per stream)

### Optimization

**Prefer UDP**:
```ini
no-tcp-relay
```

**Limit Sessions**:
```ini
max-allocate-count=100
total-quota=1000
user-quota=10
```

**Increase Workers**:
```ini
# Use multiple CPU cores (requires recompile)
proc-user=turnserver
proc-group=turnserver
```

## Security

### Credential Security

**Time-Limited**:
- Credentials expire after TTL (default 24h)
- Can't be reused after expiration
- Rotating secret not needed

**Strong Secret**:
```bash
# Generate secret
openssl rand -hex 32

# Update turnserver.conf and .env
static-auth-secret=YOUR_GENERATED_SECRET
TURN_SECRET=YOUR_GENERATED_SECRET
```

### Network Security

**Disable CLI**:
```ini
no-cli
```

Or set strong password:
```ini
cli-password=strong_random_password
```

**Restrict IPs** (if known):
```ini
allowed-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
```

### DDoS Protection

**Rate Limiting**:
```ini
max-bps=0  # No bandwidth limit (or set to reasonable value)
total-quota=1000  # Max 1000 MB total
user-quota=100  # Max 100 MB per user
```

**Firewall**:
```bash
# Limit connection rate
iptables -A INPUT -p udp --dport 3478 -m limit --limit 100/sec -j ACCEPT
```

## Troubleshooting

### No Relay Candidates

**Check**:
1. TURN server running: `docker ps | grep coturn`
2. Port open: `telnet turn.example.com 3478`
3. External IP set: Check `turnserver.conf`
4. Credentials valid: Test with `turnutils_uclient`

### Connection Uses TURN When P2P Should Work

**Possible Causes**:
- Firewall blocking UDP
- Symmetric NAT detected (TURN is correct choice)
- ICE gathering timeout too short

**Solution**:
- Check firewall rules
- Increase ICE gathering timeout
- This is often expected behavior

### High Bandwidth Usage

**Check**:
1. Number of active sessions: `turnutils` admin
2. Per-session bandwidth
3. Video bitrate settings

**Solutions**:
- Limit concurrent sessions
- Reduce video bitrate
- Upgrade server bandwidth

### TURN Server Not Responding

**Debug Steps**:
```bash
# Check service status
systemctl status coturn

# Check port binding
netstat -tulpn | grep 3478

# Test locally
turnutils_stunclient localhost

# Check firewall
iptables -L -n -v | grep 3478
```

## Best Practices

1. **Set external IP correctly**: Critical for NAT traversal
2. **Use dynamic credentials**: More secure than static
3. **Enable both UDP and TCP**: UDP preferred, TCP fallback
4. **Monitor bandwidth**: TURN can consume significant bandwidth
5. **Set resource limits**: Prevent abuse
6. **Use TLS in production**: TURNS for security
7. **Geographic distribution**: Multiple servers reduce latency
8. **Regular testing**: Verify connectivity monthly
9. **Log analysis**: Monitor for issues
10. **Backup TURN server**: High availability setup
