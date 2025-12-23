# MuMu Camera Device Agent - 部署指南

完整的 Raspberry Pi 部署指南，包含 go2rtc + Cloudflare Tunnel 配置。

## 目录

- [準備工作](#準備工作)
- [方案一：直接連線（無 Cloudflare）](#方案一直接連線無-cloudflare)
- [方案二：Cloudflare Tunnel（推薦）](#方案二cloudflare-tunnel推薦)
- [安裝 go2rtc](#安裝-go2rtc)
- [安裝 Device Agent](#安裝-device-agent)
- [設定開機自動啟動](#設定開機自動啟動)
- [監控與維護](#監控與維護)
- [故障排除](#故障排除)

---

## 準備工作

### 1. 硬體需求

- Raspberry Pi 3/4/5 或其他 Linux 裝置
- IP Camera（支援 RTSP）或 Pi Camera Module
- 穩定的網路連線（Wi-Fi 或有線）

### 2. 軟體需求

```bash
# 更新系統
sudo apt update && sudo apt upgrade -y

# 安裝必要套件
sudo apt install -y \
  python3 \
  python3-pip \
  python3-venv \
  git \
  curl \
  wget
```

### 3. 取得 Backend URL

確認你的 backend server URL：
- **本地測試**：`ws://localhost:8000/ws/device`
- **直接連線**：`ws://your-server-ip:8000/ws/device` 或 `wss://your-domain.com/ws/device`
- **Cloudflare Tunnel**：`wss://your-tunnel.your-domain.com/ws/device`

---

## 方案一：直接連線（無 Cloudflare）

適合：本地測試、內網環境、已有固定 IP

### 1. Backend Server 設定

確保 backend server 的 8000 port 對外開放：

```bash
# 檢查防火牆
sudo ufw allow 8000/tcp

# 或使用 firewalld
sudo firewall-cmd --permanent --add-port=8000/tcp
sudo firewall-cmd --reload
```

### 2. Raspberry Pi 設定

```bash
# 設定環境變數
export DEVICE_ID="pi-cam-001"
export BACKEND_URL="ws://YOUR_SERVER_IP:8000/ws/device"
export GO2RTC_HTTP="http://127.0.0.1:1984"

# 或使用 wss（需要 SSL 憑證）
export BACKEND_URL="wss://your-domain.com/ws/device"
```

跳到 [安裝 go2rtc](#安裝-go2rtc) 繼續。

---

## 方案二：Cloudflare Tunnel（推薦）

適合：沒有固定 IP、需要安全連線、多地點部署

### 優點

✅ 免費（不需 public IP）
✅ 自動 SSL/TLS 加密
✅ 防 DDoS 保護
✅ 支援多個 tunnel（可部署多台 Pi）
✅ 穿透 NAT/防火牆

### 架構圖

```
Raspberry Pi                    Cloudflare Edge              Your Server
┌─────────────────┐            ┌──────────────┐            ┌──────────────┐
│  Device Agent   │───tunnel───│  Cloudflare  │───tunnel───│   Backend    │
│  (WebSocket)    │            │              │            │   (FastAPI)  │
└─────────────────┘            └──────────────┘            └──────────────┘
         │                                                           │
         │                                                           │
    ┌────▼────┐                                                ┌────▼────┐
    │ go2rtc  │                                                │  Redis  │
    │ (RTSP→  │                                                │  Postgres│
    │ WebRTC) │                                                └─────────┘
    └─────────┘
```

### 步驟 1：Backend Server 設定 Cloudflare Tunnel

#### 1.1 安裝 cloudflared（在 Backend Server）

```bash
# Linux (Ubuntu/Debian)
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

# 或使用 apt
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-archive-keyring.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-archive-keyring.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared
```

#### 1.2 登入 Cloudflare

```bash
cloudflared tunnel login
```

這會開啟瀏覽器，選擇要使用的 domain。

#### 1.3 建立 Tunnel（Backend）

```bash
# 建立 tunnel（名稱：mumucam-backend）
cloudflared tunnel create mumucam-backend

# 記下 Tunnel ID（會顯示在輸出中）
# 例如：Created tunnel mumucam-backend with id xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

#### 1.4 設定 DNS

```bash
# 設定子網域指向 tunnel
cloudflared tunnel route dns mumucam-backend backend.your-domain.com
```

#### 1.5 建立 Backend Tunnel 配置檔

```bash
sudo mkdir -p /etc/cloudflared
sudo nano /etc/cloudflared/config.yml
```

填入以下內容：

```yaml
tunnel: mumucam-backend
credentials-file: /root/.cloudflared/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.json

ingress:
  # WebSocket 路徑（Device Agent 連線）
  - hostname: backend.your-domain.com
    path: /ws/device
    service: ws://localhost:8000
    originRequest:
      noTLSVerify: true
      connectTimeout: 30s
      http2Origin: false

  # WebSocket 路徑（Web Viewer 連線）
  - hostname: backend.your-domain.com
    path: /ws/viewer
    service: ws://localhost:8000
    originRequest:
      noTLSVerify: true
      connectTimeout: 30s
      http2Origin: false

  # HTTP API
  - hostname: backend.your-domain.com
    service: http://localhost:8000
    originRequest:
      noTLSVerify: true
      connectTimeout: 30s

  # Catch-all（必須）
  - service: http_status:404
```

#### 1.6 啟動 Backend Tunnel

```bash
# 測試配置
sudo cloudflared tunnel --config /etc/cloudflared/config.yml run

# 安裝為 service（開機自動啟動）
sudo cloudflared service install
sudo systemctl start cloudflared
sudo systemctl enable cloudflared

# 檢查狀態
sudo systemctl status cloudflared
```

現在 backend 已可透過 `wss://backend.your-domain.com/ws/device` 訪問！

---

### 步驟 2：Raspberry Pi 設定環境變數

```bash
# 編輯環境變數檔
sudo nano /etc/mumucam/agent.env
```

填入：

```bash
# 裝置 ID（每台 Pi 要不同）
DEVICE_ID=pi-cam-001

# Backend URL（使用 Cloudflare Tunnel）
BACKEND_URL=wss://backend.your-domain.com/ws/device

# go2rtc 本地 URL
GO2RTC_HTTP=http://127.0.0.1:1984

# 裝置密鑰（可選）
DEVICE_SECRET=your-secret-key-here
```

儲存後：

```bash
sudo chmod 600 /etc/mumucam/agent.env
```

---

## 安裝 go2rtc

### 方法 1：使用預編譯執行檔（推薦）

```bash
# 下載 go2rtc
cd /opt
sudo wget -O go2rtc https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_arm64
sudo chmod +x go2rtc

# 建立設定檔
sudo mkdir -p /etc/go2rtc
sudo nano /etc/go2rtc/go2rtc.yaml
```

設定檔範例：

```yaml
api:
  listen: "127.0.0.1:1984"

streams:
  # RTSP 攝影機範例
  cam:
    - rtsp://admin:password@192.168.1.100:554/stream1
    # 如果需要多個來源（備援）
    # - rtsp://admin:password@192.168.1.101:554/stream1

  # Raspberry Pi Camera Module 範例
  # picam:
  #   - ffmpeg:device?video=/dev/video0&input_format=h264&video_size=1920x1080

log:
  level: info
```

### 方法 2：使用 Docker

```bash
# 建立 docker-compose.yml
mkdir -p ~/go2rtc
cd ~/go2rtc
nano docker-compose.yml
```

```yaml
version: '3.8'
services:
  go2rtc:
    image: alexxit/go2rtc
    network_mode: host
    restart: unless-stopped
    volumes:
      - ./go2rtc.yaml:/config/go2rtc.yaml
```

啟動：

```bash
docker-compose up -d
```

### 測試 go2rtc

```bash
# 檢查 API
curl http://127.0.0.1:1984/api/streams

# 應該回傳 JSON（即使沒有 stream 也會回傳 {}）
```

### 設定 go2rtc 開機自動啟動（方法 1）

```bash
sudo nano /etc/systemd/system/go2rtc.service
```

```ini
[Unit]
Description=go2rtc Stream Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt
ExecStart=/opt/go2rtc -c /etc/go2rtc/go2rtc.yaml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

啟動：

```bash
sudo systemctl daemon-reload
sudo systemctl start go2rtc
sudo systemctl enable go2rtc
sudo systemctl status go2rtc
```

---

## 安裝 Device Agent

### 方法 1：Python venv（推薦）

```bash
# 建立專案目錄
sudo mkdir -p /opt/mumucam
cd /opt/mumucam

# 下載 agent.py 和 requirements.txt
sudo wget https://raw.githubusercontent.com/YOUR_REPO/device-agent/agent.py
sudo wget https://raw.githubusercontent.com/YOUR_REPO/device-agent/requirements.txt

# 或從本地複製
# sudo cp /path/to/agent.py /opt/mumucam/
# sudo cp /path/to/requirements.txt /opt/mumucam/

# 建立 Python 虛擬環境
sudo python3 -m venv venv
source venv/bin/activate

# 安裝依賴
sudo venv/bin/pip install --upgrade pip
sudo venv/bin/pip install -r requirements.txt
```

### 方法 2：Docker

```bash
# 建立 Dockerfile（使用專案中的 Dockerfile）
cd /opt/mumucam
sudo docker build -t mumucam-agent .

# 或直接執行
sudo docker run -d \
  --name mumucam-agent \
  --network host \
  --env-file /etc/mumucam/agent.env \
  --restart unless-stopped \
  mumucam-agent
```

### 測試 Agent

```bash
# 方法 1（venv）
sudo /opt/mumucam/venv/bin/python /opt/mumucam/agent.py --verbose

# 方法 2（Docker）
sudo docker logs -f mumucam-agent
```

應該看到：

```
2024-xx-xx xx:xx:xx - __main__ - INFO - === MuMu Camera Device Agent (go2rtc mode) ===
2024-xx-xx xx:xx:xx - __main__ - INFO - Device ID: pi-cam-001
2024-xx-xx xx:xx:xx - __main__ - INFO - [go2rtc] ✓ Service is now healthy
2024-xx-xx xx:xx:xx - __main__ - INFO - ✓ Device registered: Device already registered
2024-xx-xx xx:xx:xx - __main__ - INFO - [ws] Connecting to wss://backend.your-domain.com/ws/device...
2024-xx-xx xx:xx:xx - __main__ - INFO - [ws] ✓ Connected as device: pi-cam-001
2024-xx-xx xx:xx:xx - __main__ - INFO - [ws] ✓ Server acknowledged connection
```

---

## 設定開機自動啟動

### systemd Service（推薦）

```bash
sudo nano /etc/systemd/system/mumucam-agent.service
```

```ini
[Unit]
Description=MuMu Camera Device Agent
After=network-online.target go2rtc.service
Wants=network-online.target
Requires=go2rtc.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/mumucam
EnvironmentFile=/etc/mumucam/agent.env
ExecStart=/opt/mumucam/venv/bin/python /opt/mumucam/agent.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Graceful shutdown
TimeoutStopSec=30
KillMode=mixed

[Install]
WantedBy=multi-user.target
```

啟動：

```bash
sudo systemctl daemon-reload
sudo systemctl start mumucam-agent
sudo systemctl enable mumucam-agent
sudo systemctl status mumucam-agent
```

---

## 監控與維護

### 查看 Logs

```bash
# Agent logs
sudo journalctl -u mumucam-agent -f

# go2rtc logs
sudo journalctl -u go2rtc -f

# 只看最近 100 行
sudo journalctl -u mumucam-agent -n 100

# 查看特定時間
sudo journalctl -u mumucam-agent --since "2024-01-01 10:00:00"
```

### 重啟服務

```bash
# 重啟 agent
sudo systemctl restart mumucam-agent

# 重啟 go2rtc
sudo systemctl restart go2rtc

# 重啟全部
sudo systemctl restart mumucam-agent go2rtc
```

### 檢查連線狀態

```bash
# 檢查 agent 是否運行
sudo systemctl status mumucam-agent

# 檢查 go2rtc
curl http://127.0.0.1:1984/api/streams

# 檢查網路連線
ping backend.your-domain.com

# 檢查 WebSocket（需要 wscat）
npm install -g wscat
wscat -c wss://backend.your-domain.com/ws/device
```

### 監控資源使用

```bash
# CPU/Memory 使用率
top -p $(pidof python)

# 或使用 htop
sudo apt install htop
htop

# 網路使用
sudo apt install nethogs
sudo nethogs
```

---

## 多台 Raspberry Pi 部署

### 1. 為每台設備設定不同 DEVICE_ID

```bash
# Pi 1
DEVICE_ID=pi-cam-livingroom

# Pi 2
DEVICE_ID=pi-cam-kitchen

# Pi 3
DEVICE_ID=pi-cam-garage
```

### 2. 所有 Pi 都連到同一個 Backend Tunnel

```bash
BACKEND_URL=wss://backend.your-domain.com/ws/device
```

### 3. 快速部署腳本

建立 `deploy.sh`：

```bash
#!/bin/bash
set -e

DEVICE_ID=$1
if [ -z "$DEVICE_ID" ]; then
  echo "Usage: $0 <device-id>"
  exit 1
fi

echo "部署 MuMu Camera Agent (Device ID: $DEVICE_ID)"

# 建立目錄
sudo mkdir -p /opt/mumucam /etc/mumucam /etc/go2rtc

# 下載檔案
cd /opt/mumucam
sudo wget -O agent.py https://raw.githubusercontent.com/YOUR_REPO/device-agent/agent.py
sudo wget -O requirements.txt https://raw.githubusercontent.com/YOUR_REPO/device-agent/requirements.txt

# 建立環境檔
sudo tee /etc/mumucam/agent.env > /dev/null <<EOF
DEVICE_ID=$DEVICE_ID
BACKEND_URL=wss://backend.your-domain.com/ws/device
GO2RTC_HTTP=http://127.0.0.1:1984
EOF

# 安裝 Python 依賴
sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt

# 下載 go2rtc
cd /opt
sudo wget -O go2rtc https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_arm64
sudo chmod +x go2rtc

# 安裝 systemd services
# （參考上面的 .service 檔案）

echo "部署完成！執行以下命令啟動："
echo "  sudo systemctl start go2rtc mumucam-agent"
echo "  sudo systemctl enable go2rtc mumucam-agent"
```

使用：

```bash
chmod +x deploy.sh
sudo ./deploy.sh pi-cam-001
```

---

## 故障排除

### 問題 1：Agent 無法連線到 Backend

**症狀**：
```
[ws] Connection error: [Errno 111] Connection refused
```

**解決方法**：

1. 檢查 Backend URL 是否正確：
```bash
echo $BACKEND_URL
ping backend.your-domain.com
```

2. 檢查 Cloudflare Tunnel 是否運行：
```bash
sudo systemctl status cloudflared
sudo journalctl -u cloudflared -f
```

3. 測試 WebSocket 連線：
```bash
wscat -c wss://backend.your-domain.com/ws/device
```

### 問題 2：go2rtc 無法啟動

**症狀**：
```
[go2rtc] Service is unhealthy
```

**解決方法**：

1. 檢查 go2rtc 是否運行：
```bash
sudo systemctl status go2rtc
curl http://127.0.0.1:1984/api/streams
```

2. 檢查配置檔：
```bash
sudo cat /etc/go2rtc/go2rtc.yaml
```

3. 測試 RTSP 來源：
```bash
ffprobe rtsp://admin:password@192.168.1.100:554/stream1
```

### 問題 3：重新連線循環

**症狀**：
```
[ws] ⟳ Reconnecting in 30.0s (attempt 10)
```

**解決方法**：

1. 檢查網路狀態：
```bash
ping -c 5 8.8.8.8
ping -c 5 backend.your-domain.com
```

2. 檢查 Backend 是否正常：
```bash
curl https://backend.your-domain.com/health
```

3. 查看詳細日誌：
```bash
sudo /opt/mumucam/venv/bin/python /opt/mumucam/agent.py --verbose
```

### 問題 4：Cloudflare Tunnel 斷線

**症狀**：
```
cloudflared: connection refused
```

**解決方法**：

1. 重啟 cloudflared：
```bash
sudo systemctl restart cloudflared
```

2. 檢查 Cloudflare Dashboard：
   - 前往 https://dash.cloudflare.com
   - Zero Trust → Tunnels
   - 確認 tunnel 狀態為 "HEALTHY"

3. 重新建立 tunnel（如果損壞）：
```bash
cloudflared tunnel delete mumucam-backend
cloudflared tunnel create mumucam-backend
# 更新 /etc/cloudflared/config.yml 的 credentials-file
sudo systemctl restart cloudflared
```

---

## 進階設定

### 設定多個 Stream

編輯 `/etc/go2rtc/go2rtc.yaml`：

```yaml
streams:
  front_door:
    - rtsp://admin:pass@192.168.1.100:554/stream1

  backyard:
    - rtsp://admin:pass@192.168.1.101:554/stream1

  garage:
    - ffmpeg:device?video=/dev/video0&input_format=h264
```

### 效能調整

編輯 `/etc/mumucam/agent.env`：

```bash
# 降低 capabilities 回報頻率（減少網路流量）
# 修改 agent.py 中的 capabilities_interval = 60

# 增加 heartbeat 間隔
# 修改 agent.py 中的 heartbeat_interval = 30
```

### 安全性強化

```bash
# 使用 DEVICE_SECRET
DEVICE_SECRET=$(openssl rand -hex 32)
echo "DEVICE_SECRET=$DEVICE_SECRET" | sudo tee -a /etc/mumucam/agent.env

# 限制檔案權限
sudo chmod 600 /etc/mumucam/agent.env
sudo chown root:root /etc/mumucam/agent.env
```

---

## 完整檢查清單

部署前確認：

- [ ] Backend Server 已部署並運行
- [ ] Cloudflare Tunnel 已設定並測試（或網路已開通）
- [ ] go2rtc 配置檔已準備
- [ ] RTSP 攝影機可訪問（或 Pi Camera 可用）
- [ ] DEVICE_ID 已決定且唯一
- [ ] 環境變數檔 `/etc/mumucam/agent.env` 已建立

部署後確認：

- [ ] `systemctl status go2rtc` 顯示 active (running)
- [ ] `curl http://127.0.0.1:1984/api/streams` 回傳 JSON
- [ ] `systemctl status mumucam-agent` 顯示 active (running)
- [ ] Agent logs 顯示 "✓ Connected as device"
- [ ] Backend 可以看到設備上線
- [ ] Web UI 可以看到設備並播放影像

---

## 支援

如有問題，請檢查：

1. Agent logs: `sudo journalctl -u mumucam-agent -f`
2. go2rtc logs: `sudo journalctl -u go2rtc -f`
3. Backend logs: `docker-compose logs -f backend`
4. Cloudflare Tunnel status: https://dash.cloudflare.com
