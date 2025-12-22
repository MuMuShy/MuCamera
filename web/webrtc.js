/**
 * MuMu Camera Web Client - WebRTC Logic
 */

const WS_BASE = window.location.origin.replace(':8080', ':8000').replace('http', 'ws');

let ws = null;
let peerConnection = null;
let currentSessionId = null;
let iceServers = [];

/**
 * Initialize WebRTC connection for watching a device
 */
async function initializeWebRTC(deviceId) {
    const token = localStorage.getItem('token');

    try {
        // Connect to WebSocket
        ws = new WebSocket(`${WS_BASE}/ws/viewer`);

        ws.onopen = async () => {
            console.log('WebSocket connected');

            // Send hello message with token
            sendMessage({
                type: 'hello',
                ts: new Date().toISOString(),
                payload: {
                    token: token
                }
            });

            // Wait a bit for hello_ack, then send watch request
            setTimeout(() => {
                sendMessage({
                    type: 'watch_request',
                    ts: new Date().toISOString(),
                    payload: {
                        device_id: deviceId
                    }
                });
            }, 500);
        };

        ws.onmessage = async (event) => {
            const message = JSON.parse(event.data);
            await handleMessage(message);
        };

        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            updateConnectionStatus('Error');
        };

        ws.onclose = () => {
            console.log('WebSocket closed');
            updateConnectionStatus('Disconnected');
        };

    } catch (error) {
        console.error('Error initializing WebRTC:', error);
        alert('Failed to connect. Please try again.');
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
 * Handle incoming WebSocket message
 */
async function handleMessage(message) {
    console.log('Received message:', message.type);

    switch (message.type) {
        case 'hello_ack':
            console.log('Server acknowledged connection');
            break;

        case 'watch_ready':
            // Server is ready, we received ICE servers
            currentSessionId = message.payload.session_id;
            iceServers = message.payload.ice_servers;
            document.getElementById('sessionId').textContent = currentSessionId;

            // Create peer connection and send offer
            await createPeerConnection();
            await createOffer();
            break;

        case 'signal_answer':
            // Received SDP answer from device
            await handleAnswer(message.payload.sdp);
            break;

        case 'signal_ice':
            // Received ICE candidate from device
            await handleIceCandidate(message.payload.candidate);
            break;

        case 'watch_ended':
            console.log('Watch session ended:', message.payload.reason);
            alert(`Watch session ended: ${message.payload.reason}`);
            endWatching();
            break;

        case 'error':
            console.error('Server error:', message.payload.message);
            alert(`Error: ${message.payload.message}`);
            break;

        case 'heartbeat_ack':
            // Heartbeat acknowledged
            break;

        default:
            console.log('Unknown message type:', message.type);
    }
}

/**
 * Create RTCPeerConnection
 */
async function createPeerConnection() {
    const configuration = {
        iceServers: iceServers.map(server => {
            const config = {
                urls: Array.isArray(server.urls) ? server.urls : [server.urls]
            };
            if (server.username) {
                config.username = server.username;
                config.credential = server.credential;
            }
            return config;
        })
    };

    console.log('Creating peer connection with ICE servers:', configuration);

    peerConnection = new RTCPeerConnection(configuration);

    // Handle incoming tracks
    peerConnection.ontrack = (event) => {
        console.log('Received remote track');
        const remoteVideo = document.getElementById('remoteVideo');
        if (remoteVideo.srcObject !== event.streams[0]) {
            remoteVideo.srcObject = event.streams[0];
            console.log('Set remote video stream');
        }
    };

    // Handle ICE candidates
    peerConnection.onicecandidate = (event) => {
        if (event.candidate) {
            console.log('Sending ICE candidate');
            sendMessage({
                type: 'signal_ice',
                ts: new Date().toISOString(),
                payload: {
                    session_id: currentSessionId,
                    candidate: {
                        candidate: event.candidate.candidate,
                        sdpMid: event.candidate.sdpMid,
                        sdpMLineIndex: event.candidate.sdpMLineIndex
                    }
                }
            });
        }
    };

    // Handle connection state changes
    peerConnection.onconnectionstatechange = () => {
        console.log('Connection state:', peerConnection.connectionState);
        document.getElementById('connectionState').textContent = peerConnection.connectionState;
        updateConnectionStatus(peerConnection.connectionState);
    };

    peerConnection.oniceconnectionstatechange = () => {
        console.log('ICE connection state:', peerConnection.iceConnectionState);
        document.getElementById('iceState').textContent = peerConnection.iceConnectionState;
    };
}

/**
 * Create and send offer
 */
async function createOffer() {
    try {
        console.log('Creating offer');
        const offer = await peerConnection.createOffer({
            offerToReceiveVideo: true,
            offerToReceiveAudio: false
        });

        await peerConnection.setLocalDescription(offer);
        console.log('Set local description');

        // Send offer to device via server
        sendMessage({
            type: 'signal_offer',
            ts: new Date().toISOString(),
            payload: {
                session_id: currentSessionId,
                sdp: {
                    type: offer.type,
                    sdp: offer.sdp
                }
            }
        });

        console.log('Sent offer');
    } catch (error) {
        console.error('Error creating offer:', error);
    }
}

/**
 * Handle SDP answer from device
 */
async function handleAnswer(answerData) {
    try {
        console.log('Received answer');
        const answer = new RTCSessionDescription(answerData);
        await peerConnection.setRemoteDescription(answer);
        console.log('Set remote description');
    } catch (error) {
        console.error('Error handling answer:', error);
    }
}

/**
 * Handle ICE candidate from device
 */
async function handleIceCandidate(candidateData) {
    try {
        if (candidateData && peerConnection) {
            const candidate = new RTCIceCandidate(candidateData);
            await peerConnection.addIceCandidate(candidate);
            console.log('Added ICE candidate');
        }
    } catch (error) {
        console.error('Error handling ICE candidate:', error);
    }
}

/**
 * Update connection status display
 */
function updateConnectionStatus(status) {
    const statusEl = document.getElementById('connectionStatus');
    statusEl.textContent = status;

    // Update class for styling
    statusEl.className = 'connection-status';
    if (status === 'connected') {
        statusEl.classList.add('connected');
    } else if (status === 'failed' || status === 'disconnected' || status === 'Error') {
        statusEl.classList.add('error');
    }
}

/**
 * End watching session
 */
function endWatching() {
    // Send end watch message
    if (ws && currentSessionId) {
        sendMessage({
            type: 'end_watch',
            ts: new Date().toISOString(),
            payload: {
                session_id: currentSessionId
            }
        });
    }

    // Close peer connection
    if (peerConnection) {
        peerConnection.close();
        peerConnection = null;
    }

    // Close WebSocket
    if (ws) {
        ws.close();
        ws = null;
    }

    // Clear video
    const remoteVideo = document.getElementById('remoteVideo');
    if (remoteVideo.srcObject) {
        remoteVideo.srcObject.getTracks().forEach(track => track.stop());
        remoteVideo.srcObject = null;
    }

    // Reset state
    currentSessionId = null;
    iceServers = [];

    console.log('Watch session ended');
}

// Send heartbeat every 30 seconds
setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        sendMessage({
            type: 'heartbeat',
            ts: new Date().toISOString(),
            payload: {}
        });
    }
}, 30000);
