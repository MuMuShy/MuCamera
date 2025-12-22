# Redis Keys and Data Structures

Redis usage in the MuMu Camera system for caching and presence tracking.

## Overview

Redis stores transient, high-frequency data:
- Device online/offline status
- Active session metadata
- Temporary cached data

Database (PostgreSQL) stores persistent data.

## Configuration

```bash
# .env
REDIS_URL=redis://localhost:6379/0
REDIS_ENABLED=true
```

If Redis unavailable, system falls back to in-memory store (non-persistent).

## Key Patterns

### Namespace Convention

```
{entity_type}:{identifier}:{field}
```

Examples:
- `devices:online` - Hash of online devices
- `session:550e8400` - Session data
- `device:presence:cam-001` - Device presence data

## Data Structures

### Device Presence

**Type**: Hash

**Key**: `devices:online`

**Structure**:
```
HSET devices:online "device-001" '{"connected_at": "2025-01-01T12:00:00", "last_heartbeat": "2025-01-01T12:05:00"}'
HSET devices:online "device-002" '{"connected_at": "2025-01-01T12:01:00", "last_heartbeat": "2025-01-01T12:05:30"}'
```

**Operations**:

```python
# Device connects
await redis_client.hset("devices:online", device_id, {
    "connected_at": datetime.utcnow().isoformat(),
    "last_heartbeat": datetime.utcnow().isoformat()
})

# Device heartbeat
await redis_client.hset("devices:online", device_id, {
    "last_heartbeat": datetime.utcnow().isoformat()
})

# Device disconnects
await redis_client.hdel("devices:online", device_id)

# Get all online devices
online_devices = await redis_client.hgetall("devices:online")

# Check if device online
is_online = await redis_client.hexists("devices:online", device_id)
```

**TTL**: No expiration (explicitly deleted on disconnect)

---

### Device Presence Details

**Type**: Hash

**Key**: `device:presence:{device_id}`

**Structure**:
```
HSET device:presence:cam-001 status '{"battery": 85, "temperature": 45, "recording": false}'
```

**Operations**:

```python
# Update presence
await redis_client.hset(f"device:presence:{device_id}", "status", {
    "battery": 85,
    "temperature": 45,
    "recording": False
})

# Get presence
presence = await redis_client.hget(f"device:presence:{device_id}", "status")
```

**TTL**: Optional, set to device heartbeat timeout (90 seconds)

---

### Active Watch Sessions

**Type**: Hash

**Key**: `session:{session_id}`

**Structure**:
```
HSET session:550e8400 data '{
    "user_id": "123",
    "device_id": "cam-001",
    "started_at": "2025-01-01T12:00:00",
    "status": "active"
}'
```

**Operations**:

```python
# Create session
await redis_client.hset(f"session:{session_id}", "data", {
    "user_id": user_id,
    "device_id": device_id,
    "started_at": datetime.utcnow().isoformat(),
    "status": "active"
})

# Get session
session_data = await redis_client.hget(f"session:{session_id}", "data")

# Delete session
await redis_client.delete(f"session:{session_id}")
```

**TTL**: Optional, max session duration (e.g., 24 hours)

---

### Session Device Mapping

**Type**: Set

**Key**: `device:sessions:{device_id}`

**Structure**:
```
SADD device:sessions:cam-001 "session-001"
SADD device:sessions:cam-001 "session-002"
```

**Operations**:

```python
# Add session to device
await redis_client.sadd(f"device:sessions:{device_id}", session_id)

# Get all sessions for device
sessions = await redis_client.smembers(f"device:sessions:{device_id}")

# Remove session
await redis_client.srem(f"device:sessions:{device_id}", session_id)

# Count active sessions
count = await redis_client.scard(f"device:sessions:{device_id}")
```

**Usage**: Quickly find all active sessions for a device when it disconnects.

---

### User Active Sessions

**Type**: Set

**Key**: `user:sessions:{user_id}`

**Structure**:
```
SADD user:sessions:123 "session-001"
```

**Operations**: Same as device sessions

**Usage**: Limit concurrent sessions per user, session cleanup on disconnect.

---

## Operations

### On Device Connect

```python
device_id = "cam-001"

# Mark online
await redis_client.hset("devices:online", device_id, {
    "connected_at": datetime.utcnow().isoformat(),
    "last_heartbeat": datetime.utcnow().isoformat()
})

# Update in database
await db.execute(
    update(Device)
    .where(Device.device_id == device_id)
    .values(is_online=True, last_seen=datetime.utcnow())
)
```

### On Device Disconnect

```python
# Remove from online set
await redis_client.hdel("devices:online", device_id)

# Get active sessions
sessions = await redis_client.smembers(f"device:sessions:{device_id}")

# End each session
for session_id in sessions:
    await redis_client.delete(f"session:{session_id}")
    # Notify viewer, update database...

# Clean up session set
await redis_client.delete(f"device:sessions:{device_id}")

# Update database
await db.execute(
    update(Device)
    .where(Device.device_id == device_id)
    .values(is_online=False, last_seen=datetime.utcnow())
)
```

### On Watch Request

```python
session_id = str(uuid.uuid4())

# Create session
await redis_client.hset(f"session:{session_id}", "data", {
    "user_id": user_id,
    "device_id": device_id,
    "started_at": datetime.utcnow().isoformat()
})

# Add to device sessions
await redis_client.sadd(f"device:sessions:{device_id}", session_id)

# Add to user sessions
await redis_client.sadd(f"user:sessions:{user_id}", session_id)

# Create in database
session = WatchSession(
    session_id=session_id,
    user_id=user_id,
    device_id=device.id,
    status="pending"
)
db.add(session)
await db.commit()
```

### On Watch End

