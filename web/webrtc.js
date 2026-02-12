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
    let currentStreamSrc = 'cam';
    let isStreamingActive = false;
    let gpsPollingInterval = null;
    let latencyMonitorId = null;
    let healthCheckInterval = null;
    let lastFramesReceived = 0;
    let stallCount = 0;
    let isReconnecting = false;

    /**
     * Low-latency playback monitor
     */
    function startLatencyMonitor(video) {
        if (latencyMonitorId) cancelAnimationFrame(latencyMonitorId);

        const check = () => {
            if (!isStreamingActive) return;
            if (video.buffered.length > 0) {
                const bufferedEnd = video.buffered.end(video.buffered.length - 1);
                const lag = bufferedEnd - video.currentTime;
                if (lag > 0.5) {
                    video.currentTime = bufferedEnd;
                }
            }
            latencyMonitorId = requestAnimationFrame(check);
        };
        latencyMonitorId = requestAnimationFrame(check);
    }

    function stopLatencyMonitor() {
        if (latencyMonitorId) {
            cancelAnimationFrame(latencyMonitorId);
            latencyMonitorId = null;
        }
    }

    /**
     * Stream health monitor - detects frozen video and auto-reconnects
     */
    function startHealthCheck() {
        stopHealthCheck();
        lastFramesReceived = 0;
        stallCount = 0;
        let checkCount = 0;

        console.log('[health] Monitor started');

        healthCheckInterval = setInterval(async () => {
            if (!pc || !isStreamingActive || isReconnecting) return;

            try {
                const stats = await pc.getStats();
                let currentFrames = 0;

                stats.forEach(report => {
                    if (report.type === 'inbound-rtp' && report.kind === 'video') {
                        currentFrames = report.framesReceived || 0;
                    }
                });

                checkCount++;
                // Log status every 8 checks (~16s)
                if (checkCount % 8 === 0) {
                    console.log(`[health] frames=${currentFrames}, ice=${pc.iceConnectionState}`);
                }

                // Detect stall: no new frames
                if (currentFrames === lastFramesReceived && checkCount > 1) {
                    stallCount++;
                    if (stallCount >= 3) {
                        console.warn(`[health] No new frames (${stallCount}/4), total=${currentFrames}`);
                    }

                    if (stallCount >= 4) {
                        console.warn('[health] Stream frozen, reconnecting...');
                        updateConnectionStatus('重新連線中...');
                        reconnectStream();
                    }
                } else {
                    if (stallCount >= 3) console.log('[health] Stream recovered');
                    stallCount = 0;
                }

                lastFramesReceived = currentFrames;
            } catch (e) {
                // pc might be closed
            }
        }, 3000);
    }

    function stopHealthCheck() {
        if (healthCheckInterval) {
            clearInterval(healthCheckInterval);
            healthCheckInterval = null;
        }
        stallCount = 0;
    }

    /**
     * Reconnect the WebRTC stream without closing WebSocket
     */
    async function reconnectStream(retries = 3) {
        if (isReconnecting || !currentDeviceId) return;
        isReconnecting = true;

        stopLatencyMonitor();
        stopHealthCheck();

        for (let attempt = 1; attempt <= retries; attempt++) {
            // Close old peer connection
            if (pc) {
                pc.close();
                pc = null;
            }

            try {
                console.log(`[health] Reconnect attempt ${attempt}/${retries}`);
                updateConnectionStatus(`重新連線 (${attempt}/${retries})...`);
                await startWebRTC(currentDeviceId);
                console.log('[health] Reconnect succeeded');
                isReconnecting = false;
                return;
            } catch (e) {
                console.error(`[health] Attempt ${attempt} failed:`, e.message);
                if (attempt < retries) {
                    await new Promise(r => setTimeout(r, 2000));
                }
            }
        }

        isReconnecting = false;
        updateConnectionStatus('重連失敗，點擊重試');
        console.error('[health] All reconnect attempts failed');
    }

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

            // Fetch TURN credentials from backend
            let iceServers = [{ urls: 'stun:stun.l.google.com:19302' }];
            try {
                const token = localStorage.getItem('token');
                const turnResp = await fetch(`${API_BASE}/api/turn/credentials?token=${token}`);
                if (turnResp.ok) {
                    const turnData = await turnResp.json();
                    iceServers = turnData.ice_servers;
                    console.log('Got TURN credentials, ICE servers:', iceServers.length);
                }
            } catch (e) {
                console.warn('Failed to fetch TURN credentials, using STUN only:', e);
            }

            // Create RTCPeerConnection
            pc = new RTCPeerConnection({ iceServers });

            // Set up video element with low-latency settings
            const videoElement = document.getElementById('remoteVideo');
            videoElement.autoplay = true;
            videoElement.playsInline = true;
            videoElement.muted = true;

            pc.ontrack = (event) => {
                console.log('Received track:', event.track.kind);
                if (event.streams && event.streams[0]) {
                    videoElement.srcObject = event.streams[0];
                    updateConnectionStatus('已連線');
                    isStreamingActive = true;

                    // Start monitors
                    startLatencyMonitor(videoElement);
                    startHealthCheck();
                }
            };

            pc.oniceconnectionstatechange = () => {
                console.log('ICE connection state:', pc.iceConnectionState);
                if (pc.iceConnectionState === 'connected') {
                    updateConnectionStatus('監控中');
                    stallCount = 0;
                } else if (pc.iceConnectionState === 'disconnected') {
                    updateConnectionStatus('連線不穩...');
                    // Wait 5s, if still disconnected, reconnect
                    setTimeout(() => {
                        if (pc && pc.iceConnectionState === 'disconnected') {
                            console.warn('[ICE] Still disconnected, reconnecting...');
                            reconnectStream();
                        }
                    }, 5000);
                } else if (pc.iceConnectionState === 'failed') {
                    updateConnectionStatus('重新連線中...');
                    reconnectStream();
                }
            };

            // Add transceiver for receiving video
            pc.addTransceiver('video', { direction: 'recvonly' });
            pc.addTransceiver('audio', { direction: 'recvonly' });

            // Create offer
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);

            // Wait for ICE candidates before sending offer
            // go2rtc needs browser's candidates (no trickle ICE in HTTP POST mode)
            // Send as soon as we have srflx (fast) or relay (needed for strict NAT)
            console.log('Gathering ICE candidates...');
            await new Promise((resolve) => {
                if (pc.iceGatheringState === 'complete') {
                    resolve();
                    return;
                }
                let hasSrflx = false;
                let resolved = false;
                const done = () => {
                    if (resolved) return;
                    resolved = true;
                    pc.removeEventListener('icecandidate', onCandidate);
                    pc.removeEventListener('icegatheringstatechange', onStateChange);
                    resolve();
                };
                const onCandidate = (e) => {
                    if (e.candidate && !hasSrflx) {
                        if (e.candidate.type === 'srflx' || e.candidate.type === 'relay') {
                            hasSrflx = true;
                            // Got public candidate - short delay for a few more, then send
                            setTimeout(done, 200);
                        }
                    }
                };
                const onStateChange = () => {
                    if (pc.iceGatheringState === 'complete') done();
                };
                pc.addEventListener('icecandidate', onCandidate);
                pc.addEventListener('icegatheringstatechange', onStateChange);
                setTimeout(done, 3000); // hard timeout
            });

            // Use the full local description which now includes gathered candidates
            const fullOffer = pc.localDescription;
            const offerCandidates = fullOffer.sdp.split('\n').filter(l => l.startsWith('a=candidate:'));
            console.log('Offer candidates:', offerCandidates.length);
            offerCandidates.forEach(c => console.log('  ', c.trim()));

            console.log('Sending offer to go2rtc via proxy');

            // Send offer to go2rtc's WebRTC endpoint via proxy
            const response = await fetch(
                `${API_BASE}/api/devices/${deviceId}/proxy/api/webrtc?src=${currentStreamSrc}`,
                {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded'
                    },
                    body: new URLSearchParams({
                        data: btoa(fullOffer.sdp)
                    })
                }
            );

            if (!response.ok) {
                throw new Error(`go2rtc WebRTC failed: ${response.status} ${response.statusText}`);
            }

            const rawAnswer = await response.text();
            const answerSDP = rawAnswer.trim();
            const decodedSDP = atob(answerSDP);
            const remoteCandidates = decodedSDP.split('\n').filter(l => l.startsWith('a=candidate:'));
            console.log('Answer received, remote candidates:', remoteCandidates.length);
            remoteCandidates.forEach(c => console.log('  ', c.trim()));

            // Set remote description
            await pc.setRemoteDescription({
                type: 'answer',
                sdp: decodedSDP
            });

            console.log('WebRTC connection established');

            // Start GPS polling after connection
            startGPSPolling();

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
     * GPS Functions
     */
    const GPS_STATUS_TEXT = {
        'disabled': '未啟用',
        'unavailable': '無資料',
        'searching': '搜尋中...',
        'fixed': '定位成功',
        'lost': '訊號遺失',
        'error': '錯誤'
    };

    async function fetchGPSData() {
        if (!currentDeviceId) return;

        try {
            const response = await fetch(`${API_BASE}/api/devices/${currentDeviceId}/gps`);
            if (!response.ok) return;

            const data = await response.json();
            updateGPSDisplay(data);
        } catch (error) {
            console.debug('[GPS] Fetch error:', error);
        }
    }

    function updateGPSDisplay(data) {
        const indicator = document.getElementById('gpsIndicator');
        const statusText = document.getElementById('gpsStatusText');
        const coords = document.getElementById('gpsCoords');
        const actions = document.getElementById('gpsActions');
        const latEl = document.getElementById('gpsLat');
        const lonEl = document.getElementById('gpsLon');
        const satsEl = document.getElementById('gpsSats');
        const speedRow = document.getElementById('gpsSpeedRow');
        const speedEl = document.getElementById('gpsSpeed');
        const mapLink = document.getElementById('gpsMapLink');

        if (!indicator || !statusText) return;

        const status = data.status || 'unavailable';
        const enabled = data.enabled;

        // Update indicator
        indicator.className = 'gps-indicator ' + status;

        // Update status text
        statusText.textContent = GPS_STATUS_TEXT[status] || status;

        // Show/hide coordinates based on status
        if (status === 'fixed' && data.lat !== null && data.lon !== null) {
            coords.style.display = 'block';
            actions.style.display = 'flex';

            latEl.textContent = data.lat.toFixed(6) + '°';
            lonEl.textContent = data.lon.toFixed(6) + '°';
            satsEl.textContent = data.satellites !== null ? data.satellites : '--';

            // Speed (convert knots to km/h)
            if (data.speed_knots !== null && data.speed_knots !== undefined) {
                speedRow.style.display = 'flex';
                const speedKmh = (data.speed_knots * 1.852).toFixed(1);
                speedEl.textContent = speedKmh + ' km/h';
            } else {
                speedRow.style.display = 'none';
            }

            // Update map link
            mapLink.href = `https://www.google.com/maps?q=${data.lat},${data.lon}`;
        } else {
            coords.style.display = 'none';
            actions.style.display = 'none';
        }
    }

    function startGPSPolling() {
        // Fetch immediately
        fetchGPSData();

        // Then poll every 5 seconds
        if (gpsPollingInterval) {
            clearInterval(gpsPollingInterval);
        }
        gpsPollingInterval = setInterval(fetchGPSData, 5000);
        console.log('[GPS] Polling started');
    }

    function stopGPSPolling() {
        if (gpsPollingInterval) {
            clearInterval(gpsPollingInterval);
            gpsPollingInterval = null;
            console.log('[GPS] Polling stopped');
        }

        // Reset GPS display
        const indicator = document.getElementById('gpsIndicator');
        const statusText = document.getElementById('gpsStatusText');
        const coords = document.getElementById('gpsCoords');
        const actions = document.getElementById('gpsActions');

        if (indicator) indicator.className = 'gps-indicator';
        if (statusText) statusText.textContent = '未啟用';
        if (coords) coords.style.display = 'none';
        if (actions) actions.style.display = 'none';
    }

    /**
     * Stop the video stream
     */
    function stopStream() {
        isStreamingActive = false;

        // Stop monitors
        stopLatencyMonitor();
        stopHealthCheck();

        // Stop GPS polling
        stopGPSPolling();

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
     * Switch stream source (e.g. 'cam' or 'cam2')
     */
    window.switchStream = async function (src) {
        if (src === currentStreamSrc) return;
        currentStreamSrc = src;
        console.log(`[stream] Switching to: ${src}`);

        // Update button active state
        document.querySelectorAll('.stream-switch-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.src === src);
        });

        if (!currentDeviceId || !isStreamingActive) return;

        // Reconnect with new stream source
        stopLatencyMonitor();
        stopHealthCheck();
        if (pc) {
            pc.close();
            pc = null;
        }
        isReconnecting = true;
        updateConnectionStatus('切換串流中...');
        try {
            await startWebRTC(currentDeviceId);
            isReconnecting = false;
        } catch (e) {
            console.error('[stream] Switch failed:', e);
            isReconnecting = false;
            updateConnectionStatus('切換失敗');
        }
    };

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
