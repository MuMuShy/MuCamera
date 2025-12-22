# WebSocket Protocol

WebSocket protocol specification for device-server and viewer-server communication.

## Endpoints

- **Device WebSocket**: `ws://localhost:8000/ws/device`
- **Viewer WebSocket**: `ws://localhost:8000/ws/viewer`

Production (with TLS):
- `wss://yourdomain.com/ws/device`
- `wss://yourdomain.com/ws/viewer`

## Message Format

All messages follow this structure:

```json
{
  "type": "message_type",
  "request_id": "optional_correlation_id",
  "ts": "2025-01-01T12:34:56.789Z",
  "payload": {
    // Type-specific data
  }
}
```

### Fields

- `type` (string, required): Message type identifier
- `request_id` (string, optional): Correlation ID for request/response matching
- `ts` (string, required): ISO 8601 timestamp
- `payload` (object, required): Type-specific data

## Device Messages

### Connection

#### Hello

Sent immediately after connection to identify device.

**Direction**: Device → Server

```json
{
  "type": "hello",
  "ts": "2025-01-01T12:34:56.789Z",
  "payload": {
    "device_id": "kitchen-cam-001"
  }
}
```

**Server Response**:

```json
{
  "type": "hello_ack",
  "ts": "2025-01-01T12:34:56.800Z",
  "payload": {
    "device_id": "kitchen-cam-001",
    "server_time": "2025-01-01T12:34:56.800Z"
  }
}
```

If device not found, connection is closed with code 1008.

---

#### Heartbeat

Sent periodically (default: every 30 seconds) to keep connection alive.

**Direction**: Device → Server

```json
{
  "type": "heartbeat",
  "ts": "2025-01-01T12:35:26.789Z",
  "payload": {}
}
```

**Server Response**:

```json
{
  "type": "heartbeat_ack",
  "ts": "2025-01-01T12:35:26.795Z",
  "payload": {}
}
```

---

### Signaling

#### Watch Request (Received)

Sent by server when a viewer wants to watch this device.

**Direction**: Server → Device

```json
{
  "type": "watch_request",
  "ts": "2025-01-01T12:36:00.000Z",
  "payload": {
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "user_id": "123",
    "ice_servers": [
      {
        "urls": "stun:stun.l.google.com:19302"
      },
      {
        "urls": [
          "turn:turn.example.com:3478?transport=udp",
          "turn:turn.example.com:3478?transport=tcp"
        ],
        "username": "1735740000:device_550e8400",
        "credential": "a1b2c3d4e5f6...",
        "credentialType": "password"
      }
    ]
  }
}
```

Device should:
1. Create RTCPeerConnection with provided ICE servers
2. Add video track
3. Wait for SDP offer from viewer

---

#### Signal Offer (Received)

SDP offer from viewer.

**Direction**: Server → Device

```json
{
  "type": "signal_offer",
  "ts": "2025-01-01T12:36:01.000Z",
  "payload": {
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "sdp": {
      "type": "offer",
      "sdp": "v=0\r\no=- 123456789 2 IN IP4 127.0.0.1\r\n..."
    }
  }
}
```

Device should:
1. Set remote description
2. Create answer
3. Set local description
4. Send signal_answer

---

#### Signal Answer (Sent)

SDP answer to viewer's offer.

**Direction**: Device → Server

```json
{
  "type": "signal_answer",
  "ts": "2025-01-01T12:36:01.500Z",
  "payload": {
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "sdp": {
      "type": "answer",
      "sdp": "v=0\r\no=- 987654321 2 IN IP4 127.0.0.1\r\n..."
    }
  }
}
```

---

#### Signal ICE

ICE candidate exchange (bidirectional).

**Direction**: Device ↔ Server

```json
{
  "type": "signal_ice",
  "ts": "2025-01-01T12:36:02.000Z",
  "payload": {
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "candidate": {
      "candidate": "candidate:1 1 UDP 2130706431 192.168.1.100 54321 typ host",
      "sdpMid": "0",
      "sdpMLineIndex": 0
    }
  }
}
```

