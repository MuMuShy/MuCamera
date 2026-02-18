/**
 * 水下監視系統 Web Client - go2rtc WebRTC Streaming
 *
 * Dual-mode architecture:
 * - P2P mode: Direct WebRTC to go2rtc through Backend proxy (default)
 * - LiveKit mode: Connect to LiveKit SFU for multi-viewer support
 *
 * Mode is determined by backend config (LIVEKIT_ENABLED).
 * WebSocket is always used for PTZ, GPS, device status.
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

    // Use same origin - nginx proxies /api and /ws to backend
    const API_BASE = ORIGIN;

    const WS_BASE = API_BASE.replace("https://", "wss://").replace("http://", "ws://");

    // Debug logging
    console.log('[水下監視系統] Origin:', window.location.origin);
    console.log('[水下監視系統] Port:', window.location.port);
    console.log('[水下監視系統] API_BASE:', API_BASE);
    console.log('[水下監視系統] WS_BASE:', WS_BASE);

    /**
     * Show toast notification (replaces alert)
     */
    function showToast(message, type = 'info') {
        const existing = document.querySelector('.toast-notification');
        if (existing) existing.remove();

        const toast = document.createElement('div');
        toast.className = `toast-notification toast-${type}`;
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 5000);
    }

    let ws = null;
    let pc = null;
    let lkRoom = null;              // LiveKit Room instance
    let streamingMode = 'p2p';      // 'p2p' or 'livekit'
    let currentDeviceId = null;
    let currentCamera = 'cam';       // 'cam' or 'cam2'
    let currentQuality = 'sd';       // 'sd' (sub-stream) or 'hd' (main stream)
    let currentStreamSrc = 'cam_sub'; // derived: cam_sub, cam, cam2_sub, cam2
    let isStreamingActive = false;
    let gpsPollingInterval = null;
    let latencyMonitorId = null;
    let healthCheckInterval = null;
    let lastFramesReceived = 0;
    let stallCount = 0;
    let isReconnecting = false;

    /**
     * Detect streaming mode from backend
     */
    async function detectStreamingMode() {
        try {
            const resp = await fetch(`${API_BASE}/api/config/streaming-mode`);
            if (resp.ok) {
                const data = await resp.json();
                streamingMode = data.mode || 'p2p';
                console.log(`[mode] Streaming mode: ${streamingMode}`);
            }
        } catch (e) {
            console.warn('[mode] Failed to detect streaming mode, defaulting to P2P:', e);
            streamingMode = 'p2p';
        }
    }

    /**
     * Low-latency playback monitor (P2P mode only)
     */
    function startLatencyMonitor(video) {
        if (latencyMonitorId) cancelAnimationFrame(latencyMonitorId);

        const check = () => {
            if (!isStreamingActive) return;
            if (video.buffered.length > 0) {
                const bufferedEnd = video.buffered.end(video.buffered.length - 1);
                const lag = bufferedEnd - video.currentTime;
                if (lag > 0.15) {
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
     * Stream health monitor - detects frozen video and auto-reconnects (P2P mode)
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
                // Log status every 6 checks (~30s)
                if (checkCount % 6 === 0) {
                    console.log(`[health] frames=${currentFrames}, ice=${pc.iceConnectionState}`);
                }

                // Detect stall: no new frames
                if (currentFrames === lastFramesReceived && checkCount > 2) {
                    stallCount++;

                    // Never received any frame = wait longer for first keyframe
                    // Mid-stream freeze = more tolerant (4G jitter)
                    const threshold = (currentFrames === 0) ? 5 : 5;
                    const label = (currentFrames === 0) ? '無畫面' : '畫面凍結';

                    if (stallCount >= threshold - 1) {
                        console.warn(`[health] ${label} (${stallCount}/${threshold}), total=${currentFrames}`);
                    }

                    if (stallCount >= threshold) {
                        console.warn(`[health] ${label}，重新連線...`);
                        updateConnectionStatus('重新連線中...');
                        reconnectStream();
                    }
                } else {
                    if (stallCount >= 2) console.log('[health] Stream recovered');
                    stallCount = 0;
                }

                lastFramesReceived = currentFrames;
            } catch (e) {
                // pc might be closed
            }
        }, 5000);
    }

    function stopHealthCheck() {
        if (healthCheckInterval) {
            clearInterval(healthCheckInterval);
            healthCheckInterval = null;
        }
        stallCount = 0;
    }

    /**
     * Reconnect the WebRTC stream without closing WebSocket (P2P mode)
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
     * Initialize streaming connection (detects mode and starts accordingly)
     */
    window.initializeWebRTC = async function (deviceId) {
        currentDeviceId = deviceId;

        try {
            updateConnectionStatus('連線中...');

            // Detect streaming mode from backend
            await detectStreamingMode();

            // Connect to WebSocket for status updates (always needed for PTZ, GPS etc.)
            ws = new WebSocket(`${WS_BASE}/ws/viewer`);
            const token = localStorage.getItem('token');

            ws.onopen = async () => {
                console.log('WebSocket connected for status updates');
                sendMessage({
                    type: 'hello',
                    ts: new Date().toISOString(),
                    payload: { token: token }
                });

                // Start streaming based on mode
                if (streamingMode === 'livekit') {
                    await startLiveKit(deviceId);
                } else {
                    await startWebRTC(deviceId);
                }
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
            showToast('無法連線，請重試。', 'error');
        }
    };

    /**
     * Start LiveKit SFU connection
     */
    async function startLiveKit(deviceId) {
        try {
            console.log('[livekit] Starting LiveKit connection');
            updateConnectionStatus('連線 LiveKit...');

            // Get LiveKit token from backend
            const token = localStorage.getItem('token');
            const resp = await fetch(`${API_BASE}/api/devices/${deviceId}/livekit-token?token=${token}`);
            if (!resp.ok) {
                throw new Error(`Failed to get LiveKit token: ${resp.status}`);
            }
            const data = await resp.json();
            const lkToken = data.token;
            const lkUrl = data.url;

            // Check if LiveKit client SDK is loaded
            if (typeof LivekitClient === 'undefined') {
                throw new Error('LiveKit SDK not loaded');
            }

            // Create LiveKit Room
            lkRoom = new LivekitClient.Room({
                adaptiveStream: true,
                dynacast: true,
            });

            const videoElement = document.getElementById('remoteVideo');
            videoElement.autoplay = true;
            videoElement.playsInline = true;
            videoElement.muted = true;

            // Handle track subscription
            lkRoom.on(LivekitClient.RoomEvent.TrackSubscribed, (track, publication, participant) => {
                console.log(`[livekit] Track subscribed: ${track.kind} from ${participant.identity}`);
                if (track.kind === 'video') {
                    track.attach(videoElement);
                    updateConnectionStatus('監控中');
                    isStreamingActive = true;
                    startGPSPolling();
                } else if (track.kind === 'audio') {
                    // Attach audio to a hidden element
                    const audioEl = track.attach();
                    audioEl.style.display = 'none';
                    document.body.appendChild(audioEl);
                }
            });

            lkRoom.on(LivekitClient.RoomEvent.TrackUnsubscribed, (track) => {
                console.log(`[livekit] Track unsubscribed: ${track.kind}`);
                track.detach();
            });

            lkRoom.on(LivekitClient.RoomEvent.Disconnected, (reason) => {
                console.log('[livekit] Disconnected:', reason);
                updateConnectionStatus('LiveKit 已斷線');
            });

            lkRoom.on(LivekitClient.RoomEvent.Reconnecting, () => {
                console.log('[livekit] Reconnecting...');
                updateConnectionStatus('重新連線中...');
            });

            lkRoom.on(LivekitClient.RoomEvent.Reconnected, () => {
                console.log('[livekit] Reconnected');
                updateConnectionStatus('監控中');
            });

            // Connect to LiveKit server
            await lkRoom.connect(lkUrl, lkToken);
            console.log('[livekit] Connected to room:', lkRoom.name);
            updateConnectionStatus('已連線 (LiveKit)');

        } catch (error) {
            console.error('[livekit] Error:', error);
            updateConnectionStatus('LiveKit 連線失敗');
            showToast('LiveKit 連線失敗: ' + error.message, 'error');
        }
    }

    /**
     * Stop LiveKit connection
     */
    function stopLiveKit() {
        if (lkRoom) {
            lkRoom.disconnect();
            lkRoom = null;
        }
    }

    /**
     * Start WebRTC connection to go2rtc through proxy (P2P mode)
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
            showToast('無法啟動視訊串流: ' + error.message, 'error');
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
            const token = localStorage.getItem('token');
            const response = await fetch(`${API_BASE}/api/devices/${currentDeviceId}/gps?token=${token}`);
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

        // Clean up P2P connection
        if (pc) {
            pc.close();
            pc = null;
        }

        // Clean up LiveKit connection
        stopLiveKit();

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

            // Map status text to CSS classes (styles defined in styles.css)
            let statusClass = 'status-default';
            if (status.includes('連線中') || status.includes('切換') || status.includes('重新連線')) statusClass = 'status-connecting';
            else if (status.includes('監控中') || status.includes('已連線')) statusClass = 'status-connected';
            else if (status.includes('錯誤') || status.includes('離線') || status.includes('已斷線') || status.includes('失敗')) statusClass = 'status-error';

            statusElement.className = 'connection-status ' + statusClass;
            // Remove inline styles - CSS classes handle colors now
            statusElement.style.backgroundColor = '';
            statusElement.style.color = '';
        }
    }


    /**
     * Derive stream source from camera + quality
     */
    function getStreamSrc(camera, quality) {
        return quality === 'sd' ? camera + '_sub' : camera;
    }

    /**
     * Internal: reconnect stream with current settings
     */
    async function applyStreamSwitch() {
        if (!currentDeviceId || !isStreamingActive) return;

        if (streamingMode === 'livekit') {
            // In LiveKit mode, tell device to switch RTSP source via backend API
            updateConnectionStatus('切換串流中...');
            try {
                const token = localStorage.getItem('token');
                const resp = await fetch(
                    `${API_BASE}/api/devices/${currentDeviceId}/livekit-switch-stream?token=${token}`,
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ stream_src: currentStreamSrc })
                    }
                );
                if (resp.ok) {
                    console.log(`[livekit] Switch stream to ${currentStreamSrc} requested`);
                    updateConnectionStatus('監控中');
                } else {
                    console.error('[livekit] Switch failed:', resp.status);
                    updateConnectionStatus('切換失敗');
                }
            } catch (e) {
                console.error('[livekit] Switch error:', e);
                updateConnectionStatus('切換失敗');
            }
            return;
        }

        // P2P mode: close and reconnect with new stream source
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
    }

    /**
     * Switch camera (cam / cam2)
     */
    window.switchCamera = async function (cam) {
        if (cam === currentCamera) return;
        currentCamera = cam;
        currentStreamSrc = getStreamSrc(currentCamera, currentQuality);
        console.log(`[stream] Camera → ${cam}, stream: ${currentStreamSrc}`);

        // Update camera button active state
        document.querySelectorAll('.stream-switch-btn[data-cam]').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.cam === cam);
        });

        await applyStreamSwitch();
    };

    /**
     * Toggle quality (sd / hd)
     */
    window.toggleQuality = async function (quality) {
        if (quality === currentQuality) return;
        currentQuality = quality;
        currentStreamSrc = getStreamSrc(currentCamera, currentQuality);
        console.log(`[stream] Quality → ${quality}, stream: ${currentStreamSrc}`);

        // Update quality button active state
        document.getElementById('btnSD').classList.toggle('active', quality === 'sd');
        document.getElementById('btnHD').classList.toggle('active', quality === 'hd');

        await applyStreamSwitch();
    };

    // Keep backward compatibility
    window.switchStream = window.switchCamera;

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
            source: currentCamera,
            ...params
        };

        console.log(`[PTZ] Sending ${action} to ${currentDeviceId}:`, params);

        try {
            const token = localStorage.getItem('token');
            const response = await fetch(`${API_BASE}/api/devices/${currentDeviceId}/ptz?token=${token}`, {
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

    /**
     * Controls sidebar toggle (collapse/expand)
     */
    function initControlsToggle() {
        const toggleBtn = document.getElementById('toggleControlsBtn');
        const controlsSidebar = document.querySelector('#watchSection .controls-sidebar');
        if (!toggleBtn || !controlsSidebar) return;

        function setIcon(collapsed) {
            const icon = collapsed ? 'panel-right-open' : 'panel-right-close';
            toggleBtn.innerHTML = `<i data-lucide="${icon}" style="width: 20px; height: 20px;"></i>`;
            lucide.createIcons({ nodes: [toggleBtn] });
        }

        toggleBtn.addEventListener('click', function () {
            const collapsed = controlsSidebar.classList.toggle('collapsed');
            localStorage.setItem('controls_collapsed', collapsed);
            setIcon(collapsed);
        });

        // Restore preference
        if (localStorage.getItem('controls_collapsed') === 'true') {
            controlsSidebar.classList.add('collapsed');
            setIcon(true);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initControlsToggle);
    } else {
        initControlsToggle();
    }

    console.log('[水下監視系統] webrtc.js loaded successfully');
})();
