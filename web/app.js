/**
 * MuMu Camera Web Client - Main Application Logic
 */

const API_BASE = window.location.origin.replace(':8080', ':8000');
let currentDevices = [];

// Check authentication
function checkAuth() {
    const token = localStorage.getItem('token');
    const user = JSON.parse(localStorage.getItem('user') || 'null');

    if (!token || !user) {
        window.location.href = 'login.html';
        return null;
    }

    document.getElementById('username').textContent = user.username;
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
    devicesList.innerHTML = '<p class="loading">Loading devices...</p>';

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
                <div class="empty-state">
                    <p>No devices paired yet.</p>
                    <p>Click "Pair Device" to add your first camera.</p>
                </div>
            `;
            return;
        }

        devicesList.innerHTML = devices.map(device => `
            <div class="device-card ${device.is_online ? 'online' : 'offline'}">
                <div class="device-header">
                    <h3>${device.device_name || device.device_id}</h3>
                    <span class="status-badge">${device.is_online ? 'Online' : 'Offline'}</span>
                </div>
                <div class="device-info">
                    <p><strong>ID:</strong> ${device.device_id}</p>
                    <p><strong>Type:</strong> ${device.device_type}</p>
                    <p><strong>Last Seen:</strong> ${device.last_seen ? new Date(device.last_seen).toLocaleString() : 'Never'}</p>
                </div>
                <div class="device-actions">
                    <button
                        class="btn btn-primary"
                        onclick="startWatching('${device.device_id}')"
                        ${!device.is_online ? 'disabled' : ''}
                    >
                        ${device.is_online ? 'Watch' : 'Offline'}
                    </button>
                </div>
            </div>
        `).join('');

    } catch (error) {
        console.error('Error loading devices:', error);
        devicesList.innerHTML = '<p class="error">Failed to load devices. Please try again.</p>';
    }
}

// Refresh devices
document.getElementById('refreshBtn').addEventListener('click', loadDevices);

// Pairing modal
const pairingModal = document.getElementById('pairingModal');
const pairBtn = document.getElementById('pairBtn');
const closeModal = document.querySelector('.close');

pairBtn.addEventListener('click', () => {
    pairingModal.style.display = 'block';
    document.getElementById('pairingCodeInput').value = '';
    document.getElementById('pairingError').textContent = '';
});

closeModal.addEventListener('click', () => {
    pairingModal.style.display = 'none';
});

window.addEventListener('click', (e) => {
    if (e.target === pairingModal) {
        pairingModal.style.display = 'none';
    }
});

// Submit pairing code
document.getElementById('submitPairingBtn').addEventListener('click', async () => {
    const token = checkAuth();
    if (!token) return;

    const code = document.getElementById('pairingCodeInput').value;
    const errorEl = document.getElementById('pairingError');

    if (code.length !== 6) {
        errorEl.textContent = 'Please enter a 6-digit code';
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/devices/pair`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                pairing_code: code,
                token: token
            })
        });

        const data = await response.json();

        if (response.ok) {
            pairingModal.style.display = 'none';
            await loadDevices();
            alert(`Device paired successfully: ${data.device.device_name || data.device.device_id}`);
        } else {
            errorEl.textContent = data.detail || 'Pairing failed';
        }
    } catch (error) {
        console.error('Pairing error:', error);
        errorEl.textContent = 'Network error. Please try again.';
    }
});

// Start watching device
async function startWatching(deviceId) {
    console.log('Starting watch for device:', deviceId);

    const device = currentDevices.find(d => d.device_id === deviceId);
    if (!device) {
        alert('Device not found');
        return;
    }

    document.getElementById('watchingDeviceName').textContent = device.device_name || device.device_id;
    document.getElementById('watchSection').style.display = 'block';

    // Initialize WebRTC (see webrtc.js)
    await initializeWebRTC(deviceId);
}

// End watching
document.getElementById('endWatchBtn').addEventListener('click', () => {
    endWatching();
    document.getElementById('watchSection').style.display = 'none';
});

// Initialize on load
checkAuth();
loadDevices();

// Auto-refresh devices every 30 seconds
setInterval(loadDevices, 30000);
