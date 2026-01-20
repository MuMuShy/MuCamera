/**
 * 水下監視系統 Web Client - Timeline Playback
 * 支援 24 小時時間軸拖曳播放
 */

const API_BASE = window.location.origin.replace(':8080', ':8000');
let currentDeviceId = null;
let hlsPlayer = null;
let timelineData = null;
let currentPlaybackTime = null;
let isPlaying = false;

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

// Format utilities
function formatSize(bytes) {
    if (!bytes) return '--';
    const mb = bytes / (1024 * 1024);
    if (mb >= 1024) {
        return (mb / 1024).toFixed(2) + ' GB';
    }
    return mb.toFixed(1) + ' MB';
}

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

function formatTime(date) {
    return date.toLocaleTimeString('zh-TW', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
    });
}

function formatTimeShort(date) {
    return date.toLocaleTimeString('zh-TW', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    });
}

// Get today's date in YYYY-MM-DD format
function getTodayDate() {
    const now = new Date();
    return now.toISOString().split('T')[0];
}

// Initialize timeline hour markers
function initTimelineHours() {
    const hoursContainer = document.querySelector('.timeline-hours');
    if (!hoursContainer) return;

    hoursContainer.innerHTML = '';
    for (let h = 0; h <= 24; h += 2) {
        const mark = document.createElement('span');
        mark.className = 'timeline-hour-mark';
        mark.textContent = h.toString().padStart(2, '0');
        hoursContainer.appendChild(mark);
    }
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

// Load timeline data for selected date
async function loadTimeline() {
    const token = checkAuth();
    if (!token) return;

    const deviceId = document.getElementById('deviceSelect').value;
    const date = document.getElementById('timelineDate').value;

    if (!deviceId || !date) {
        return;
    }

    currentDeviceId = deviceId;

    const timelineTrack = document.getElementById('timelineTrack');

    try {
        const response = await fetch(`${API_BASE}/api/devices/${deviceId}/recordings/timeline?date=${date}&token=${token}`);

        if (response.status === 503) {
            console.error('Device offline');
            return;
        }

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        timelineData = await response.json();
        renderTimeline();

        // Also load recordings list
        await loadRecordings();
        await loadStats();

    } catch (error) {
        console.error('Error loading timeline:', error);
        timelineData = { segments: [] };
        renderTimeline();
    }
}

// Render timeline segments
function renderTimeline() {
    const timelineTrack = document.getElementById('timelineTrack');
    if (!timelineTrack || !timelineData) return;

    // Clear existing segments (keep cursor and hover indicator)
    const cursor = document.getElementById('timelineCursor');
    const hoverIndicator = document.getElementById('timelineHoverIndicator');
    timelineTrack.innerHTML = '';
    timelineTrack.appendChild(cursor);
    timelineTrack.appendChild(hoverIndicator);

    const date = document.getElementById('timelineDate').value;
    const dayStart = new Date(`${date}T00:00:00`);
    const dayEnd = new Date(`${date}T23:59:59`);
    const totalMs = dayEnd - dayStart;

    // Render each segment
    timelineData.segments.forEach(segment => {
        const startTime = new Date(segment.start_time);
        const endTime = new Date(segment.end_time);

        // Calculate position and width as percentage
        const startOffset = Math.max(0, startTime - dayStart);
        const endOffset = Math.min(totalMs, endTime - dayStart);

        const left = (startOffset / totalMs) * 100;
        const width = ((endOffset - startOffset) / totalMs) * 100;

        if (width > 0) {
            const segmentEl = document.createElement('div');
            segmentEl.className = 'timeline-segment';
            segmentEl.style.left = `${left}%`;
            segmentEl.style.width = `${width}%`;
            segmentEl.dataset.filename = segment.filename;
            segmentEl.dataset.startTime = segment.start_time;
            segmentEl.title = `${formatTimeShort(startTime)} - ${formatTimeShort(endTime)}`;
            timelineTrack.appendChild(segmentEl);
        }
    });

    console.log(`[timeline] Rendered ${timelineData.segments.length} segments`);
}

// Handle timeline click - start playback from clicked position
async function handleTimelineClick(event) {
    const token = checkAuth();
    if (!token || !currentDeviceId) return;

    const timelineTrack = document.getElementById('timelineTrack');
    const rect = timelineTrack.getBoundingClientRect();
    const clickX = event.clientX - rect.left;
    const percentage = clickX / rect.width;

    const date = document.getElementById('timelineDate').value;
    const dayStart = new Date(`${date}T00:00:00`);
    const clickedTime = new Date(dayStart.getTime() + percentage * 24 * 60 * 60 * 1000);

    console.log(`[timeline] Clicked at ${formatTime(clickedTime)}`);

    // Check if there's a recording at this time
    const segment = findSegmentAtTime(clickedTime);
    if (!segment) {
        console.log('[timeline] No recording at this time');
        return;
    }

    // Start playback from this time
    await startPlaybackFromTime(clickedTime);
}

// Find segment that contains the given time
function findSegmentAtTime(time) {
    if (!timelineData || !timelineData.segments) return null;

    for (const segment of timelineData.segments) {
        const startTime = new Date(segment.start_time);
        const endTime = new Date(segment.end_time);

        if (time >= startTime && time <= endTime) {
            return segment;
        }
    }
    return null;
}

// Start playback from a specific time
async function startPlaybackFromTime(startTime) {
    const token = checkAuth();
    if (!token || !currentDeviceId) return;

    const playerInline = document.getElementById('playerInline');
    const videoPlayer = document.getElementById('videoPlayer');
    const timeDisplay = document.getElementById('playerTimeDisplay');
    const cursor = document.getElementById('timelineCursor');

    // Show player
    playerInline.style.display = 'block';
    currentPlaybackTime = startTime;
    isPlaying = true;

    // Update cursor position
    updateCursorPosition(startTime);
    cursor.classList.add('active');

    // Update time display
    timeDisplay.textContent = formatTime(startTime);

    // Build stream URL
    const startISO = startTime.toISOString();
    const streamUrl = `${API_BASE}/api/devices/${currentDeviceId}/recordings/stream?start=${encodeURIComponent(startISO)}&token=${token}`;

    console.log('[playback] Loading stream from:', startISO);

    // Destroy previous player
    if (hlsPlayer) {
        hlsPlayer.destroy();
        hlsPlayer = null;
    }

    // Initialize HLS player
    if (Hls.isSupported()) {
        hlsPlayer = new Hls({
            debug: false,
            enableWorker: true,
            lowLatencyMode: false
        });

        hlsPlayer.loadSource(streamUrl);
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
                        console.error('[playback] Fatal error, stopping');
                        stopPlayback();
                        break;
                }
            }
        });

        // Update cursor position during playback
        videoPlayer.addEventListener('timeupdate', handleTimeUpdate);

    } else if (videoPlayer.canPlayType('application/vnd.apple.mpegurl')) {
        // Native HLS support (Safari)
        videoPlayer.src = streamUrl;
        videoPlayer.addEventListener('loadedmetadata', function() {
            videoPlayer.play().catch(e => console.log('[playback] Autoplay blocked:', e));
        });
    } else {
        alert('您的瀏覽器不支援 HLS 播放');
        stopPlayback();
    }
}

