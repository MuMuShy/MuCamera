# Security

Security considerations and best practices for the MuMu Camera system.

## Security Model

### Threat Model

**Protected Against**:
- Unauthorized device access
- Session hijacking
- Credential theft
- Man-in-the-middle attacks (with HTTPS/WSS)
- SQL injection
- XSS attacks
- CSRF (to be implemented)

**Not Protected Against** (requires additional measures):
- DDoS attacks (requires rate limiting, CDN)
- Physical device compromise
- Insider threats
- Zero-day vulnerabilities

## Authentication

### User Authentication

**JWT Tokens**:
- Algorithm: HS256 (HMAC-SHA256)
- Expiration: 24 hours (configurable)
- Payload: User ID, username
- Signature: Secret key from environment

**Password Storage**:
- Algorithm: bcrypt
- Cost factor: 12 rounds
- Never stored in plain text
- Never logged

**Example**:
```python
# Hash password
hashed = pwd_context.hash(password)  # bcrypt with 12 rounds

# Verify password
is_valid = pwd_context.verify(plain_password, hashed)

# Create token
token = jwt.encode({
    "user_id": user.id,
    "username": user.username,
    "exp": datetime.utcnow() + timedelta(hours=24)
}, settings.JWT_SECRET, algorithm="HS256")
```

### Device Authentication

**Current Implementation**:
- Device ID based (simple)
- Devices registered via API
- WebSocket auth by device ID in hello message

**Recommended Enhancement**:
```python
# Generate device token on registration
device_token = secrets.token_urlsafe(32)
token_hash = hashlib.sha256(device_token.encode()).hexdigest()

# Store hash in database
DeviceToken(device_id=device.id, token_hash=token_hash)

# Device includes token in hello message
{
  "type": "hello",
  "payload": {
    "device_id": "cam-001",
    "token": "device_token_here"
  }
}

# Server verifies
token_hash = hashlib.sha256(provided_token.encode()).hexdigest()
is_valid = await verify_device_token(device_id, token_hash)
```

### WebSocket Authentication

**Viewer**:
- JWT token in hello message payload
- Token validated before accepting connection
- Invalid token = connection closed (code 1008)

**Device**:
- Device ID in hello message
- Verified against database
- Future: Add device token

## Authorization

### Device Ownership

Before allowing watch:
```python
# Check if user owns device
ownership = await db.execute(
    select(DeviceOwnership).where(
        DeviceOwnership.user_id == user.id,
        DeviceOwnership.device_id == device.id
    )
)
if not ownership.scalar_one_or_none():
    raise HTTPException(403, "Unauthorized")
```

### Session Authorization

Each session tied to:
- Specific user (viewer)
- Specific device
- Session ID (prevents replay)

Session ID sent with each signaling message:
```python
# Server checks session belongs to user/device
session = await get_session(session_id)
if session.user_id != user.id:
    raise Unauthorized
```

## Network Security

### HTTPS/WSS

**Production Requirements**:
- HTTPS for API (port 443)
- WSS for WebSocket (secure WebSocket)
- Valid TLS certificate (Let's Encrypt)

**Nginx Configuration**:
```nginx
server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    # Strong SSL settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256...';
    ssl_prefer_server_ciphers on;

    # HSTS
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # ... rest of config
}
```

### CORS

Configure allowed origins:
```bash
# .env
BACKEND_CORS_ORIGINS=https://app.yourdomain.com,https://admin.yourdomain.com
```

Backend enforces:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
```

### Firewall

**Backend Server**:
- Allow: 443/tcp (HTTPS), 8000/tcp (if direct access needed)
- Deny: 5432/tcp (PostgreSQL), 6379/tcp (Redis) from public

**TURN Server**:
- Allow: 3478/udp, 3478/tcp, 5349/tcp, 49152-65535/udp
- Deny: All other ports

**Database/Redis**:
- Only allow from backend server IP
- Use security groups (AWS) or firewall rules

## Data Security

### Sensitive Data

**Never Log**:
- Passwords (plain text)
- JWT secrets
- TURN secrets
- Full JWT tokens (log only first/last 4 chars)

**Example**:
```python
# Bad
logger.info(f"User login: {username}, password: {password}")

# Good
logger.info(f"User login attempt: {username}")
```

### Database Security

**SQL Injection Prevention**:
- Use parameterized queries (SQLAlchemy ORM)
- Never construct SQL from user input

```python
# Bad (vulnerable)
query = f"SELECT * FROM users WHERE username = '{username}'"

# Good (safe)
stmt = select(User).where(User.username == username)
```

**Database Access**:
- Dedicated database user (not superuser)
- Minimal permissions (SELECT, INSERT, UPDATE, DELETE on specific tables)
- No DDL permissions (CREATE, DROP, ALTER) for application

```sql
-- Create limited user
CREATE USER mumucam WITH PASSWORD 'strong_password';
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO mumucam;
```

### Redis Security

**Authentication**:
```bash
# redis.conf
requirepass strong_redis_password
```

**Network**:
```bash
# Bind to localhost only (if backend on same server)
bind 127.0.0.1

# Or use firewall for remote access
```

**Disable Dangerous Commands**:
```bash
rename-command FLUSHDB ""
rename-command FLUSHALL ""
rename-command CONFIG "SECRET_CONFIG_COMMAND"
```

## Input Validation

### API Input

FastAPI with Pydantic provides automatic validation:

```python
class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, regex="^[a-zA-Z0-9_]+$")
    email: EmailStr  # Validates email format
    password: str = Field(..., min_length=8, max_length=100)

