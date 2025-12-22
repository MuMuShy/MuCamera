# API Reference

REST API endpoints for the MuMu Camera system.

## Base URL

```
http://localhost:8000
```

Production:
```
https://your-domain.com
```

## Authentication

Most endpoints require authentication via JWT token.

### Header Format

```
Authorization: Bearer <jwt_token>
```

Or pass as query parameter:
```
?token=<jwt_token>
```

## Endpoints

### Authentication

#### Register User

```http
POST /api/auth/register
```

**Request Body**:
```json
{
  "username": "john_doe",
  "email": "john@example.com",
  "password": "secure_password"
}
```

**Response** (201 Created):
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "username": "john_doe",
    "email": "john@example.com"
  }
}
```

**Error** (400 Bad Request):
```json
{
  "detail": "Username already exists"
}
```

---

#### Login

```http
POST /api/auth/login
```

**Request Body**:
```json
{
  "username": "john_doe",
  "password": "secure_password"
}
```

**Response** (200 OK):
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "username": "john_doe",
    "email": "john@example.com"
  }
}
```

**Error** (401 Unauthorized):
```json
{
  "detail": "Incorrect username or password"
}
```

---

### Device Management

#### Register Device

```http
POST /api/devices/register
```

Public endpoint (no auth required) for initial device registration.

**Request Body**:
```json
{
  "device_id": "kitchen-cam-001",
  "device_name": "Kitchen Camera",
  "device_type": "camera"
}
```

**Response** (200 OK):
```json
{
  "device_id": "kitchen-cam-001",
  "message": "Device registered successfully"
}
```

**Notes**:
- If device already exists, returns success without error
- Device must be paired to user before use

---

#### Get User Devices

```http
GET /api/devices?token={jwt_token}
```

**Response** (200 OK):
```json
[
  {
    "id": 1,
    "device_id": "kitchen-cam-001",
    "device_name": "Kitchen Camera",
    "device_type": "camera",
    "is_online": true,
    "last_seen": "2025-01-01T12:34:56"
  },
  {
    "id": 2,
    "device_id": "garage-cam-001",
    "device_name": "Garage Camera",
    "device_type": "camera",
    "is_online": false,
    "last_seen": "2025-01-01T10:00:00"
  }
]
```

**Error** (401 Unauthorized):
```json
{
  "detail": "Invalid token"
}
```

---

#### Pair Device

```http
POST /api/devices/pair
```

Pair a device to the authenticated user using a pairing code.

**Request Body**:
```json
{
  "pairing_code": "123456",
  "token": "eyJ0eXAiOiJKV1QiLCJhbGc..."
}
```

**Response** (200 OK):
```json
{
  "message": "Device paired successfully",
  "device": {
    "device_id": "kitchen-cam-001",
    "device_name": "Kitchen Camera"
  }
}
```

**Error** (404 Not Found):
```json
{
  "detail": "Invalid or expired pairing code"
}
```

---

#### Get Device Status

```http
GET /api/devices/{device_id}/status
```

**Response** (200 OK):
```json
{
  "device_id": "kitchen-cam-001",
  "is_online": true,
  "last_seen": "2025-01-01T12:34:56"
}
```

**Error** (404 Not Found):
```json
{
  "detail": "Device not found"
}
```

---

### Pairing

#### Generate Pairing Code

```http
POST /api/pairing/generate?device_id={device_id}
```

Called by device agent to generate a pairing code.

**Response** (200 OK):
```json
{
  "code": "123456",
  "expires_at": "2025-01-01T12:39:56",
  "ttl": 300
}
```

**Error** (404 Not Found):
```json
{
  "detail": "Device not found"
}
```

**Notes**:
- Code is 6 digits
- Valid for 5 minutes (300 seconds)
- Can only be used once

---

### Health Check

#### Health

```http
GET /health
```

**Response** (200 OK):
```json
{
  "status": "healthy",
  "timestamp": "2025-01-01T12:34:56"
}
```

---

#### Root

```http
GET /
```

**Response** (200 OK):
```json
{
  "app": "MuMu Camera System",
  "version": "1.0.0",
  "status": "running"
}
```

---

## Error Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request - Invalid input |
| 401 | Unauthorized - Invalid/missing token |
| 404 | Not Found - Resource doesn't exist |
| 422 | Unprocessable Entity - Validation error |
| 500 | Internal Server Error |

## Error Response Format

```json
{
  "detail": "Error message describing what went wrong"
}
```

For validation errors (422):
```json
{
  "detail": [
    {
      "loc": ["body", "email"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

## Rate Limiting

Currently not implemented. Consider adding in production:

- Authentication: 5 requests per minute per IP
- API calls: 100 requests per minute per user
- Pairing: 10 requests per hour per device

## CORS

Configured via environment variable `BACKEND_CORS_ORIGINS`.

Default:
```
http://localhost,http://localhost:8080
```

Production example:
```
https://yourdomain.com,https://app.yourdomain.com
```

## Interactive Documentation

FastAPI provides interactive API documentation:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Example Usage

### Python

```python
import requests

# Register
response = requests.post('http://localhost:8000/api/auth/register', json={
    'username': 'testuser',
    'email': 'test@example.com',
    'password': 'password123'
})
data = response.json()
token = data['access_token']

# Get devices
response = requests.get(
    'http://localhost:8000/api/devices',
    params={'token': token}
)
devices = response.json()
print(devices)
```

### JavaScript

```javascript
// Register
const response = await fetch('http://localhost:8000/api/auth/register', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    username: 'testuser',
    email: 'test@example.com',
    password: 'password123'
  })
});
const data = await response.json();
const token = data.access_token;

// Get devices
const devicesResponse = await fetch(
  `http://localhost:8000/api/devices?token=${token}`
);
const devices = await devicesResponse.json();
console.log(devices);
```

### cURL

```bash
# Register
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","email":"test@example.com","password":"password123"}'

# Login
TOKEN=$(curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","password":"password123"}' \
  | jq -r '.access_token')

# Get devices
curl http://localhost:8000/api/devices?token=$TOKEN
```

## Future API Endpoints

Potential additions:

- `PUT /api/devices/{device_id}` - Update device settings
- `DELETE /api/devices/{device_id}` - Remove device
- `GET /api/sessions` - List watch session history
- `GET /api/users/me` - Get current user profile
- `PUT /api/users/me` - Update user profile
- `POST /api/devices/{device_id}/snapshot` - Capture snapshot
- `POST /api/devices/{device_id}/recording/start` - Start recording
- `POST /api/devices/{device_id}/recording/stop` - Stop recording
