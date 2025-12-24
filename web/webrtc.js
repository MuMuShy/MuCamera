/**
 * MuMu Camera Web Client - go2rtc MSE Streaming
 *
 * New architecture using go2rtc proxy mode:
 * - No WebRTC signaling needed
 * - Direct MSE streaming through Backend proxy
 * - Simpler and more reliable
 */

// Detect API base URL
// If accessed via Nginx (port 80/443), use same origin
// If accessed directly (port 8080), use port 8000 instead
const API_BASE = window.location.port === '8080'
    ? window.location.origin.replace(':8080', ':8000')
    : window.location.origin;

const WS_BASE = API_BASE.replace('http', 'ws');

// Debug logging
console.log('[MuMu Camera] Origin:', window.location.origin);
console.log('[MuMu Camera] Port:', window.location.port);
console.log('[MuMu Camera] API_BASE:', API_BASE);
console.log('[MuMu Camera] WS_BASE:', WS_BASE);

let ws = null;
let mediaSource = null;
let sourceBuffer = null;
let currentDeviceId = null;
let streamUrl = null;
let isStreamingActive = false;

/**
 * Initialize video stream for a device using go2rtc MSE
 */
async function initializeWebRTC(deviceId) {
    currentDeviceId = deviceId;

    try {
        // Connect to WebSocket for status updates only
        ws = new WebSocket(`${WS_BASE}/ws/viewer`);
        const token = localStorage.getItem('token');

        ws.onopen = async () => {
            console.log('WebSocket connected for status updates');

            // Send hello message
            sendMessage({
                type: 'hello',
                ts: new Date().toISOString(),
                payload: { token: token }
            });

            // Start MSE streaming
            await startMSEStream(deviceId);
        };

        ws.onmessage = async (event) => {
            const message = JSON.parse(event.data);
            handleStatusMessage(message);
        };

        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            updateConnectionStatus('Error');
        };

        ws.onclose = () => {
            console.log('WebSocket closed');
            stopStream();
            updateConnectionStatus('Disconnected');
        };

    } catch (error) {
        console.error('Error initializing stream:', error);
        alert('Failed to connect. Please try again.');
    }
}

/**
 * Start MSE streaming from go2rtc through proxy
 */
async function startMSEStream(deviceId) {
    try {
        updateConnectionStatus('Connecting');

        const videoElement = document.getElementById('remoteVideo');

        // Construct proxy URL to go2rtc MSE endpoint
        // go2rtc MSE endpoint: /api/stream.mp4?src=cam
        streamUrl = `${API_BASE}/api/devices/${deviceId}/proxy/api/stream.mp4?src=cam`;

        console.log('Starting MSE stream:', streamUrl);

        // Use native video element with progressive download
        videoElement.src = streamUrl;
        videoElement.play().catch(err => {
            console.error('Error playing video:', err);
            updateConnectionStatus('Error');
        });

        videoElement.onloadedmetadata = () => {
            console.log('Stream metadata loaded');
            updateConnectionStatus('Connected');
            isStreamingActive = true;
        };

        videoElement.onplay = () => {
            console.log('Stream started playing');
            updateConnectionStatus('Streaming');
        };

        videoElement.onerror = (err) => {
            console.error('Video element error:', err);
            updateConnectionStatus('Error');
            isStreamingActive = false;
        };

    } catch (error) {
        console.error('Error starting MSE stream:', error);
        updateConnectionStatus('Error');
    }
}

/**
 * Handle WebSocket status messages
 */
function handleStatusMessage(message) {
    console.log('Received status message:', message.type);

    switch (message.type) {
        case 'hello_ack':
            console.log('Server acknowledged connection');
            break;

        case 'heartbeat_ack':
            // Server heartbeat
            break;

        case 'device_offline':
            console.log('Device went offline');
            updateConnectionStatus('Device Offline');
            stopStream();
            break;

        default:
            console.log('Unknown message type:', message.type);
    }
}

/**
 * Send message via WebSocket
 */
function sendMessage(message) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(message));
    }
}

/**
 * Stop the video stream
 */
function stopStream() {
    isStreamingActive = false;

    const videoElement = document.getElementById('remoteVideo');
    if (videoElement) {
        videoElement.pause();
        videoElement.src = '';
        videoElement.load();
    }

    if (ws) {
        ws.close();
        ws = null;
    }

    currentDeviceId = null;
    streamUrl = null;

    updateConnectionStatus('Disconnected');
}

/**
 * End watching (alias for stopStream for compatibility)
 */
function endWatching() {
    stopStream();
}

/**
 * Update connection status display
 */
function updateConnectionStatus(status) {
    const statusElement = document.getElementById('connectionStatus');
    if (statusElement) {
        statusElement.textContent = status;
        statusElement.className = 'status-' + status.toLowerCase().replace(' ', '-');
    }
}

/**
 * Cleanup on page unload
 */
window.addEventListener('beforeunload', () => {
    stopStream();
});
