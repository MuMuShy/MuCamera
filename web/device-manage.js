/**
 * Device Management - System Info & Remote Control
 */

(function () {
    'use strict';

    const API_BASE = window.location.origin;
    let token = localStorage.getItem('token');
    let currentDeviceId = null;
    let refreshTimer = null;
    const REFRESH_INTERVAL = 5000; // 5 seconds

    // -------- Auth check --------
    if (!token) {
        window.location.href = 'login.html';
        return;
    }

    // -------- User info --------
    const userRaw = localStorage.getItem('user');
    if (userRaw) {
        try {
            const user = JSON.parse(userRaw);
            const nameEl = document.getElementById('username');
            const initialEl = document.getElementById('username-initial');
            if (nameEl) nameEl.textContent = user.username || 'User';
            if (initialEl) initialEl.textContent = (user.username || 'U')[0].toUpperCase();
        } catch (e) { }
    }

    // -------- Logout --------
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => {
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            window.location.href = 'login.html';
        });
    }

    // -------- Sidebar toggle (mobile) --------
    const sidebar = document.querySelector('.sidebar');
    const sidebarOverlay = document.getElementById('sidebarOverlay');
    const hamburgerBtn = document.getElementById('widthSidebarBtn');

    function toggleSidebar() {
        sidebar.classList.toggle('active');
        sidebarOverlay.classList.toggle('active');
    }

    if (hamburgerBtn) hamburgerBtn.addEventListener('click', toggleSidebar);
    if (sidebarOverlay) sidebarOverlay.addEventListener('click', toggleSidebar);

    // -------- Load devices --------
    const deviceSelect = document.getElementById('deviceSelect');

    async function loadDevices() {
        try {
            const res = await fetch(`${API_BASE}/api/devices?token=${token}`);
            if (res.status === 401) {
                window.location.href = 'login.html';
                return;
            }
            const devices = await res.json();
            deviceSelect.innerHTML = '<option value="">選擇裝置</option>';
            devices.forEach(d => {
                const opt = document.createElement('option');
                opt.value = d.device_id;
                opt.textContent = d.device_name || d.device_id;
                if (d.is_online) opt.textContent += ' (線上)';
                deviceSelect.appendChild(opt);
            });

            // Restore previous selection
            const saved = localStorage.getItem('manage_device_id');
            if (saved && deviceSelect.querySelector(`option[value="${saved}"]`)) {
                deviceSelect.value = saved;
                selectDevice(saved);
            }
        } catch (e) {
            console.error('Failed to load devices:', e);
        }
    }

    deviceSelect.addEventListener('change', () => {
        const id = deviceSelect.value;
        if (id) {
            localStorage.setItem('manage_device_id', id);
            selectDevice(id);
        } else {
            currentDeviceId = null;
            stopAutoRefresh();
            document.getElementById('noDeviceState').style.display = '';
            document.getElementById('sysinfoContent').style.display = 'none';
            updateStatusBadge(false);
        }
    });

    function selectDevice(deviceId) {
        currentDeviceId = deviceId;
        document.getElementById('noDeviceState').style.display = 'none';
        document.getElementById('sysinfoContent').style.display = '';
        fetchSysinfo();
        startAutoRefresh();
    }

    // -------- Fetch system info --------
    async function fetchSysinfo() {
        if (!currentDeviceId) return;
        try {
            const res = await fetch(`${API_BASE}/api/devices/${currentDeviceId}/sysinfo?token=${token}`);
            if (res.status === 401) {
                window.location.href = 'login.html';
                return;
            }
            const data = await res.json();
            if (data.error) {
                updateStatusBadge(data.online || false);
                return;
            }
            updateStatusBadge(true);
            renderSysinfo(data);
        } catch (e) {
            console.error('Failed to fetch sysinfo:', e);
        }
    }

    function updateStatusBadge(online) {
        const badge = document.getElementById('deviceStatus');
        const text = document.getElementById('deviceStatusText');
        if (online) {
            badge.classList.add('online');
            text.textContent = '線上';
        } else {
            badge.classList.remove('online');
            text.textContent = currentDeviceId ? '離線' : '未選擇';
        }
    }

    // -------- Render sysinfo --------
    function renderSysinfo(data) {
        // CPU
        const cpu = data.cpu || {};
        const cpuPct = cpu.usage_percent != null ? cpu.usage_percent : '--';
        document.getElementById('cpuUsage').textContent = cpuPct !== '--' ? cpuPct + '%' : '--%';
        document.getElementById('cpuCores').textContent = cpu.cores ? `${cpu.cores} 核心` : '';
        setProgressBar('cpuBar', cpuPct);
        document.getElementById('cpuLoad1').textContent = cpu.load_1m != null ? cpu.load_1m.toFixed(2) : '--';
        document.getElementById('cpuLoad5').textContent = cpu.load_5m != null ? cpu.load_5m.toFixed(2) : '--';
        document.getElementById('cpuLoad15').textContent = cpu.load_15m != null ? cpu.load_15m.toFixed(2) : '--';

        // Memory
        const mem = data.memory || {};
        const memPct = mem.usage_percent != null ? mem.usage_percent : '--';
        document.getElementById('memUsage').textContent = memPct !== '--' ? memPct + '%' : '--%';
        if (mem.used_mb != null && mem.total_mb != null) {
            document.getElementById('memDetail').textContent = `${mem.used_mb} MB / ${mem.total_mb} MB`;
        } else {
            document.getElementById('memDetail').textContent = '';
        }
        setProgressBar('memBar', memPct);

        // Disk
        const disk = data.disk || {};
        const diskPct = disk.usage_percent != null ? disk.usage_percent : '--';
        document.getElementById('diskUsage').textContent = diskPct !== '--' ? diskPct + '%' : '--%';
        if (disk.used_gb != null && disk.total_gb != null) {
            document.getElementById('diskDetail').textContent = `${disk.used_gb} GB / ${disk.total_gb} GB (剩餘 ${disk.free_gb} GB)`;
        } else {
            document.getElementById('diskDetail').textContent = '';
        }
        setProgressBar('diskBar', diskPct);

        // Network
        const net = data.network || {};
        const interfaces = net.interfaces || {};
        const netContainer = document.getElementById('netInterfaces');
        if (Object.keys(interfaces).length === 0) {
            netContainer.innerHTML = '<div class="sysinfo-row"><span class="sysinfo-row-label">無網路介面</span><span class="sysinfo-row-value">--</span></div>';
        } else {
            let html = '';
            for (const [iface, info] of Object.entries(interfaces)) {
                const rxRate = info.rx_rate_kbps != null ? info.rx_rate_kbps.toFixed(1) + ' KB/s' : '--';
                const txRate = info.tx_rate_kbps != null ? info.tx_rate_kbps.toFixed(1) + ' KB/s' : '--';
                const rxTotal = formatBytes(info.rx_bytes);
                const txTotal = formatBytes(info.tx_bytes);
                html += `
                    <div class="sysinfo-row">
                        <span class="sysinfo-row-label">${iface}</span>
                        <span class="sysinfo-row-value" style="font-size: 0.8rem;">&darr;${rxRate} &uarr;${txRate}</span>
                    </div>
                    <div class="sysinfo-row">
                        <span class="sysinfo-row-label" style="padding-left: 12px; font-size: 0.75rem;">累計</span>
                        <span class="sysinfo-row-value" style="font-size: 0.75rem;">&darr;${rxTotal} &uarr;${txTotal}</span>
                    </div>`;
            }
            netContainer.innerHTML = html;
        }

        // System info
        const sys = data.system || {};
        const sysContainer = document.getElementById('sysRows');
        let sysHtml = '';
        if (sys.hostname) sysHtml += row('主機名稱', sys.hostname);
        if (sys.model) sysHtml += row('型號', sys.model);
        if (sys.os_name) sysHtml += row('作業系統', sys.os_name);
        if (sys.kernel) sysHtml += row('核心', sys.kernel);
        if (sys.uptime_seconds != null) sysHtml += row('已開機', formatUptime(sys.uptime_seconds));
        sysContainer.innerHTML = sysHtml || row('無資料', '--');

        // go2rtc + temperature
        const temp = data.temperature || {};
        document.getElementById('go2rtcStatus').textContent = data.go2rtc_healthy ? '正常' : '異常';
        document.getElementById('go2rtcStatus').style.color = data.go2rtc_healthy ? 'var(--success-color)' : 'var(--danger-color)';
        document.getElementById('cpuTemp').textContent = temp.cpu_temp != null ? temp.cpu_temp + ' °C' : '--';
    }

    function row(label, value) {
        return `<div class="sysinfo-row"><span class="sysinfo-row-label">${label}</span><span class="sysinfo-row-value">${value}</span></div>`;
    }

    function setProgressBar(id, pct) {
        const bar = document.getElementById(id);
        if (!bar) return;
        const val = typeof pct === 'number' ? pct : 0;
        bar.style.width = val + '%';
        bar.className = 'progress-bar-fill';
        if (val >= 90) bar.classList.add('danger');
        else if (val >= 70) bar.classList.add('warning');
    }

    function formatBytes(bytes) {
        if (bytes == null) return '--';
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
    }

    function formatUptime(seconds) {
        const d = Math.floor(seconds / 86400);
        const h = Math.floor((seconds % 86400) / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        let parts = [];
        if (d > 0) parts.push(d + ' 天');
        if (h > 0) parts.push(h + ' 時');
        parts.push(m + ' 分');
        return parts.join(' ');
    }

    // -------- Auto refresh --------
    function startAutoRefresh() {
        stopAutoRefresh();
        refreshTimer = setInterval(fetchSysinfo, REFRESH_INTERVAL);
    }

    function stopAutoRefresh() {
        if (refreshTimer) {
            clearInterval(refreshTimer);
            refreshTimer = null;
        }
    }

    document.getElementById('refreshBtn').addEventListener('click', fetchSysinfo);

    // -------- Control commands --------
    const COMMAND_LABELS = {
        restart_agent: { title: '重啟 Agent', msg: '確定要重啟 Agent？Agent 會短暫斷線後重新連線。' },
        restart_go2rtc: { title: '重啟 go2rtc', msg: '確定要重啟 go2rtc？串流將會暫時中斷。' },
        restart_stream: { title: '重啟串流', msg: '確定要重啟串流？當前觀看的用戶會暫時斷線。' },
        reboot: { title: '重新開機', msg: '確定要重新啟動樹莓派？裝置將會離線數分鐘。' }
    };

    let pendingCommand = null;

    document.querySelectorAll('.control-buttons .btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const cmd = btn.dataset.command;
            if (!cmd || !currentDeviceId) return;
            showConfirmDialog(cmd);
        });
    });

    function showConfirmDialog(command) {
        const info = COMMAND_LABELS[command];
        if (!info) return;
        pendingCommand = command;
        document.getElementById('confirmTitle').textContent = info.title;
        document.getElementById('confirmMsg').textContent = info.msg;
        document.getElementById('confirmDialog').classList.add('active');

        const okBtn = document.getElementById('confirmOk');
        if (command === 'reboot') {
            okBtn.className = 'btn btn-danger';
            okBtn.textContent = '確認重開機';
        } else {
            okBtn.className = 'btn btn-primary';
            okBtn.textContent = '確認';
        }
    }

    document.getElementById('confirmCancel').addEventListener('click', () => {
        pendingCommand = null;
        document.getElementById('confirmDialog').classList.remove('active');
    });

    document.getElementById('confirmOk').addEventListener('click', async () => {
        const cmd = pendingCommand;
        pendingCommand = null;
        document.getElementById('confirmDialog').classList.remove('active');
        if (!cmd || !currentDeviceId) return;
        await executeCommand(cmd);
    });

    async function executeCommand(command) {
        try {
            const res = await fetch(`${API_BASE}/api/devices/${currentDeviceId}/control?token=${token}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command })
            });

            if (res.status === 401) {
                window.location.href = 'login.html';
                return;
            }

            const data = await res.json();

            if (res.ok) {
                showToast(data.message || '指令已發送', 'success');
            } else {
                showToast(data.detail || '指令失敗', 'error');
            }
        } catch (e) {
            console.error('Control command error:', e);
            showToast('無法連線伺服器', 'error');
        }
    }

    // -------- Toast notification --------
    function showToast(message, type) {
        const existing = document.querySelector('.toast-notification');
        if (existing) existing.remove();

        const toast = document.createElement('div');
        toast.className = 'toast-notification';
        toast.style.cssText = `
            position: fixed;
            bottom: 24px;
            right: 24px;
            padding: 14px 24px;
            border-radius: 12px;
            font-size: 0.9rem;
            font-weight: 500;
            z-index: 2000;
            animation: modal-appear 0.3s ease;
            color: white;
            box-shadow: 0 4px 20px rgba(0,0,0,0.4);
            background: ${type === 'error' ? 'var(--danger-color)' : 'var(--success-color)'};
        `;
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 4000);
    }

    // -------- Init --------
    loadDevices();
})();
