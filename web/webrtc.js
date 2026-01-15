/**
 * MuMu Camera Web Client - go2rtc WebRTC Streaming
 *
 * New architecture using go2rtc WebRTC:
 * - Direct WebRTC to go2rtc through Backend proxy
 * - No signaling needed, go2rtc handles it via HTTP
 * - Simple and reliable
 */

// Prevent multiple loading issues
if (typeof window.MuMuCamera === 'undefined') {
    window.MuMuCamera = {};
}

(function() {
    'use strict';

    // Detect API base URL
    const ORIGIN = window.location.origin;
    const HOST = window.location.hostname;

    // In production, use same origin (nginx proxies /api and /ws to backend)
    // In dev with port 8080, redirect to backend port 8000
    const API_BASE = (window.location.port === "8080")
        ? ORIGIN.replace(":8080", ":8000")
        : ORIGIN;

    const WS_BASE = API_BASE.replace("https://", "wss://").replace("http://", "ws://");

    // Debug logging
    console.log('[MuMu Camera] Origin:', window.location.origin);
    console.log('[MuMu Camera] Port:', window.location.port);
    console.log('[MuMu Camera] API_BASE:', API_BASE);
    console.log('[MuMu Camera] WS_BASE:', WS_BASE);

    let ws = null;
    let pc = null;
    let currentDeviceId = null;
    let isStreamingActive = false;

    /**
     * Initialize WebRTC connection using go2rtc
     */
    window.initializeWebRTC = async function(deviceId) {
        currentDeviceId = deviceId;

        try {
            updateConnectionStatus('Connecting');

            // Connect to WebSocket for status updates
            ws = new WebSocket(`${WS_BASE}/ws/viewer`);
            const token = localStorage.getItem('token');

            ws.onopen = async () => {
                console.log('WebSocket connected for status updates');
                sendMessage({
                    type: 'hello',
                    ts: new Date().toISOString(),
                    payload: { token: token }
                });

                // Start WebRTC connection to go2rtc
                await startWebRTC(deviceId);
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
    };

    /**
     * Start WebRTC connection to go2rtc through proxy
     */
    async function startWebRTC(deviceId) {
        try {
            console.log('Starting WebRTC connection to go2rtc');

            // Create RTCPeerConnection
            pc = new RTCPeerConnection({
                iceServers: [{urls: 'stun:stun.l.google.com:19302'}]
            });

            // Set up video element
            const videoElement = document.getElementById('remoteVideo');

            pc.ontrack = (event) => {
                console.log('Received track:', event.track.kind);
                if (event.streams && event.streams[0]) {
                    videoElement.srcObject = event.streams[0];
                    updateConnectionStatus('Connected');
                    isStreamingActive = true;
                }
            };

            pc.oniceconnectionstatechange = () => {
                console.log('ICE connection state:', pc.iceConnectionState);
                if (pc.iceConnectionState === 'connected') {
                    updateConnectionStatus('Streaming');
                } else if (pc.iceConnectionState === 'disconnected' || pc.iceConnectionState === 'failed') {
                    updateConnectionStatus('Disconnected');
                }
            };

            // Add transceiver for receiving video
            pc.addTransceiver('video', {direction: 'recvonly'});
            pc.addTransceiver('audio', {direction: 'recvonly'});

            // Create offer
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);

            console.log('Sending offer to go2rtc via proxy');

            // Send offer to go2rtc's WebRTC endpoint via proxy
            // go2rtc WebRTC endpoint: POST /api/webrtc?src=cam
            const response = await fetch(
                `${API_BASE}/api/devices/${deviceId}/proxy/api/webrtc?src=cam`,
                {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded'
                    },
                    body: new URLSearchParams({
                        data: btoa(offer.sdp)
                    })
                }
            );

            if (!response.ok) {
                throw new Error(`go2rtc WebRTC failed: ${response.status} ${response.statusText}`);
            }

            const answerSDP = await response.text();
            console.log('Received answer from go2rtc');

            // Set remote description
            await pc.setRemoteDescription({
                type: 'answer',
                sdp: atob(answerSDP)
            });

            console.log('WebRTC connection established');

        } catch (error) {
            console.error('Error starting WebRTC:', error);
            updateConnectionStatus('Error');
            alert('Failed to start video stream: ' + error.message);
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
            videoElement.srcObject = null;
        }

        if (pc) {
            pc.close();
            pc = null;
        }

        if (ws) {
            ws.close();
            ws = null;
        }

        currentDeviceId = null;
        updateConnectionStatus('Disconnected');
    }

    /**
     * End watching
     */
    window.endWatching = function() {
        stopStream();
    };

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

    /**
     * PTZ Control Functions
     */
    window.sendPTZCommand = async function(action, params = {}) {
        if (!currentDeviceId) {
            console.error('[PTZ] No device selected');
            return;
        }

        const payload = {
            action: action,
            ...params
        };

        console.log(`[PTZ] Sending ${action} to ${currentDeviceId}:`, params);

        try {
            const response = await fetch(`${API_BASE}/api/devices/${currentDeviceId}/ptz`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });

            const result = await response.json();
            console.log('[PTZ] Response:', result);

            if (!result.success && result.error) {
                console.error('[PTZ] Error:', result.error);
            }

            return result;
        } catch (error) {
            console.error('[PTZ] Request failed:', error);
            return { success: false, error: error.message };
        }
    };

    /**
     * Initialize PTZ controls when DOM is ready
     */
    function initPTZControls() {
        const ptzControls = document.getElementById('ptzControls');
        if (!ptzControls) return;

        // Add click handlers to all PTZ buttons
        ptzControls.querySelectorAll('.ptz-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                const action = btn.dataset.action;
                if (!action) return;

                const params = {};

                // Parse data attributes
                if (btn.dataset.pan !== undefined) params.pan = parseFloat(btn.dataset.pan);
                if (btn.dataset.tilt !== undefined) params.tilt = parseFloat(btn.dataset.tilt);
                if (btn.dataset.zoom !== undefined) params.zoom = parseFloat(btn.dataset.zoom);
                if (btn.dataset.direction !== undefined) params.direction = btn.dataset.direction;
                if (btn.dataset.preset !== undefined) params.preset = parseInt(btn.dataset.preset);
                if (btn.dataset.duration !== undefined) params.duration = parseFloat(btn.dataset.duration);

                // Default duration for move actions
                if (action === 'move' && params.duration === undefined) {
                    params.duration = 0.3;
                }

                btn.disabled = true;
                await window.sendPTZCommand(action, params);
                btn.disabled = false;
            });
        });

        console.log('[PTZ] Controls initialized');
    }

    // Initialize PTZ controls when DOM is loaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPTZControls);
    } else {
        initPTZControls();
    }

    console.log('[MuMu Camera] webrtc.js loaded successfully');
})();
