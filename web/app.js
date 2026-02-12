/**
 * 水下監視系統 Web Client - Main Application Logic
 */

const API_BASE = window.location.origin.replace(':8080', ':8000');
let currentDevices = [];

// XSS protection helper
function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}

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

    // Also set initial if available
    const initialEl = document.getElementById('username-initial');
    if (initialEl && user.username) initialEl.textContent = user.username[0].toUpperCase();

    return token;
}

// Logout
document.getElementById('logoutBtn').addEventListener('click', () => {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    window.location.href = 'login.html';
});

// Load devices
async function loadDevices() {
    const token = checkAuth();
    if (!token) return;

    const devicesList = document.getElementById('devicesList');

    try {
        const response = await fetch(`${API_BASE}/api/devices?token=${token}`);

        if (response.status === 401) {
            localStorage.removeItem('token');
            window.location.href = 'login.html';
            return;
        }

        const devices = await response.json();
        currentDevices = devices;

        if (devices.length === 0) {
            devicesList.innerHTML = `
                <div class="empty-state" style="grid-column: 1/-1;">
                    <div class="empty-state-icon">
                        <i data-lucide="camera-off" style="width: 36px; height: 36px;"></i>
                    </div>
                    <h3>尚未配對任何裝置</h3>
                    <p>點擊「配對新裝置」來新增您的第一台攝影機。</p>
                </div>
            `;
            lucide.createIcons();
            return;
        }

        devicesList.innerHTML = devices.map(device => `
            <div class="device-card" onclick="startWatching('${escapeHtml(device.device_id)}')" style="${!device.is_online ? 'opacity: 0.6; cursor: not-allowed;' : ''}">
                <div class="device-preview">
                    ${device.is_online ? `
                    <div class="signal-bars">
                        <div class="bar"></div><div class="bar"></div><div class="bar"></div><div class="bar"></div>
                    </div>
                    <span>預覽畫面</span>
                    ` : '<span>離線</span>'}
                </div>
                <div class="device-info">
                    <div class="device-header">
                        <span class="device-name">${escapeHtml(device.device_name || device.device_id)}</span>
                        <div class="device-status ${device.is_online ? 'online' : ''}"></div>
                    </div>
                    <div class="device-meta">
                        ID: ${escapeHtml(device.device_id)} • ${escapeHtml(device.device_type)}
                    </div>
                </div>
            </div>
        `).join('');

    } catch (error) {
        console.error('Error loading devices:', error);
        devicesList.innerHTML = '<p class="error">載入裝置失敗，請稍後再試。</p>';
    }
}

// Refresh devices
document.getElementById('refreshBtn').addEventListener('click', loadDevices);

// Pairing modal
const pairingModal = document.getElementById('pairingModal');
const pairBtn = document.getElementById('pairBtn');
const closeModal = document.querySelector('.close-modal');

pairBtn.addEventListener('click', () => {
    pairingModal.classList.add('active');
    const input = document.getElementById('pairingCodeInput');
    input.value = '';
    input.focus();
    document.getElementById('pairingError').textContent = '';
});

closeModal.addEventListener('click', () => {
    pairingModal.classList.remove('active');
});

window.addEventListener('click', (e) => {
    if (e.target === pairingModal) {
        pairingModal.classList.remove('active');
    }
});

// Submit pairing code
document.getElementById('submitPairingBtn').addEventListener('click', async () => {
    const token = checkAuth();
    if (!token) return;

    const code = document.getElementById('pairingCodeInput').value;
    const errorEl = document.getElementById('pairingError');

    if (code.length !== 6) {
        errorEl.textContent = '請輸入 6 位數代碼';
        return;
    }

    // Show loading state on button
    const submitBtn = document.getElementById('submitPairingBtn');
    const originalText = submitBtn.textContent;
    submitBtn.textContent = '連線中...';
    submitBtn.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/api/devices/pair?token=${token}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                pairing_code: code
            })
        });

        const data = await response.json();

        if (response.ok) {
            pairingModal.classList.remove('active');
            await loadDevices();
            // alert(`配對成功: ${data.device.device_name || data.device.device_id}`);
        } else {
            errorEl.textContent = data.detail || '配對失敗';
        }
    } catch (error) {
        console.error('Pairing error:', error);
        errorEl.textContent = '網路錯誤，請重試。';
    } finally {
        submitBtn.textContent = originalText;
        submitBtn.disabled = false;
    }
});

// Start watching device
async function startWatching(deviceId) {
    console.log('Starting watch for device:', deviceId);

    const device = currentDevices.find(d => d.device_id === deviceId);
    if (!device) {
        return;
    }

    if (!device.is_online) {
        return;
    }

    document.getElementById('watchingDeviceName').textContent = device.device_name || device.device_id; // textContent is XSS-safe
    document.getElementById('watchSection').classList.add('active');

    // Initialize WebRTC (see webrtc.js)
    await initializeWebRTC(deviceId);
}

// End watching
document.getElementById('endWatchBtn').addEventListener('click', () => {
    if (window.endWatching) window.endWatching();
    document.getElementById('watchSection').classList.remove('active');
});

// Initialize on load
checkAuth();
loadDevices();

// Auto-refresh devices every 30 seconds
// Auto-refresh devices every 30 seconds
setInterval(loadDevices, 30000);

// Mobile Sidebar Toggle
const widthSidebarBtn = document.getElementById('widthSidebarBtn');
const sidebar = document.querySelector('.sidebar');
const sidebarOverlay = document.getElementById('sidebarOverlay');

if (widthSidebarBtn && sidebar && sidebarOverlay) {
    widthSidebarBtn.addEventListener('click', () => {
        sidebar.classList.add('active');
        sidebarOverlay.classList.add('active');
    });

    function closeSidebar() {
        sidebar.classList.remove('active');
        sidebarOverlay.classList.remove('active');
    }

    sidebarOverlay.addEventListener('click', closeSidebar);

    // Close sidebar when clicking a nav item on mobile
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        item.addEventListener('click', () => {
            if (window.innerWidth <= 768) {
                closeSidebar();
            }
        });
    });
}
