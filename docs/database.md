# Database Schema

PostgreSQL database schema for the MuMu Camera system.

## Overview

The database stores persistent state including:
- User accounts
- Device registry
- Device ownership
- Pairing codes
- Watch session history

Transient state (online status, active sessions) is stored in Redis.

## Tables

### users

User accounts table.

```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_users_username ON users(username);
CREATE INDEX ix_users_email ON users(email);
```

**Columns**:
- `id`: Auto-incrementing primary key
- `username`: Unique username for login
- `email`: Unique email address
- `hashed_password`: bcrypt hashed password
- `is_active`: Account status (for soft delete/ban)
- `created_at`: Account creation timestamp
- `updated_at`: Last update timestamp (auto-updated)

**Sample Data**:
```sql
INSERT INTO users (username, email, hashed_password)
VALUES ('john_doe', 'john@example.com', '$2b$12$...');
```

---

### devices

Registered devices table.

```sql
CREATE TABLE devices (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(100) UNIQUE NOT NULL,
    device_name VARCHAR(255),
    device_type VARCHAR(50) NOT NULL DEFAULT 'camera',
    is_online BOOLEAN NOT NULL DEFAULT false,
    last_seen TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_devices_device_id ON devices(device_id);
```

**Columns**:
- `id`: Auto-incrementing primary key
- `device_id`: Unique device identifier (set by device)
- `device_name`: Human-readable device name
- `device_type`: Device type (camera, sensor, etc.)
- `is_online`: Current online status
- `last_seen`: Last connection timestamp
- `created_at`: Registration timestamp
- `updated_at`: Last update timestamp

**Sample Data**:
```sql
INSERT INTO devices (device_id, device_name, device_type)
VALUES ('kitchen-cam-001', 'Kitchen Camera', 'camera');
```

**Notes**:
- `is_online` updated by WebSocket connection handler
- `last_seen` updated on disconnect

---

### device_tokens

Device authentication tokens (optional, for future use).

```sql
CREATE TABLE device_tokens (
    id SERIAL PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP
);

CREATE INDEX ix_device_tokens_token_hash ON device_tokens(token_hash);
```

**Columns**:
- `id`: Auto-incrementing primary key
- `device_id`: Foreign key to devices table
- `token_hash`: Hashed authentication token
- `is_active`: Token status
- `created_at`: Creation timestamp
- `expires_at`: Expiration timestamp (NULL = never expires)

**Notes**:
- Currently not used (devices authenticate by ID only)
- Can be implemented for enhanced security

---

### device_ownership

User-device ownership mapping.

```sql
CREATE TABLE device_ownership (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL DEFAULT 'owner',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, device_id)
);

CREATE INDEX ix_device_ownership_user_device ON device_ownership(user_id, device_id);
```

**Columns**:
- `id`: Auto-incrementing primary key
- `user_id`: Foreign key to users table
- `device_id`: Foreign key to devices table
- `role`: Ownership role (owner, viewer, admin)
- `created_at`: Ownership creation timestamp

**Sample Data**:
```sql
INSERT INTO device_ownership (user_id, device_id, role)
VALUES (1, 1, 'owner');
```

**Notes**:
- One user can own multiple devices
- One device can have multiple owners (shared access)
- `UNIQUE` constraint prevents duplicate ownership

---

### pairing_codes

Temporary pairing codes for device setup.

```sql
CREATE TABLE pairing_codes (
    id SERIAL PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    code VARCHAR(10) UNIQUE NOT NULL,
    is_used BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP NOT NULL
);

CREATE INDEX ix_pairing_codes_code ON pairing_codes(code);
```

**Columns**:
- `id`: Auto-incrementing primary key
- `device_id`: Foreign key to devices table
- `code`: 6-digit pairing code
- `is_used`: Whether code has been used
- `created_at`: Creation timestamp
- `expires_at`: Expiration timestamp (typically +5 minutes)

**Sample Data**:
```sql
INSERT INTO pairing_codes (device_id, code, expires_at)
VALUES (1, '123456', NOW() + INTERVAL '5 minutes');
```

**Notes**:
- Codes are single-use
- Expired codes should be cleaned up periodically
- Code uniqueness enforced by database

---

### watch_sessions

Watch session history and active sessions.

```sql
CREATE TABLE watch_sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100) UNIQUE NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMP,
    ended_reason VARCHAR(255)
);

CREATE INDEX ix_watch_sessions_session_id ON watch_sessions(session_id);
CREATE INDEX ix_watch_sessions_status ON watch_sessions(status);
```

**Columns**:
- `id`: Auto-incrementing primary key
- `session_id`: Unique session identifier (UUID)
- `user_id`: Foreign key to users table (viewer)
- `device_id`: Foreign key to devices table
- `status`: Session status (pending, active, ended)
- `started_at`: Session start timestamp
- `ended_at`: Session end timestamp (NULL if active)
- `ended_reason`: Why session ended (user_ended, device_disconnected, etc.)

**Sample Data**:
```sql
INSERT INTO watch_sessions (session_id, user_id, device_id, status)
VALUES ('550e8400-e29b-41d4-a716-446655440000', 1, 1, 'active');
```

**Status Values**:
- `pending`: Session created, waiting for WebRTC connection
- `active`: WebRTC connection established, streaming
- `ended`: Session terminated

**Ended Reasons**:
- `user_ended`: User explicitly ended session
- `viewer_disconnected`: Viewer connection lost
- `device_disconnected`: Device connection lost
- `timeout`: Session timeout
- `error`: Error occurred

---

## Entity Relationship Diagram