// Handle video time update - sync cursor with playback
function handleTimeUpdate() {
    const videoPlayer = document.getElementById('videoPlayer');
    if (!currentPlaybackTime || !isPlaying) return;

    // Calculate current playback time
    const currentTime = new Date(currentPlaybackTime.getTime() + videoPlayer.currentTime * 1000);

    // Update cursor position
    updateCursorPosition(currentTime);

    // Update time display
    const timeDisplay = document.getElementById('playerTimeDisplay');
    timeDisplay.textContent = formatTime(currentTime);
    document.getElementById('timelineCurrentTime').textContent = formatTime(currentTime);
}

// Update timeline cursor position
function updateCursorPosition(time) {
    const cursor = document.getElementById('timelineCursor');
    const date = document.getElementById('timelineDate').value;
    const dayStart = new Date(`${date}T00:00:00`);
    const dayEnd = new Date(`${date}T23:59:59`);
    const totalMs = dayEnd - dayStart;

    const offset = time - dayStart;
    const percentage = (offset / totalMs) * 100;

    cursor.style.left = `${Math.max(0, Math.min(100, percentage))}%`;
}

// Handle timeline hover
function handleTimelineHover(event) {
    const timelineTrack = document.getElementById('timelineTrack');
    const hoverIndicator = document.getElementById('timelineHoverIndicator');
    const rect = timelineTrack.getBoundingClientRect();
    const hoverX = event.clientX - rect.left;
    const percentage = hoverX / rect.width;

    const date = document.getElementById('timelineDate').value;
    const dayStart = new Date(`${date}T00:00:00`);
    const hoverTime = new Date(dayStart.getTime() + percentage * 24 * 60 * 60 * 1000);

    // Check if there's a recording at this time
    const segment = findSegmentAtTime(hoverTime);
    const hoverTimeEl = hoverIndicator.querySelector('.hover-time');

    if (segment) {
        hoverTimeEl.textContent = formatTime(hoverTime) + ' (有錄影)';
    } else {
        hoverTimeEl.textContent = formatTime(hoverTime) + ' (無錄影)';
    }

    hoverIndicator.style.left = `${hoverX}px`;
}

