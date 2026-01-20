/**
 * 水下監視系統 Web Client - go2rtc WebRTC Streaming
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

(function () {
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
    console.log('[水下監視系統] Origin:', window.location.origin);
    console.log('[水下監視系統] Port:', window.location.port);
    console.log('[水下監視系統] API_BASE:', API_BASE);
    console.log('[水下監視系統] WS_BASE:', WS_BASE);

    let ws = null;
    let pc = null;
    let currentDeviceId = null;
    let isStreamingActive = false;

    /**
     * Initialize WebRTC connection using go2rtc
     */
    /**
     * Initialize WebRTC connection using go2rtc
     */
    window.initializeWebRTC = async function (deviceId) {
        currentDeviceId = deviceId;

        try {
            updateConnectionStatus('連線中...');

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
                updateConnectionStatus('錯誤');
            };

            ws.onclose = () => {
                console.log('WebSocket closed');
                stopStream();
                updateConnectionStatus('已斷線');
            };

        } catch (error) {
            console.error('Error initializing stream:', error);
            alert('無法連線，請重試。');
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
                iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
            });

            // Set up video element
            const videoElement = document.getElementById('remoteVideo');

            pc.ontrack = (event) => {
                console.log('Received track:', event.track.kind);
                if (event.streams && event.streams[0]) {
                    videoElement.srcObject = event.streams[0];
                    updateConnectionStatus('已連線');
                    isStreamingActive = true;
                }
            };

            pc.oniceconnectionstatechange = () => {
                console.log('ICE connection state:', pc.iceConnectionState);
                if (pc.iceConnectionState === 'connected') {
                    updateConnectionStatus('監控中');
                } else if (pc.iceConnectionState === 'disconnected' || pc.iceConnectionState === 'failed') {
                    updateConnectionStatus('已斷線');
                }
            };

            // Add transceiver for receiving video
            pc.addTransceiver('video', { direction: 'recvonly' });
            pc.addTransceiver('audio', { direction: 'recvonly' });

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
            updateConnectionStatus('錯誤');
            alert('無法啟動視訊串流: ' + error.message);
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
                updateConnectionStatus('裝置離線');
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
        updateConnectionStatus('已斷線');
    }

    /**
     * End watching
     */
    window.endWatching = function () {
        stopStream();
    };

    /**
     * Update connection status display
     */
    function updateConnectionStatus(status) {
        const statusElement = document.getElementById('connectionStatus');
        if (statusElement) {
            statusElement.textContent = status;

            // Map status text to classes for styling
            let statusClass = 'status-default';
            if (status.includes('連線中')) statusClass = 'status-connecting';
            else if (status.includes('監控中') || status.includes('已連線')) statusClass = 'status-connected';
            else if (status.includes('錯誤') || status.includes('離線') || status.includes('已斷線')) statusClass = 'status-error';

            // We need to implement these classes in CSS if we want specific colors, 
            // or we can just rely on the existing style and text change.
            // Earlier styles.css has generic .connection-status.connected etc.

            // For now, let's just clean up the class assignment to be robust
            statusElement.className = 'connection-status ' + statusClass;

            if (statusClass === 'status-connected') {
                statusElement.style.backgroundColor = 'rgba(16, 185, 129, 0.2)';
                statusElement.style.color = '#34d399';
            } else if (statusClass === 'status-error') {
                statusElement.style.backgroundColor = 'rgba(239, 68, 68, 0.2)';
                statusElement.style.color = '#f87171';
            } else {
                statusElement.style.backgroundColor = 'rgba(59, 130, 246, 0.2)';
                statusElement.style.color = '#60a5fa';
            }
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
    window.sendPTZCommand = async function (action, params = {}) {
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
     * Uses continuous_start on press, stop on release for smooth control
     */
    function initPTZControls() {
        const ptzControls = document.getElementById('ptzControls');
        if (!ptzControls) return;

        let activeButton = null;
        let isMoving = false;

        // Parse button params
        function getButtonParams(btn) {
            const action = btn.dataset.action;
            if (!action) return null;

            const params = {};
            if (btn.dataset.pan !== undefined) params.pan = parseFloat(btn.dataset.pan);
            if (btn.dataset.tilt !== undefined) params.tilt = parseFloat(btn.dataset.tilt);
            if (btn.dataset.zoom !== undefined) params.zoom = parseFloat(btn.dataset.zoom);
            if (btn.dataset.direction !== undefined) params.direction = btn.dataset.direction;
            if (btn.dataset.preset !== undefined) params.preset = parseInt(btn.dataset.preset);

            return { action, params };
        }

        // Start continuous movement
        function startMovement(btn) {
            const config = getButtonParams(btn);
            if (!config) return;

            // Stop any existing movement first
            if (isMoving) {
                stopMovement();
            }

            activeButton = btn;
            btn.classList.add('active');

            // For move actions (pan/tilt/zoom), use continuous_start (non-blocking)
            if (config.action === 'move') {
                isMoving = true;
                window.sendPTZCommand('continuous_start', config.params);
            }
            // For focus, use the original focus action with short duration (already smooth)
            else if (config.action === 'focus') {
                config.params.duration = 0.2;
                window.sendPTZCommand('focus', config.params);
            }
            // For other actions (stop, auto_focus, preset), just send once
            else {
                window.sendPTZCommand(config.action, config.params);
            }
        }

        // Stop movement
        function stopMovement() {
            if (activeButton) {
                activeButton.classList.remove('active');
                activeButton = null;
            }

            // Send stop command if we were moving
            if (isMoving) {
                isMoving = false;
                window.sendPTZCommand('stop', {});
            }
        }

        // Add event handlers to all PTZ buttons
        ptzControls.querySelectorAll('.ptz-btn').forEach(btn => {
            // Mouse events
            btn.addEventListener('mousedown', (e) => {
                e.preventDefault();
                startMovement(btn);
            });

            btn.addEventListener('mouseup', (e) => {
                e.preventDefault();
                stopMovement();
            });

            btn.addEventListener('mouseleave', (e) => {
                if (activeButton === btn) {
                    stopMovement();
                }
            });

            // Touch events for mobile
            btn.addEventListener('touchstart', (e) => {
                e.preventDefault();
                startMovement(btn);
            });

            btn.addEventListener('touchend', (e) => {
                e.preventDefault();
                stopMovement();
            });

            btn.addEventListener('touchcancel', (e) => {
                stopMovement();
            });

            // Prevent context menu on long press
            btn.addEventListener('contextmenu', (e) => {
                e.preventDefault();
            });
        });

        // Global mouseup/touchend to handle cases where mouse leaves button while pressed
        document.addEventListener('mouseup', stopMovement);
        document.addEventListener('touchend', stopMovement);

        console.log('[PTZ] Controls initialized with continuous movement support');
    }

    // Initialize PTZ controls when DOM is loaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPTZControls);
    } else {
        initPTZControls();
    }

    console.log('[水下監視系統] webrtc.js loaded successfully');
})();
