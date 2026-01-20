/**
 * 水下監視系統 Web Client - Recordings Playback
 */

const API_BASE = window.location.origin.replace(':8080', ':8000');
let currentDeviceId = null;
let hlsPlayer = null;

// Check authentication
function checkAuth() {
    const token = localStorage.getItem('token');
    const user = JSON.parse(localStorage.getItem('user') || 'null');

    if (!token || !user) {
        window.location.href = 'login.html';
        return null;
    }

    const usernameEl = document.getElementById('username');
    if (usernameEl) usernameEl.textContent = user.username;

    const initialEl = document.getElementById('username-initial');
    if (initialEl && user.username) initialEl.textContent = user.username[0].toUpperCase();

    return token;
}

// Format file size
function formatSize(bytes) {
    if (!bytes) return '--';
    const mb = bytes / (1024 * 1024);
    if (mb >= 1024) {
        return (mb / 1024).toFixed(2) + ' GB';
    }
    return mb.toFixed(1) + ' MB';
}

// Format duration
function formatDuration(seconds) {
    if (!seconds) return '--';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) {
        return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    }
    return `${m}:${s.toString().padStart(2, '0')}`;
}

// Format datetime
function formatDateTime(isoString) {
    if (!isoString) return '--';
    const date = new Date(isoString);
    return date.toLocaleString('zh-TW', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// Load devices for selector
async function loadDevices() {
    const token = checkAuth();
    if (!token) return;

    const deviceSelect = document.getElementById('deviceSelect');

    try {
        const response = await fetch(`${API_BASE}/api/devices?token=${token}`);

        if (response.status === 401) {
            localStorage.removeItem('token');
            window.location.href = 'login.html';
            return;
        }

        const devices = await response.json();

        if (devices.length === 0) {
            deviceSelect.innerHTML = '<option value="">-- 尚無裝置 --</option>';
            return;
        }

        deviceSelect.innerHTML = '<option value="">-- 選擇裝置 --</option>' +
            devices.map(d => `<option value="${d.device_id}" ${d.is_online ? '' : 'disabled'}>${d.device_name || d.device_id} ${d.is_online ? '' : '(離線)'}</option>`).join('');

    } catch (error) {
        console.error('Error loading devices:', error);
        deviceSelect.innerHTML = '<option value="">載入失敗</option>';
    }
}

// Load recordings for selected device
async function loadRecordings() {
    const token = checkAuth();
    if (!token) return;

    const deviceId = document.getElementById('deviceSelect').value;
    const recordingsList = document.getElementById('recordingsList');
    const statsPanel = document.getElementById('statsPanel');

    if (!deviceId) {
        recordingsList.innerHTML = '<div class="empty-state">請選擇一個裝置來查看錄影</div>';
        statsPanel.innerHTML = '';
        return;
    }

    currentDeviceId = deviceId;

    // Show loading
    recordingsList.innerHTML = '<div class="loading-indicator"><div class="spinner"></div><p>載入錄影中...</p></div>';

    try {
        // Get date filter
        const startDate = document.getElementById('startDate').value;
        const endDate = document.getElementById('endDate').value;

        let url = `${API_BASE}/api/devices/${deviceId}/recordings?token=${token}`;
        if (startDate) url += `&start_date=${startDate}T00:00:00`;
        if (endDate) url += `&end_date=${endDate}T23:59:59`;

        const response = await fetch(url);

        if (response.status === 503) {
            recordingsList.innerHTML = '<div class="error-state">裝置離線，無法取得錄影列表</div>';
            return;
        }

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        const recordings = data.recordings || [];

        if (recordings.length === 0) {
            recordingsList.innerHTML = '<div class="empty-state">此時間範圍內沒有錄影</div>';
        } else {
            recordingsList.innerHTML = recordings.map(rec => `
                <div class="recording-card" onclick="playRecording('${rec.filename}')">
                    <div class="recording-thumbnail">
                        <div class="play-icon">播放</div>
                    </div>
                    <div class="recording-info">
                        <div class="recording-time">${formatDateTime(rec.start_time)}</div>
                        <div class="recording-meta">
                            <span>${formatSize(rec.file_size_bytes)}</span>
                            ${rec.duration_seconds ? `<span>${formatDuration(rec.duration_seconds)}</span>` : ''}
                        </div>
                    </div>
                    <div class="recording-actions">
                        <button class="btn btn-sm btn-secondary" onclick="event.stopPropagation(); downloadRecording('${rec.filename}')">
                            下載
                        </button>
                    </div>
                </div>
            `).join('');
        }

        // Load stats
        await loadStats();

    } catch (error) {
        console.error('Error loading recordings:', error);
        recordingsList.innerHTML = `<div class="error-state">載入錄影失敗: ${error.message}</div>`;
    }
}

// Load recording statistics
async function loadStats() {
    const token = checkAuth();
    if (!token || !currentDeviceId) return;

    const statsPanel = document.getElementById('statsPanel');

    try {
        const response = await fetch(`${API_BASE}/api/devices/${currentDeviceId}/recordings/stats?token=${token}`);

        if (!response.ok) return;

        const stats = await response.json();

        statsPanel.innerHTML = `
            <div class="stat-item">
                <span class="stat-label">錄影數量</span>
                <span class="stat-value">${stats.count || 0}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">總容量</span>
                <span class="stat-value">${stats.total_size_gib?.toFixed(2) || '0'} GB</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">最舊錄影</span>
                <span class="stat-value">${stats.oldest ? formatDateTime(stats.oldest) : '--'}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">最新錄影</span>
                <span class="stat-value">${stats.newest ? formatDateTime(stats.newest) : '--'}</span>
            </div>
        `;

    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

// Play recording with HLS
async function playRecording(filename) {
    const token = checkAuth();
    if (!token || !currentDeviceId) return;

    const playerOverlay = document.getElementById('playerOverlay');
    const videoPlayer = document.getElementById('videoPlayer');
    const playerFilename = document.getElementById('playerFilename');

    playerFilename.textContent = filename;
    playerOverlay.classList.add('active');

    // Build HLS URL
    const hlsUrl = `${API_BASE}/api/devices/${currentDeviceId}/recordings/${filename}/hls/playlist.m3u8?token=${token}`;

    console.log('[playback] Loading HLS:', hlsUrl);

    // Destroy previous player
    if (hlsPlayer) {
        hlsPlayer.destroy();
        hlsPlayer = null;
    }

    // Check if HLS.js is available and needed
    if (Hls.isSupported()) {
        hlsPlayer = new Hls({
            debug: false,
            enableWorker: true,
            lowLatencyMode: false
        });

        hlsPlayer.loadSource(hlsUrl);
        hlsPlayer.attachMedia(videoPlayer);

        hlsPlayer.on(Hls.Events.MANIFEST_PARSED, function() {
            console.log('[playback] HLS manifest loaded, starting playback');
            videoPlayer.play().catch(e => console.log('[playback] Autoplay blocked:', e));
        });

        hlsPlayer.on(Hls.Events.ERROR, function(event, data) {
            console.error('[playback] HLS error:', data);
            if (data.fatal) {
                switch (data.type) {
                    case Hls.ErrorTypes.NETWORK_ERROR:
                        console.log('[playback] Network error, retrying...');
                        hlsPlayer.startLoad();
                        break;
                    case Hls.ErrorTypes.MEDIA_ERROR:
                        console.log('[playback] Media error, recovering...');
                        hlsPlayer.recoverMediaError();
                        break;
                    default:
                        alert('播放失敗，請稍後再試');
                        closePlayer();
                        break;
                }
            }
        });

    } else if (videoPlayer.canPlayType('application/vnd.apple.mpegurl')) {
        // Native HLS support (Safari)
        videoPlayer.src = hlsUrl;
        videoPlayer.addEventListener('loadedmetadata', function() {
            videoPlayer.play().catch(e => console.log('[playback] Autoplay blocked:', e));
        });
    } else {
        alert('您的瀏覽器不支援 HLS 播放');
        closePlayer();
    }
}

// Close player
function closePlayer() {
    const playerOverlay = document.getElementById('playerOverlay');
    const videoPlayer = document.getElementById('videoPlayer');

    playerOverlay.classList.remove('active');
    videoPlayer.pause();
    videoPlayer.src = '';

    if (hlsPlayer) {
        hlsPlayer.destroy();
        hlsPlayer = null;
    }
}

// Download recording
function downloadRecording(filename) {
    const token = checkAuth();
    if (!token || !currentDeviceId) return;

    const downloadUrl = `${API_BASE}/api/devices/${currentDeviceId}/recordings/${filename}/download?token=${token}`;

    // Create hidden link and trigger download
    const a = document.createElement('a');
    a.href = downloadUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

// Event listeners
document.addEventListener('DOMContentLoaded', function() {
    checkAuth();
    loadDevices();

    // Device selector
    document.getElementById('deviceSelect').addEventListener('change', loadRecordings);

    // Date filters
    document.getElementById('startDate').addEventListener('change', loadRecordings);
    document.getElementById('endDate').addEventListener('change', loadRecordings);

    // Refresh button
    document.getElementById('refreshRecordingsBtn').addEventListener('click', loadRecordings);

    // Close player
    document.getElementById('closePlayerBtn').addEventListener('click', closePlayer);

    // Logout
    document.getElementById('logoutBtn').addEventListener('click', () => {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        window.location.href = 'login.html';
    });

    // Close player on escape
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            closePlayer();
        }
    });

    // Close player on overlay click
    document.getElementById('playerOverlay').addEventListener('click', function(e) {
        if (e.target === this) {
            closePlayer();
        }
    });
});