Or null candidate (end of candidates):

```json
{
  "type": "signal_ice",
  "ts": "2025-01-01T12:36:05.000Z",
  "payload": {
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "candidate": null
  }
}
```

---

#### Watch Ended

Sent by server when watch session ends.

**Direction**: Server → Device

```json
{
  "type": "watch_ended",
  "ts": "2025-01-01T12:40:00.000Z",
  "payload": {
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "reason": "viewer_disconnected"
  }
}
```

Possible reasons:
- `viewer_disconnected`: Viewer closed connection
- `user_ended`: Viewer explicitly ended session
- `timeout`: Session timeout
- `error`: Error occurred

Device should close the peer connection.

---

### Presence

#### Device Presence

Optional message to update device status/metadata.

**Direction**: Device → Server

```json
{
  "type": "device_presence",
  "ts": "2025-01-01T12:34:56.789Z",
  "payload": {
    "battery": 85,
    "temperature": 45,
    "recording": false
  }
}
```

Stored in Redis for quick access.

---

## Viewer Messages

### Connection

#### Hello

Sent immediately after connection to authenticate.

**Direction**: Viewer → Server

```json
{
  "type": "hello",
  "ts": "2025-01-01T12:34:56.789Z",
  "payload": {
    "token": "eyJ0eXAiOiJKV1QiLCJhbGc..."
  }
}
```

**Server Response**:

```json
{
  "type": "hello_ack",
  "ts": "2025-01-01T12:34:56.800Z",
  "payload": {
    "user_id": "123",
    "server_time": "2025-01-01T12:34:56.800Z"
  }
}
```

If token invalid, connection is closed with code 1008.

---

#### Heartbeat

Same as device heartbeat.

**Direction**: Viewer → Server

```json
{
  "type": "heartbeat",
  "ts": "2025-01-01T12:35:26.789Z",
  "payload": {}
}
```

---

### Watch Session

#### Watch Request

Request to watch a specific device.

**Direction**: Viewer → Server

```json
{
  "type": "watch_request",
  "request_id": "req-001",
  "ts": "2025-01-01T12:36:00.000Z",
  "payload": {
    "device_id": "kitchen-cam-001"
  }
}
```

**Server Response** (Success):

```json
{
  "type": "watch_ready",
  "request_id": "req-001",
  "ts": "2025-01-01T12:36:00.100Z",
  "payload": {
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "ice_servers": [
      {
        "urls": "stun:stun.l.google.com:19302"
      },
      {
        "urls": [
          "turn:turn.example.com:3478?transport=udp"
        ],
        "username": "1735740000:viewer_123_550e8400",
        "credential": "f6e5d4c3b2a1...",
        "credentialType": "password"
      }
    ]
  }
}
```

**Server Response** (Error):

```json
{
  "type": "error",
  "request_id": "req-001",
  "ts": "2025-01-01T12:36:00.100Z",
  "payload": {
    "message": "Device is offline"
  }
}
```

Viewer should:
1. Create RTCPeerConnection with ICE servers
2. Create SDP offer
3. Send signal_offer

---

#### Signal Offer (Sent)

SDP offer to device.

**Direction**: Viewer → Server

```json
{
  "type": "signal_offer",
  "ts": "2025-01-01T12:36:01.000Z",
  "payload": {
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "sdp": {
      "type": "offer",
      "sdp": "v=0\r\no=- 123456789 2 IN IP4 127.0.0.1\r\n..."
    }
  }
}
```

---

#### Signal Answer (Received)

SDP answer from device.

**Direction**: Server → Viewer

```json
{
  "type": "signal_answer",
  "ts": "2025-01-01T12:36:01.500Z",
  "payload": {
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "sdp": {
      "type": "answer",
      "sdp": "v=0\r\no=- 987654321 2 IN IP4 127.0.0.1\r\n..."
    }
  }
}
```