@app.post("/api/auth/register")
async def register(user_data: UserCreate):
    # user_data already validated
    pass
```

### WebSocket Messages

Validate all incoming messages:

```python
def validate_message(message: dict) -> bool:
    # Required fields
    if "type" not in message or "ts" not in message or "payload" not in message:
        return False

    # Type whitelist
    allowed_types = ["hello", "heartbeat", "watch_request", "signal_offer", ...]
    if message["type"] not in allowed_types:
        return False

    # Timestamp format
    try:
        datetime.fromisoformat(message["ts"])
    except ValueError:
        return False

    return True
```

### Sanitization

For any data displayed in web UI:

```javascript
// Escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Use when displaying user-provided data
deviceCard.innerHTML = `<h3>${escapeHtml(device.device_name)}</h3>`;
```

## Session Security

### Session IDs

- Use UUIDs (version 4)
- Cryptographically random
- Never sequential or predictable

```python
import uuid
session_id = str(uuid.uuid4())  # e.g., "550e8400-e29b-41d4-a716-446655440000"
```

### Session Expiration

- Active sessions tracked in database
- Ended sessions marked with timestamp
- Old sessions cleaned up periodically

```python
# Cleanup old sessions (older than 90 days)
await db.execute(
    delete(WatchSession).where(
        WatchSession.ended_at < datetime.utcnow() - timedelta(days=90)
    )
)
```

### Session Hijacking Prevention

- Session ID sent only over WSS (encrypted)
- Validate session belongs to user on every message
- End session on disconnect

## Secrets Management

### Environment Variables

Store secrets in `.env` (never commit to Git):

```bash
# .env
JWT_SECRET=your_jwt_secret_here_min_32_chars
TURN_SECRET=your_turn_secret_here_min_32_chars
POSTGRES_PASSWORD=your_db_password
```

Add to `.gitignore`:
```
.env
*.key
*.pem
```

### Generate Strong Secrets

```bash
# JWT secret (32 bytes)
openssl rand -hex 32

# TURN secret (32 bytes)
openssl rand -hex 32

# Database password (16 chars alphanumeric)
openssl rand -base64 16
```

### Production Secrets

**AWS Secrets Manager**:
```python
import boto3

client = boto3.client('secretsmanager')
response = client.get_secret_value(SecretId='mumucam/jwt-secret')
JWT_SECRET = response['SecretString']
```

**Kubernetes Secrets**:
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: mumucam-secrets
type: Opaque
data:
  jwt-secret: <base64-encoded-secret>
```

## Rate Limiting

### API Rate Limiting

**Nginx**:
```nginx
http {
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

    server {
        location /api/auth/ {
            limit_req zone=api burst=20 nodelay;
            # ...
        }
    }
}
```

**Application Level**:
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/api/auth/login")
@limiter.limit("5/minute")
async def login(request: Request, credentials: UserLogin):
    # ...