```python
# Delete session
await redis_client.delete(f"session:{session_id}")

# Remove from device sessions
await redis_client.srem(f"device:sessions:{device_id}", session_id)

# Remove from user sessions
await redis_client.srem(f"user:sessions:{user_id}", session_id)

# Update database
await db.execute(
    update(WatchSession)
    .where(WatchSession.session_id == session_id)
    .values(status="ended", ended_at=datetime.utcnow())
)
```

## Expiration Policies

### Automatic Expiration

```python
# Session with TTL (24 hours)
await redis_client.hset(f"session:{session_id}", "data", session_data)
await redis_client.expire(f"session:{session_id}", 86400)

# Device presence (90 seconds, refreshed by heartbeat)
await redis_client.hset(f"device:presence:{device_id}", "status", presence_data)
await redis_client.expire(f"device:presence:{device_id}", 90)
```

### Manual Cleanup

Run periodic cleanup task:

```python
async def cleanup_stale_sessions():
    """Remove sessions older than 24 hours"""
    # Get all session keys
    pattern = "session:*"
    keys = await redis_client.keys(pattern)

    for key in keys:
        data = await redis_client.hget(key, "data")
        if data:
            started_at = datetime.fromisoformat(data["started_at"])
            if datetime.utcnow() - started_at > timedelta(hours=24):
                await redis_client.delete(key)
```

## Monitoring

### Key Count

```bash
# Connect to Redis
redis-cli

# Count keys by pattern
KEYS devices:online
KEYS session:*
KEYS device:sessions:*

# Get key count
DBSIZE
```

### Memory Usage

```bash
# Memory info
INFO memory

# Key memory
MEMORY USAGE session:550e8400
```

### Monitor Commands

```bash
# Monitor all commands in real-time
MONITOR

# Get slowlog
SLOWLOG GET 10
```

## Performance

### Connection Pooling

Backend uses connection pool:

```python
redis_client = await aioredis.from_url(
    REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=50
)
```

### Pipeline Commands

For multiple operations:

```python
pipe = redis_client.pipeline()
pipe.hset("devices:online", device_id, data)
pipe.sadd(f"device:sessions:{device_id}", session_id)
pipe.expire(f"session:{session_id}", 3600)
await pipe.execute()
```

### Lua Scripts

For atomic operations:

```lua
-- cleanup_device.lua
local device_id = ARGV[1]
local sessions = redis.call('SMEMBERS', 'device:sessions:' .. device_id)
for _, session_id in ipairs(sessions) do
    redis.call('DEL', 'session:' .. session_id)
end
redis.call('DEL', 'device:sessions:' .. device_id)
redis.call('HDEL', 'devices:online', device_id)
return #sessions
```

```python
# Load and execute
script = await redis_client.register_script(cleanup_script)
deleted = await script(args=[device_id])
```

## Fallback Mode

If Redis unavailable, in-memory fallback:

```python
class RedisClient:
    def __init__(self):
        self.redis = None
        self._memory_store = {}  # Fallback

    async def set(self, key, value):
        try:
            if self.redis:
                await self.redis.set(key, json.dumps(value))
            else:
                self._memory_store[key] = json.dumps(value)
        except Exception as e:
            logger.error(f"Redis error: {e}")
            # Fall back to memory
            self._memory_store[key] = json.dumps(value)
```

**Limitations**:
- No persistence across restarts
- No TTL support
- Not shared across backend instances

## Security

### Access Control

```bash
# redis.conf
requirepass your_redis_password
```

Update connection string:
```
redis://:your_redis_password@localhost:6379/0
```

### Network Security

```bash
# Bind to localhost only
bind 127.0.0.1

# Or use firewall
ufw allow from 10.0.0.0/24 to any port 6379
```

### Disable Dangerous Commands

```bash
# redis.conf
rename-command FLUSHDB ""
rename-command FLUSHALL ""
rename-command CONFIG "CONFIG_SECRET"
```

## Backup

### RDB Snapshots

```bash
# redis.conf
save 900 1      # After 900 sec if at least 1 key changed
save 300 10     # After 300 sec if at least 10 keys changed
save 60 10000   # After 60 sec if at least 10000 keys changed
```

### AOF (Append Only File)

```bash
# redis.conf
appendonly yes
appendfilename "appendonly.aof"
appendfsync everysec
```

### Manual Backup

```bash
# Trigger background save
redis-cli BGSAVE

# Copy RDB file
cp /var/lib/redis/dump.rdb /backup/dump_$(date +%Y%m%d).rdb
```

## Troubleshooting

### Connection Issues

```python
# Test connection
try:
    await redis_client.ping()
    logger.info("Redis connected")
except Exception as e:
    logger.error(f"Redis connection failed: {e}")
```

### Memory Issues

```bash
# Check memory usage
redis-cli INFO memory

# Set max memory
redis-cli CONFIG SET maxmemory 256mb
redis-cli CONFIG SET maxmemory-policy allkeys-lru
```

### Debugging

```bash
# Monitor commands
redis-cli MONITOR

# Get all keys (careful in production!)
redis-cli KEYS '*'

# Check key type
redis-cli TYPE session:550e8400

# Inspect hash
redis-cli HGETALL session:550e8400
```

## Best Practices

1. **Use appropriate data structures**: Hash for objects, Set for collections
2. **Set expiration**: Prevent memory leaks
3. **Namespace keys**: Use consistent naming
4. **Monitor memory**: Set maxmemory policy
5. **Handle failures**: Implement fallback
6. **Use pipelining**: Batch operations
7. **Avoid KEYS command**: Use SCAN in production
8. **Regular backups**: Enable RDB or AOF
9. **Secure access**: Use password, firewall
10. **Test fallback**: Ensure system works without Redis
