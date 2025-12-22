# MuMu Camera Web Client

Browser-based viewer application using native WebRTC APIs.

## Features

- **User Authentication**: Login and registration
- **Device Management**: View paired devices and their status
- **Device Pairing**: Pair new devices using 6-digit codes
- **Live Streaming**: Watch camera feeds via WebRTC
- **Connection Monitoring**: Real-time connection state display
- **Responsive Design**: Works on desktop and mobile browsers

## Technology

- **Pure JavaScript**: No frameworks, just vanilla JS
- **Native WebRTC**: Uses browser's built-in RTCPeerConnection API
- **WebSocket**: For signaling with central server
- **Modern CSS**: Responsive design with CSS Grid and Flexbox

## Files

- `index.html` - Main application (device list and video player)
- `login.html` - Authentication page
- `app.js` - Application logic (auth, device management)
- `webrtc.js` - WebRTC connection handling
- `styles.css` - Styling

## Usage

### Development

Simply serve the files with any static web server. If using nginx:

```bash
# Files are already in web/ directory
# Access via http://localhost:8080
```

Or use Python's built-in server:

```bash
cd web
python -m http.server 8080
```

### Production

Serve via nginx (see `../nginx/` directory configuration).

## Authentication Flow

1. User visits `login.html`
2. Login or register via REST API
3. Receive JWT token, store in localStorage
4. Redirect to `index.html`
5. Token used for all subsequent API calls and WebSocket auth

## Device Pairing Flow

1. Device generates pairing code via backend API
2. Device displays code to user
3. User enters code in web interface
4. Backend validates code and creates ownership record
5. Device appears in user's device list

## Watch Session Flow

1. User clicks "Watch" on an online device
2. WebSocket connection established to backend
3. Client sends `watch_request` with device_id
4. Backend sends `watch_ready` with ICE servers
5. Client creates RTCPeerConnection
6. Client creates SDP offer and sends to backend
7. Backend forwards offer to device
8. Device creates answer and sends back
9. ICE candidates exchanged
10. Peer connection established
11. Video streaming begins

## WebSocket Messages

### Sent by Client

**Hello**:
```javascript
{
  type: 'hello',
  ts: '2025-01-01T00:00:00Z',
  payload: {
    token: 'jwt_token_here'
  }
}
```

**Watch Request**:
```javascript
{
  type: 'watch_request',
  ts: '2025-01-01T00:00:00Z',
  payload: {
    device_id: 'device-001'
  }
}
```

**Signal Offer**:
```javascript
{
  type: 'signal_offer',
  ts: '2025-01-01T00:00:00Z',
  payload: {
    session_id: 'uuid',
    sdp: {
      type: 'offer',
      sdp: '...'
    }
  }
}
```

**Signal ICE**:
```javascript
{
  type: 'signal_ice',
  ts: '2025-01-01T00:00:00Z',
  payload: {
    session_id: 'uuid',
    candidate: {
      candidate: '...',
      sdpMid: '0',
      sdpMLineIndex: 0
    }
  }
}
```

**End Watch**:
```javascript
{
  type: 'end_watch',
  ts: '2025-01-01T00:00:00Z',
  payload: {
    session_id: 'uuid'
  }
}
```

### Received from Server

**Hello Ack**:
```javascript
{
  type: 'hello_ack',
  ts: '2025-01-01T00:00:00Z',
  payload: {
    user_id: '123',
    server_time: '2025-01-01T00:00:00Z'
  }
}
```

**Watch Ready**:
```javascript
{
  type: 'watch_ready',
  ts: '2025-01-01T00:00:00Z',
  payload: {
    session_id: 'uuid',
    ice_servers: [
      { urls: 'stun:stun.l.google.com:19302' },
      {
        urls: ['turn:...'],
        username: '...',
        credential: '...'
      }
    ]
  }
}
```

**Signal Answer**:
```javascript
{
  type: 'signal_answer',
  ts: '2025-01-01T00:00:00Z',
  payload: {
    session_id: 'uuid',
    sdp: {
      type: 'answer',
      sdp: '...'
    }
  }
}
```

## Browser Compatibility

- Chrome 80+
- Firefox 75+
- Safari 13+
- Edge 80+

Requires:
- WebRTC support
- WebSocket support
- localStorage support

## Configuration

Update API endpoint in `app.js` and `webrtc.js`:

```javascript
const API_BASE = 'http://your-server:8000';
const WS_BASE = 'ws://your-server:8000';
```

For production with HTTPS:

```javascript
const API_BASE = 'https://your-server.com';
const WS_BASE = 'wss://your-server.com';
```

## Troubleshooting

### Can't Connect to Backend

Check that `API_BASE` and `WS_BASE` are correct. Open browser console to see error messages.

### CORS Errors

Ensure backend has correct CORS configuration in `.env`:

```
BACKEND_CORS_ORIGINS=http://localhost:8080,https://yourdomain.com
```

### Video Not Showing

1. Check browser console for WebRTC errors
2. Verify ICE connection state in UI
3. Ensure TURN server is accessible
4. Check if device is sending video track

### WebSocket Connection Fails

1. Verify backend is running
2. Check WebSocket URL is correct
3. Ensure token is valid (check localStorage)
4. Look for authentication errors in network tab

## Security Considerations

1. **Always use HTTPS/WSS in production**
2. **Implement CSRF protection** for state-changing operations
3. **Validate and sanitize** all user input
4. **Use secure cookies** for session management in production
5. **Implement rate limiting** on authentication endpoints

## Performance Optimization

1. **Minimize reflows**: Batch DOM updates
2. **Debounce API calls**: Especially for device list refresh
3. **Clean up resources**: Always close peer connections and WebSockets
4. **Use browser caching**: Set appropriate cache headers

## Future Enhancements

- Two-way audio communication
- Recording capabilities
- Snapshot capture
- Device settings management
- Multiple simultaneous streams
- PWA support for mobile
- Push notifications for device status

## License

MIT