```

### WebSocket Rate Limiting

Prevent message flooding:

```python
class RateLimiter:
    def __init__(self, max_messages: int, window: int):
        self.max_messages = max_messages
        self.window = window
        self.messages = {}

    async def check(self, identifier: str) -> bool:
        now = time.time()
        if identifier not in self.messages:
            self.messages[identifier] = []

        # Remove old messages
        self.messages[identifier] = [
            t for t in self.messages[identifier]
            if now - t < self.window
        ]

        if len(self.messages[identifier]) >= self.max_messages:
            return False

        self.messages[identifier].append(now)
        return True

# Usage
rate_limiter = RateLimiter(max_messages=100, window=60)

if not await rate_limiter.check(device_id):
    await websocket.close(code=1008, reason="Rate limit exceeded")
```

## Security Headers

### HTTP Headers

```nginx
# Nginx
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "no-referrer-when-downgrade" always;
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'" always;
```

### CORS Headers

Only allow specific origins:

```python
BACKEND_CORS_ORIGINS = "https://app.yourdomain.com"  # Not "*"
```

## Monitoring and Logging

### Security Logging

Log security events:

```python
logger.warning(f"Failed login attempt: {username} from {ip_address}")
logger.error(f"Unauthorized access attempt: user {user_id} to device {device_id}")
logger.info(f"Device connected: {device_id} from {ip_address}")
```

### Intrusion Detection

Monitor for suspicious activity:
- Multiple failed login attempts
- Rapid session creation
- Unusual access patterns

```python
# Track failed logins
failed_logins = {}

async def login(credentials: UserLogin, request: Request):
    client_ip = request.client.host

    user = await authenticate_user(db, credentials.username, credentials.password)
    if not user:
        # Track failed attempt
        failed_logins[client_ip] = failed_logins.get(client_ip, 0) + 1

        if failed_logins[client_ip] > 5:
            logger.warning(f"Multiple failed logins from {client_ip}")
            # Consider IP ban or CAPTCHA

        raise HTTPException(401, "Invalid credentials")

    # Reset on successful login
    failed_logins.pop(client_ip, None)
    # ...
```

## Compliance

### GDPR (if applicable)

- **Data minimization**: Only collect necessary data
- **Right to deletion**: Implement user/device deletion
- **Data export**: Allow users to export their data
- **Privacy policy**: Document data usage

### HIPAA/SOC2 (if applicable)

- **Encryption at rest**: Encrypt database backups
- **Encryption in transit**: HTTPS/WSS everywhere
- **Audit logs**: Log all access to sensitive data
- **Access controls**: Role-based permissions

## Security Checklist

### Development

- [ ] All passwords hashed with bcrypt
- [ ] No secrets in code or Git
- [ ] SQL injection prevention (parameterized queries)
- [ ] XSS prevention (HTML escaping)
- [ ] Input validation on all endpoints
- [ ] CSRF protection (if using cookies)
- [ ] Secure session management

### Deployment

- [ ] HTTPS/WSS enabled (valid certificate)
- [ ] Strong secrets generated
- [ ] Firewall configured
- [ ] Database/Redis not publicly accessible
- [ ] Rate limiting enabled
- [ ] Security headers configured
- [ ] CORS properly configured
- [ ] Regular security updates

### Monitoring

- [ ] Security event logging
- [ ] Failed login monitoring
- [ ] Unusual activity alerts
- [ ] Regular log review
- [ ] Intrusion detection (optional)

## Vulnerability Response

### Reporting

Create `SECURITY.md`:

```markdown
# Security Policy

## Reporting a Vulnerability

Email: security@yourdomain.com

Please include:
- Description of vulnerability
- Steps to reproduce
- Potential impact

We will respond within 48 hours.
```

### Updates

- Subscribe to security advisories for dependencies
- Regular `pip install --upgrade` (test first)
- Monitor CVEs for PostgreSQL, Redis, Nginx

### Incident Response

1. **Identify**: Detect and confirm incident
2. **Contain**: Isolate affected systems
3. **Eradicate**: Remove threat
4. **Recover**: Restore services
5. **Learn**: Post-mortem and improvements

## Best Practices Summary

1. **Always use HTTPS/WSS in production**
2. **Never commit secrets to Git**
3. **Hash all passwords with bcrypt**
4. **Validate all input (API and WebSocket)**
5. **Use parameterized queries (prevent SQL injection)**
6. **Implement rate limiting**
7. **Configure CORS properly (not "*")**
8. **Set security headers**
9. **Log security events**
10. **Keep dependencies updated**
11. **Regular security audits**
12. **Follow principle of least privilege**