```
┌──────────┐
│  users   │
└────┬─────┘
     │
     │ 1:N
     │
     ▼
┌─────────────────┐         ┌──────────┐
│device_ownership │ N:1     │ devices  │
└────────┬────────┘────────►└────┬─────┘
         │                       │
         │                       │ 1:N
         │                       │
         │                       ▼
         │              ┌────────────────┐
         │              │ device_tokens  │
         │              └────────────────┘
         │
         │                       │ 1:N
         │                       │
         │                       ▼
         │              ┌────────────────┐
         │              │ pairing_codes  │
         │              └────────────────┘
         │
         │ N:1                   │ N:1
         │                       │
         ▼                       ▼
    ┌──────────────────────────────┐
    │      watch_sessions          │
    └──────────────────────────────┘
```

## Queries

### Common Queries

**Get user's devices**:
```sql
SELECT d.*
FROM devices d
JOIN device_ownership o ON d.id = o.device_id
WHERE o.user_id = $1;
```

**Check device ownership**:
```sql
SELECT EXISTS(
    SELECT 1
    FROM device_ownership
    WHERE user_id = $1 AND device_id = $2
);
```

**Validate pairing code**:
```sql
SELECT * FROM pairing_codes
WHERE code = $1
  AND is_used = false
  AND expires_at > NOW();
```

**Get active watch sessions for device**:
```sql
SELECT * FROM watch_sessions
WHERE device_id = $1
  AND status = 'active';
```

**Get watch session history for user**:
```sql
SELECT ws.*, d.device_name, d.device_id
FROM watch_sessions ws
JOIN devices d ON ws.device_id = d.id
WHERE ws.user_id = $1
ORDER BY ws.started_at DESC
LIMIT 50;
```

---

## Migrations

Database migrations managed by Alembic.

### Initial Migration

File: `alembic/versions/001_initial_schema.py`

Creates all tables with proper indexes and foreign keys.

### Running Migrations

```bash
# Apply all migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# Rollback all migrations
alembic downgrade base

# Show current version
alembic current

# Show migration history
alembic history
```

### Creating New Migration

```bash
# Auto-generate from model changes
alembic revision --autogenerate -m "Add new_column to devices"

# Manual migration
alembic revision -m "Add custom index"
```

---

## Indexing Strategy

### Primary Indexes

- All primary keys automatically indexed
- Unique constraints create indexes

### Secondary Indexes

- `users.username`, `users.email`: Login queries
- `devices.device_id`: Device lookup
- `device_ownership(user_id, device_id)`: Ownership checks
- `pairing_codes.code`: Code validation
- `watch_sessions.session_id`: Session lookup
- `watch_sessions.status`: Active session queries

### Query Optimization

For high-traffic queries, consider:
- Composite indexes for multi-column filters
- Partial indexes for filtered queries
- BRIN indexes for timestamp columns (large tables)

---

## Data Retention

### Cleanup Strategies

**Expired pairing codes**:
```sql
DELETE FROM pairing_codes
WHERE expires_at < NOW() - INTERVAL '1 day';
```

**Old watch sessions**:
```sql
DELETE FROM watch_sessions
WHERE ended_at < NOW() - INTERVAL '90 days';
```

**Inactive users** (soft delete):
```sql
UPDATE users
SET is_active = false
WHERE last_login < NOW() - INTERVAL '1 year';
```

### Scheduled Tasks

Set up cron jobs or background tasks:

```python
# Backend background task
@app.on_event("startup")
async def cleanup_task():
    while True:
        await cleanup_expired_codes()
        await cleanup_old_sessions()
        await asyncio.sleep(3600)  # Every hour
```

---

## Backup Strategy

### Daily Backups

```bash
# Dump database
pg_dump -U mumucam mumucam > backup_$(date +%Y%m%d).sql

# Restore
psql -U mumucam mumucam < backup_20250101.sql
```

### Continuous Archiving

Enable WAL archiving for point-in-time recovery:

```sql
-- postgresql.conf
wal_level = replica
archive_mode = on
archive_command = 'cp %p /var/lib/postgresql/archive/%f'
```

---

## Performance Tuning

### Connection Pooling

Backend uses SQLAlchemy async connection pool:

```python
engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True
)
```

### Query Optimization

Use EXPLAIN ANALYZE:

```sql
EXPLAIN ANALYZE
SELECT d.*
FROM devices d
JOIN device_ownership o ON d.id = o.device_id
WHERE o.user_id = 1;
```

### Monitoring

Enable pg_stat_statements:

```sql
CREATE EXTENSION pg_stat_statements;

-- View slow queries
SELECT query, mean_exec_time, calls
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;
```

---

## Security

### Access Control

- Database user has limited permissions
- No superuser access from application
- SSL/TLS for connections in production

### SQL Injection Prevention

- All queries use parameterized statements
- SQLAlchemy ORM provides protection
- Never construct SQL from user input

### Sensitive Data

- Passwords: bcrypt hashed (12 rounds)
- Tokens: SHA-256 hashed
- Never log sensitive data

---

## Testing

### Test Database

Create separate test database:

```bash
createdb mumucam_test
export DATABASE_URL=postgresql://mumucam:pass@localhost/mumucam_test
pytest
```

### Fixtures

```python
@pytest.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()
```

### Sample Data

```sql
-- users
INSERT INTO users (username, email, hashed_password)
VALUES
    ('alice', 'alice@example.com', '$2b$12$...'),
    ('bob', 'bob@example.com', '$2b$12$...');

-- devices
INSERT INTO devices (device_id, device_name)
VALUES
    ('cam-001', 'Alice Kitchen'),
    ('cam-002', 'Bob Garage');

-- ownership
INSERT INTO device_ownership (user_id, device_id)
VALUES (1, 1), (2, 2);
```
