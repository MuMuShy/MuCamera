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

    console.log('[MuMu Camera] webrtc.js loaded successfully');
})();