Viewer should set this as remote description.

---

#### Signal ICE

Same as device signal_ice (bidirectional).

---

#### End Watch

Explicitly end watch session.

**Direction**: Viewer → Server

```json
{
  "type": "end_watch",
  "ts": "2025-01-01T12:40:00.000Z",
  "payload": {
    "session_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

Server will:
1. End session in database
2. Notify device
3. Clean up Redis data

---

## Error Messages

#### Generic Error

**Direction**: Server → Client

```json
{
  "type": "error",
  "request_id": "req-001",
  "ts": "2025-01-01T12:36:00.000Z",
  "payload": {
    "message": "Descriptive error message"
  }
}
```

Common errors:
- "Device not found"
- "Device is offline"
- "Invalid token"
- "Session not found"
- "Unauthorized"

---

## Connection States

### Device Connection Lifecycle

```
1. CONNECTING → WebSocket opening
2. CONNECTED → WebSocket open
3. HELLO_SENT → Sent hello message
4. AUTHENTICATED → Received hello_ack
5. ACTIVE → Can receive watch requests
6. DISCONNECTING → Closing connection
7. DISCONNECTED → Connection closed
```

### Watch Session Lifecycle

```
1. REQUESTED → watch_request sent
2. READY → watch_ready received
3. CONNECTING → Creating peer connection
4. OFFER_SENT → SDP offer sent
5. ANSWER_RECEIVED → SDP answer received
6. ICE_GATHERING → Exchanging ICE candidates
7. CONNECTED → Peer connection established
8. STREAMING → Video flowing
9. ENDING → end_watch sent
10. ENDED → Session terminated
```

## Connection Close Codes

| Code | Reason |
|------|--------|
| 1000 | Normal closure |
| 1001 | Going away |
| 1008 | Policy violation (auth failure) |
| 1011 | Server error |

## Best Practices

### Clients (Device & Viewer)

1. **Always send hello first**: Don't send other messages before hello_ack
2. **Implement heartbeat**: Send every 30 seconds
3. **Handle reconnection**: Exponential backoff on disconnect
4. **Clean up resources**: Close peer connections on watch_ended
5. **Validate messages**: Check for required fields
6. **Log errors**: Help with debugging

### Server

1. **Validate all messages**: Check type, payload structure
2. **Handle disconnects gracefully**: Clean up state
3. **Rate limit**: Prevent abuse
4. **Monitor connections**: Track active sessions
5. **Log important events**: Watch sessions, errors

## Example Flows

### Successful Watch Session

```
Viewer                Server              Device
  |                     |                   |
  |------ hello ------->|                   |
  |<--- hello_ack ------|                   |
  |                     |<----- hello ------|
  |                     |--- hello_ack ---->|
  |                     |                   |
  |- watch_request ---->|                   |
  |<-- watch_ready -----|                   |
  |                     |-- watch_request ->|
  |                     |                   |
  |-- signal_offer ----->                   |
  |                     |-- signal_offer -->|
  |                     |<- signal_answer --|
  |<- signal_answer -----|                  |
  |                     |                   |
  |<-- signal_ice ----->|<-- signal_ice --->|
  |                     |                   |
  [WebRTC connection established]
  |                     |                   |
  |-- end_watch ------->|                   |
  |                     |--- watch_ended -->|
  |                     |                   |
```

### Device Offline Error

```
Viewer                Server
  |                     |
  |- watch_request ---->|
  |                     | (checks device status)
  |<----- error --------|
  |  "Device is offline"|
  |                     |
```

### Device Disconnect During Session

```
Device                Server              Viewer
  |                     |                   |
  | [connection lost]   |                   |
  X                     |                   |
                        | (detect disconnect)|
                        |--- watch_ended -->|
                        | "device_disconnected"
                        |                   |
```