// Stop playback
function stopPlayback() {
    const playerInline = document.getElementById('playerInline');
    const videoPlayer = document.getElementById('videoPlayer');
    const cursor = document.getElementById('timelineCursor');

    playerInline.style.display = 'none';
    videoPlayer.pause();
    videoPlayer.src = '';
    videoPlayer.removeEventListener('timeupdate', handleTimeUpdate);

    if (hlsPlayer) {
        hlsPlayer.destroy();
        hlsPlayer = null;
    }

    cursor.classList.remove('active');
    isPlaying = false;
    currentPlaybackTime = null;
}

// Load recordings list
async function loadRecordings() {
    const token = checkAuth();
    if (!token) return;

    const deviceId = document.getElementById('deviceSelect').value;
    const date = document.getElementById('timelineDate').value;
    const recordingsList = document.getElementById('recordingsList');

    if (!deviceId) {
        recordingsList.innerHTML = '<div class="empty-state">請選擇一個裝置來查看錄影</div>';
        return;
    }

    try {
        const startDate = `${date}T00:00:00`;
        const endDate = `${date}T23:59:59`;

        const url = `${API_BASE}/api/devices/${deviceId}/recordings?start_date=${startDate}&end_date=${endDate}&token=${token}`;
        const response = await fetch(url);

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        const recordings = data.recordings || [];

        if (recordings.length === 0) {
            recordingsList.innerHTML = '<div class="empty-state">此日期沒有錄影</div>';
        } else {
            recordingsList.innerHTML = recordings.map(rec => `
                <div class="recording-card" onclick="playRecording('${rec.filename}', '${rec.start_time}')">
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

// Play a specific recording file
async function playRecording(filename, startTime) {
    const time = new Date(startTime);
    await startPlaybackFromTime(time);
}

// Download recording
function downloadRecording(filename) {
    const token = checkAuth();
    if (!token || !currentDeviceId) return;

    const downloadUrl = `${API_BASE}/api/devices/${currentDeviceId}/recordings/${filename}/download?token=${token}`;

    const a = document.createElement('a');
    a.href = downloadUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

// Change date
function changeDate(delta) {
    const dateInput = document.getElementById('timelineDate');
    const currentDate = new Date(dateInput.value);
    currentDate.setDate(currentDate.getDate() + delta);
    dateInput.value = currentDate.toISOString().split('T')[0];
    loadTimeline();
}

// Event listeners
document.addEventListener('DOMContentLoaded', function() {
    checkAuth();
    loadDevices();
    initTimelineHours();

    // Set default date to today
    const dateInput = document.getElementById('timelineDate');
    dateInput.value = getTodayDate();

    // Device selector
    document.getElementById('deviceSelect').addEventListener('change', loadTimeline);

    // Date input
    dateInput.addEventListener('change', loadTimeline);

    // Date navigation buttons
    document.getElementById('prevDayBtn').addEventListener('click', () => changeDate(-1));
    document.getElementById('todayBtn').addEventListener('click', () => {
        dateInput.value = getTodayDate();
        loadTimeline();
    });
    document.getElementById('nextDayBtn').addEventListener('click', () => changeDate(1));

    // Refresh button
    document.getElementById('refreshRecordingsBtn').addEventListener('click', loadTimeline);

    // Timeline click
    document.getElementById('timelineTrack').addEventListener('click', handleTimelineClick);

    // Timeline hover
    document.getElementById('timelineTrack').addEventListener('mousemove', handleTimelineHover);

    // Close player button
    document.getElementById('playerCloseBtn').addEventListener('click', stopPlayback);

    // Logout
    document.getElementById('logoutBtn').addEventListener('click', () => {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        window.location.href = 'login.html';
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            stopPlayback();
        }
    });
});
