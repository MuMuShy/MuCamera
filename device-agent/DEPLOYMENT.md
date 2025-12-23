# MuMu Camera 完整部署指南

本指南涵蓋 Backend Server 和 Device Agent 的完整部署流程，包括本地測試、VM 生產環境、以及 Cloudflare Tunnel 設定。

## 📋 目錄

### 第一部分：Backend Server 部署
- [部署概覽](#部署概覽)
- [本地開發環境部署](#本地開發環境部署)
- [生產環境部署（VM/雲端）](#生產環境部署vmcloudflare雲端)
- [Cloudflare Tunnel 設定](#cloudflare-tunnel-設定)

### 第二部分：Device Agent 部署（Raspberry Pi）
- [Raspberry Pi 準備工作](#raspberry-pi-準備工作)
- [安裝 go2rtc](#安裝-go2rtc)
- [安裝 Device Agent](#安裝-device-agent)
- [systemd 服務設定](#systemd-服務設定)

### 維護與運營
- [多設備部署](#多設備部署)
- [監控與維護](#監控與維護)
- [故障排除](#故障排除)

---

## 部署概覽

### 系統架構

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Raspberry Pi (Device)                          │
│  ┌──────────────┐              ┌────────────┐                      │
│  │ Device Agent │◄────HTTP────►│  go2rtc    │◄─RTSP─► IP Camera    │
│  │  (Python)    │              │  (WebRTC)  │                      │
│  └──────┬───────┘              └────────────┘                      │
│         │ WebSocket (wss://)                                       │
└─────────┼──────────────────────────────────────────────────────────┘
          │
          │ (透過 Cloudflare Tunnel 或直接連線)
          │
┌─────────▼──────────────────────────────────────────────────────────┐
│                    Backend Server (VM/雲端)                        │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ FastAPI  │  │ Postgres │  │  Redis   │  │  Coturn  │          │
│  │  (8000)  │  │  (5432)  │  │  (6379)  │  │  (3478)  │          │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘          │
│       │                                                             │
│  ┌────▼─────┐                                                      │
│  │  Nginx   │  (Optional: 提供 Web UI)                             │
│  │  (8080)  │                                                      │
│  └──────────┘                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 部署方案比較

| 方案 | Backend 連線方式 | 適用場景 | 難度 | 推薦 |
|------|-----------------|---------|------|------|
| 本地測試 | localhost | 開發測試 | ⭐ | 開發階段 |
| 直接連線 | 公網 IP/內網 IP | 有固定 IP、內網環境 | ⭐⭐ | 企業內網 |
| **Cloudflare Tunnel** | Cloudflare 代理 | 無固定 IP、需要安全連線 | ⭐⭐⭐ | **生產環境推薦** |

---

# 第一部分：Backend Server 部署

## 本地開發環境部署

適合：開發測試、功能驗證

### 1. 系統需求

- **作業系統**：Windows 10/11, macOS, Linux
- **軟體需求**：
  - Docker Desktop (Windows/Mac) 或 Docker Engine (Linux)
  - Docker Compose v2.0+
  - Git

### 2. 克隆專案

```bash
git clone https://github.com/YOUR_REPO/mumu-cam.git
cd mumu-cam
```

### 3. 設定環境變數

```bash
# 複製環境變數範例
cp .env.example .env

# 編輯 .env（本地測試可使用預設值）
# Windows
notepad .env

# Linux/macOS
nano .env
```

環境變數說明（`.env`）：

```bash
# 資料庫配置
POSTGRES_DB=mumucam
POSTGRES_USER=mumucam
POSTGRES_PASSWORD=mumucam123  # 生產環境請改為強密碼

# TURN Server 配置
TURN_HOST=coturn              # Docker 內部網路使用
TURN_PUBLIC_HOST=localhost    # 瀏覽器連線使用（本地測試用 localhost）
TURN_PORT=3478
TURN_SECRET=mumucam_turn_secret_key  # 生產環境請改為隨機密鑰

# JWT 配置
JWT_SECRET=mumucam_jwt_secret_key    # 生產環境請改為隨機密鑰

# CORS 設定
BACKEND_CORS_ORIGINS=http://localhost,http://localhost:8080
```

### 4. 啟動所有服務

```bash
# 啟動 Backend + 資料庫 + Redis + TURN
docker-compose up -d

# 查看 logs
docker-compose logs -f backend

# 確認服務運行
docker-compose ps
```

### 5. 初始化資料庫

```bash
# 進入 backend 容器
docker-compose exec backend bash

# 執行 migration
alembic upgrade head

# 離開容器
exit
```

### 6. 測試 Backend

```bash
# 檢查 API 健康狀態
curl http://localhost:8000/health

# 應該回傳：{"status":"healthy"}

# 檢查 WebSocket 端點（需要 wscat）
npm install -g wscat
wscat -c ws://localhost:8000/ws/viewer
```

### 7. 訪問 Web UI

開啟瀏覽器：`http://localhost:8080`

---

## 生產環境部署（VM/Cloud/雲端）

適合：正式運營、長期部署

### 1. 伺服器需求

**最低配置**：
- CPU: 2 核心
- RAM: 4 GB
- 硬碟: 20 GB SSD
- 網路: 10 Mbps 上傳（每個視訊流約需 2-5 Mbps）

**推薦配置**：
- CPU: 4 核心
- RAM: 8 GB
- 硬碟: 50 GB SSD
- 網路: 50 Mbps 上傳

**雲端平台參考**：
- AWS EC2: t3.medium
- Google Cloud: e2-medium
- Azure: B2s
- DigitalOcean: Basic Droplet ($24/mo)

### 2. 作業系統安裝

推薦：**Ubuntu 22.04 LTS** 或 **Ubuntu 24.04 LTS**

```bash
# 更新系統
sudo apt update && sudo apt upgrade -y

# 安裝基本工具
sudo apt install -y curl wget git vim ufw
```

### 3. 安裝 Docker 和 Docker Compose

```bash
# 安裝 Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# 啟動 Docker
sudo systemctl start docker
sudo systemctl enable docker

# 將當前用戶加入 docker 群組（可選）
sudo usermod -aG docker $USER
newgrp docker

# 驗證安裝
docker --version
docker compose version
```

### 4. 部署 Backend

```bash
# 建立專案目錄
sudo mkdir -p /opt/mumucam
cd /opt/mumucam

# 克隆專案
git clone https://github.com/YOUR_REPO/mumu-cam.git .

# 或上傳檔案（使用 scp）
# scp -r ./mumu-cam user@your-server:/opt/mumucam
```

### 5. 設定環境變數（生產環境）

```bash
# 複製環境變數範例
cp .env.example .env

# 編輯環境變數
sudo nano .env
```

**生產環境環境變數設定**：

```bash
# 資料庫配置（使用強密碼）
POSTGRES_DB=mumucam
POSTGRES_USER=mumucam
POSTGRES_PASSWORD=$(openssl rand -hex 32)  # 產生隨機密碼

# TURN Server 配置
TURN_HOST=coturn
TURN_PUBLIC_HOST=your-domain.com  # 改為你的 domain 或公網 IP
TURN_PORT=3478
TURN_SECRET=$(openssl rand -hex 32)  # 產生隨機密鑰

# JWT 配置
JWT_SECRET=$(openssl rand -hex 32)  # 產生隨機密鑰

# CORS 設定（添加你的 domain）
BACKEND_CORS_ORIGINS=https://your-domain.com,https://backend.your-domain.com
```

**產生隨機密鑰**：

```bash
# 產生 TURN_SECRET
echo "TURN_SECRET=$(openssl rand -hex 32)"

# 產生 JWT_SECRET
echo "JWT_SECRET=$(openssl rand -hex 32)"

# 產生 POSTGRES_PASSWORD
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
```

### 6. 防火牆設定

```bash
# 啟用防火牆
sudo ufw enable

# 允許 SSH
sudo ufw allow 22/tcp

# 允許 HTTP/HTTPS（如果使用 Nginx 或需要）
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# 允許 Backend API（如果需要直接訪問）
sudo ufw allow 8000/tcp

# 允許 TURN Server（WebRTC NAT 穿透）
sudo ufw allow 3478/tcp
sudo ufw allow 3478/udp
sudo ufw allow 49152:49252/udp

# 檢查狀態
sudo ufw status verbose
```

**如果使用 firewalld（CentOS/RHEL）**：

```bash
sudo firewall-cmd --permanent --add-port=8000/tcp
sudo firewall-cmd --permanent --add-port=3478/tcp
sudo firewall-cmd --permanent --add-port=3478/udp
sudo firewall-cmd --permanent --add-port=49152-49252/udp
sudo firewall-cmd --reload
```

### 7. 啟動生產環境

```bash
cd /opt/mumucam

# 啟動所有服務
docker-compose up -d

# 查看日誌
docker-compose logs -f

# 檢查服務狀態
docker-compose ps
```

### 8. 初始化資料庫

```bash
# 執行 migration
docker-compose exec backend alembic upgrade head

# 或進入容器手動執行
docker-compose exec backend bash
alembic upgrade head
exit
```

### 9. 設定 Docker 自動重啟

```bash
# 設定 Docker 開機自動啟動（已在步驟 3 完成）
sudo systemctl enable docker

# Docker Compose 服務已設定 restart: unless-stopped
# 檢查 docker-compose.yml 確認
```

### 10. （可選）設定 SSL 憑證

**使用 Let's Encrypt（免費）**：

```bash
# 安裝 Certbot
sudo apt install -y certbot python3-certbot-nginx

# 產生憑證（需要 domain 指向此伺服器）
sudo certbot --nginx -d your-domain.com -d backend.your-domain.com

# 自動更新憑證
sudo certbot renew --dry-run
```

---

## Cloudflare Tunnel 設定

Cloudflare Tunnel 讓您無需公網 IP 或開放防火牆，就能安全地將 Backend 服務暴露到網際網路。

### 優點

✅ **無需公網 IP** - 適合家用網路、動態 IP
✅ **自動 SSL/TLS** - 免費 HTTPS 加密
✅ **DDoS 防護** - Cloudflare 網路保護
✅ **穿透 NAT** - 不需要設定路由器 Port Forwarding
✅ **多 Tunnel 支援** - 可同時連接多台設備

### 方案 A：使用 Cloudflare Dashboard 設定（推薦，較簡單）

這是您提到的方式，透過網頁介面設定，無需 CLI。

#### 步驟 1：建立 Cloudflare Tunnel（Dashboard）

1. 登入 **Cloudflare Dashboard**: https://dash.cloudflare.com
2. 選擇您的 domain（例如 `example.com`）
3. 左側選單：**Zero Trust** → **Networks** → **Tunnels**
4. 點擊 **Create a tunnel**
5. 選擇 **Cloudflared**
6. 輸入 Tunnel 名稱：`mumucam-backend`
7. 點擊 **Save tunnel**

#### 步驟 2：安裝 Connector（在 Backend Server）

Dashboard 會顯示安裝指令，複製並在您的 Backend Server 執行：

```bash
# 範例（實際指令會在 Dashboard 顯示）
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb

# 執行 Connector（Dashboard 會提供完整指令）
sudo cloudflared service install <your-token-here>
```

#### 步驟 3：設定 Public Hostname（在 Dashboard）

在 Tunnel 設定頁面：

1. 點擊 **Public Hostname** tab
2. 點擊 **Add a public hostname**

**設定 1：WebSocket for Device Agent**
- **Subdomain**: `backend`
- **Domain**: `your-domain.com`
- **Path**: `/ws/device`
- **Type**: `HTTP`
- **URL**: `http://localhost:8000`
- **Additional settings**:
  - ☑ **No TLS Verify**
  - **Connect Timeout**: 30s
  - ☑ **Disable Chunked Encoding**

**設定 2：WebSocket for Web Viewer**
- **Subdomain**: `backend`
- **Domain**: `your-domain.com`
- **Path**: `/ws/viewer`
- **Type**: `HTTP`
- **URL**: `http://localhost:8000`
- **Additional settings** 同上

**設定 3：HTTP API**
- **Subdomain**: `backend`
- **Domain**: `your-domain.com`
- **Path**: (留空，代表所有其他路徑)
- **Type**: `HTTP`
- **URL**: `http://localhost:8000`
- **Additional settings**:
  - ☑ **No TLS Verify**

4. 點擊 **Save hostname**

#### 步驟 4：啟動 Cloudflared 服務

```bash
# 啟動服務
sudo systemctl start cloudflared
sudo systemctl enable cloudflared

# 檢查狀態
sudo systemctl status cloudflared
```

#### 步驟 5：驗證連線

在 Dashboard 的 Tunnel 頁面，應該看到：
- **Status**: `HEALTHY`（綠色）
- **Connectors**: 1 active

測試連線：

```bash
# 測試 HTTP API
curl https://backend.your-domain.com/health

# 測試 WebSocket（需要 wscat）
wscat -c wss://backend.your-domain.com/ws/viewer
```

---

### 方案 B：使用 cloudflared CLI 設定（進階）

適合自動化部署或需要版本控制設定檔。

#### 步驟 1：安裝 cloudflared

```bash
# Ubuntu/Debian
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

# 或使用 apt
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-archive-keyring.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-archive-keyring.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared
```

#### 步驟 2：登入 Cloudflare

```bash
cloudflared tunnel login
```

這會開啟瀏覽器，選擇要使用的 domain。

#### 步驟 3：建立 Tunnel

```bash
# 建立 tunnel
cloudflared tunnel create mumucam-backend

# 記下 Tunnel ID
# 輸出範例：Created tunnel mumucam-backend with id xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

#### 步驟 4：設定 DNS

```bash
# 設定子網域指向 tunnel
cloudflared tunnel route dns mumucam-backend backend.your-domain.com
```

#### 步驟 5：建立配置檔

```bash
sudo mkdir -p /etc/cloudflared
sudo nano /etc/cloudflared/config.yml
```

填入以下內容（**使用專案提供的範例**）：

```yaml
tunnel: mumucam-backend
credentials-file: /root/.cloudflared/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.json

ingress:
  # WebSocket for Device Agent
  - hostname: backend.your-domain.com
    path: /ws/device
    service: http://localhost:8000
    originRequest:
      noTLSVerify: true
      connectTimeout: 30s
      http2Origin: false
      disableChunkedEncoding: true

  # WebSocket for Web Viewer
  - hostname: backend.your-domain.com
    path: /ws/viewer
    service: http://localhost:8000
    originRequest:
      noTLSVerify: true
      connectTimeout: 30s
      http2Origin: false
      disableChunkedEncoding: true

  # HTTP API
  - hostname: backend.your-domain.com
    service: http://localhost:8000
    originRequest:
      noTLSVerify: true
      connectTimeout: 30s

  # Catch-all（必須）
  - service: http_status:404
```

**或複製專案範例**：

```bash
cp /opt/mumucam/device-agent/examples/cloudflared-backend.yaml /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml
# 修改 tunnel ID 和 domain
```

#### 步驟 6：啟動 Tunnel

```bash
# 測試配置
sudo cloudflared tunnel --config /etc/cloudflared/config.yml run

# 安裝為 service
sudo cloudflared service install
sudo systemctl start cloudflared
sudo systemctl enable cloudflared

# 檢查狀態
sudo systemctl status cloudflared
```

#### 步驟 7：驗證

```bash
# 檢查 tunnel 狀態
cloudflared tunnel list

# 測試連線
curl https://backend.your-domain.com/health
```

---

### Cloudflare Tunnel 故障排除

#### 問題：Tunnel 顯示 Inactive

**檢查**：

```bash
# 查看 cloudflared 日誌
sudo journalctl -u cloudflared -f

# 重啟服務
sudo systemctl restart cloudflared
```

#### 問題：WebSocket 連線失敗

**檢查配置檔**：

- 確認 `http2Origin: false`（WebSocket 不支援 HTTP/2）
- 確認 `disableChunkedEncoding: true`
- 確認 `service` 使用 `http://` 而非 `ws://`（Cloudflare 會自動處理）

#### 問題：Dashboard 顯示 Tunnel 不存在

**重新建立 Tunnel**：

```bash
# 刪除舊 tunnel
cloudflared tunnel delete mumucam-backend

# 重新建立
cloudflared tunnel create mumucam-backend

# 更新配置檔中的 credentials-file 路徑
sudo nano /etc/cloudflared/config.yml
```

---

## 更新環境變數（使用 Cloudflare Tunnel）

如果使用 Cloudflare Tunnel，需要更新 Backend 的 TURN 設定：

```bash
# 編輯 .env
sudo nano /opt/mumucam/.env
```

修改：

```bash
# TURN_PUBLIC_HOST 改為您的 Cloudflare Tunnel domain
TURN_PUBLIC_HOST=backend.your-domain.com

# CORS 也要更新
BACKEND_CORS_ORIGINS=https://backend.your-domain.com,https://your-domain.com
```

重啟 Backend：

```bash
cd /opt/mumucam
docker-compose restart backend
```

---

# 第二部分：Device Agent 部署（Raspberry Pi）

## Raspberry Pi 準備工作

### 1. 硬體需求

- **Raspberry Pi 3/4/5** 或其他 Linux 裝置
- **IP Camera**（支援 RTSP）或 **Pi Camera Module**
- **Micro SD 卡**：16 GB 以上（推薦 32 GB Class 10）
- **電源**：5V 3A（Pi 4）或 5V 2.5A（Pi 3）
- **網路**：Wi-Fi 或有線網路

### 2. 作業系統安裝

推薦：**Raspberry Pi OS Lite（64-bit）**

```bash
# 使用 Raspberry Pi Imager
# 下載：https://www.raspberrypi.com/software/

# 或手動燒錄
# 1. 下載 Raspberry Pi OS Lite
# 2. 使用 balenaEtcher 燒錄到 SD 卡
# 3. 啟用 SSH（在 boot 分區建立空白 ssh 檔案）
```

### 3. 初始設定

```bash
# SSH 連線到 Pi
ssh pi@raspberrypi.local
# 預設密碼：raspberry

# 更新系統
sudo apt update && sudo apt upgrade -y

# 安裝必要套件
sudo apt install -y \
  python3 \
  python3-pip \
  python3-venv \
  git \
  curl \
  wget \
  vim

# 設定時區
sudo raspi-config
# System Options → Timezone

# 設定主機名稱（可選）
sudo raspi-config
# System Options → Hostname → 輸入新名稱（例如 pi-cam-001）
```

### 4. 確認 Backend URL

根據您的 Backend 部署方式，確認 URL：

- **本地測試**：`ws://YOUR_COMPUTER_IP:8000/ws/device`
- **直接連線**：`ws://YOUR_SERVER_IP:8000/ws/device` 或 `wss://your-domain.com/ws/device`
- **Cloudflare Tunnel**：`wss://backend.your-domain.com/ws/device`

---

## 安裝 go2rtc

go2rtc 負責將 RTSP 攝影機串流轉換為 WebRTC。

### 方法 1：預編譯執行檔（推薦）

```bash
# 下載 go2rtc（自動偵測架構）
cd /opt
ARCH=$(uname -m)
if [ "$ARCH" = "aarch64" ]; then
  GO2RTC_URL="https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_arm64"
elif [ "$ARCH" = "armv7l" ]; then
  GO2RTC_URL="https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_arm"
elif [ "$ARCH" = "x86_64" ]; then
  GO2RTC_URL="https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_amd64"
else
  echo "不支援的架構: $ARCH"
  exit 1
fi

sudo wget -O go2rtc $GO2RTC_URL
sudo chmod +x go2rtc
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

### 建立 go2rtc 配置檔

```bash
sudo mkdir -p /etc/go2rtc
sudo nano /etc/go2rtc/go2rtc.yaml
```

填入：

```yaml
api:
  listen: "127.0.0.1:1984"

streams:
  # RTSP 攝影機範例
  cam:
    - rtsp://admin:password@192.168.1.100:554/stream1

  # Raspberry Pi Camera Module 範例（需要 v4l2）
  # picam:
  #   - ffmpeg:device?video=/dev/video0&input_format=h264&video_size=1920x1080

  # USB Webcam 範例
  # webcam:
  #   - ffmpeg:device?video=/dev/video0&input_format=mjpeg&video_size=1280x720

log:
  level: info
```

**或複製專案範例**：

```bash
sudo cp /opt/mumucam/device-agent/examples/go2rtc.yaml /etc/go2rtc/go2rtc.yaml
sudo nano /etc/go2rtc/go2rtc.yaml
# 修改攝影機 URL
```

### 測試 go2rtc

```bash
# 手動啟動測試
/opt/go2rtc -c /etc/go2rtc/go2rtc.yaml

# 在另一個終端測試 API
curl http://127.0.0.1:1984/api/streams

# 應該回傳 JSON（即使沒有 stream 也會回傳 {}）
# 按 Ctrl+C 停止
```

---

## 安裝 Device Agent

### 方法 1：使用一鍵部署腳本（推薦）

```bash
# 下載部署腳本
cd /tmp
wget https://raw.githubusercontent.com/YOUR_REPO/main/device-agent/scripts/deploy.sh
chmod +x deploy.sh

# 執行部署（會互動式詢問 DEVICE_ID 和 BACKEND_URL）
sudo ./deploy.sh

# 或直接指定參數
sudo ./deploy.sh pi-cam-001
```

腳本會自動：
1. 建立目錄
2. 安裝系統依賴
3. 下載 agent.py 和 requirements.txt
4. 建立 Python 虛擬環境
5. 建立環境變數檔
6. 下載並安裝 go2rtc
7. 安裝 systemd services

### 方法 2：手動部署

#### 2.1 建立目錄

```bash
sudo mkdir -p /opt/mumucam /etc/mumucam /etc/go2rtc
```

#### 2.2 下載 Agent 檔案

```bash
cd /opt/mumucam

# 方法 A：從 GitHub 下載
sudo wget -O agent.py https://raw.githubusercontent.com/YOUR_REPO/main/device-agent/agent.py
sudo wget -O requirements.txt https://raw.githubusercontent.com/YOUR_REPO/main/device-agent/requirements.txt

# 方法 B：從本地複製（使用 scp）
# 在您的電腦執行：
# scp device-agent/agent.py pi@raspberrypi.local:/tmp/
# scp device-agent/requirements.txt pi@raspberrypi.local:/tmp/
# 然後在 Pi 上：
# sudo mv /tmp/agent.py /tmp/requirements.txt /opt/mumucam/
```

#### 2.3 建立 Python 虛擬環境

```bash
cd /opt/mumucam
sudo python3 -m venv venv
sudo venv/bin/pip install --upgrade pip
sudo venv/bin/pip install -r requirements.txt
```

#### 2.4 建立環境變數檔

```bash
sudo nano /etc/mumucam/agent.env
```

填入：

```bash
# 裝置 ID（每台 Pi 要不同）
DEVICE_ID=pi-cam-001

# Backend URL
# 本地測試：ws://YOUR_COMPUTER_IP:8000/ws/device
# 直接連線：wss://your-domain.com/ws/device
# Cloudflare Tunnel：wss://backend.your-domain.com/ws/device
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

**或複製專案範例**：

```bash
sudo cp /opt/mumucam/device-agent/examples/agent.env /etc/mumucam/agent.env
sudo nano /etc/mumucam/agent.env
# 修改 DEVICE_ID 和 BACKEND_URL
```

#### 2.5 測試 Agent

```bash
# 執行 agent（verbose 模式）
sudo /opt/mumucam/venv/bin/python /opt/mumucam/agent.py --verbose
```

應該看到：

```
2024-xx-xx xx:xx:xx - __main__ - INFO - === MuMu Camera Device Agent (go2rtc mode) ===
2024-xx-xx xx:xx:xx - __main__ - INFO - Device ID: pi-cam-001
2024-xx-xx xx:xx:xx - __main__ - INFO - [go2rtc] ✓ Service is now healthy
2024-xx-xx xx:xx:xx - __main__ - INFO - ✓ Device registered
2024-xx-xx xx:xx:xx - __main__ - INFO - [ws] Connecting to wss://backend.your-domain.com/ws/device...
2024-xx-xx xx:xx:xx - __main__ - INFO - [ws] ✓ Connected as device: pi-cam-001
```

按 `Ctrl+C` 停止。

---

## systemd 服務設定

設定開機自動啟動。

### 1. 建立 go2rtc service

```bash
sudo nano /etc/systemd/system/go2rtc.service
```

填入：

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
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**或複製專案範例**：

```bash
sudo cp /opt/mumucam/device-agent/systemd/go2rtc.service /etc/systemd/system/
```

### 2. 建立 mumucam-agent service

```bash
sudo nano /etc/systemd/system/mumucam-agent.service
```

填入：

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

**或複製專案範例**：

```bash
sudo cp /opt/mumucam/device-agent/systemd/mumucam-agent.service /etc/systemd/system/
```

### 3. 啟動服務

```bash
# 重新載入 systemd
sudo systemctl daemon-reload

# 啟動 go2rtc
sudo systemctl start go2rtc
sudo systemctl enable go2rtc

# 啟動 agent
sudo systemctl start mumucam-agent
sudo systemctl enable mumucam-agent

# 檢查狀態
sudo systemctl status go2rtc
sudo systemctl status mumucam-agent
```

### 4. 查看日誌

```bash
# Agent 日誌
sudo journalctl -u mumucam-agent -f

# go2rtc 日誌
sudo journalctl -u go2rtc -f

# 只看最近 100 行
sudo journalctl -u mumucam-agent -n 100
```

---

# 維護與運營

## 多設備部署

### 1. 為每台設備設定不同 DEVICE_ID

```bash
# Pi 1
DEVICE_ID=pi-cam-livingroom

# Pi 2
DEVICE_ID=pi-cam-kitchen

# Pi 3
DEVICE_ID=pi-cam-garage
```

### 2. 所有 Pi 都連到同一個 Backend

```bash
BACKEND_URL=wss://backend.your-domain.com/ws/device
```

### 3. 批次部署腳本

在每台 Pi 上執行：

```bash
# 下載部署腳本
wget https://raw.githubusercontent.com/YOUR_REPO/main/device-agent/scripts/deploy.sh
chmod +x deploy.sh

# 部署（指定不同 DEVICE_ID）
sudo ./deploy.sh pi-cam-livingroom
sudo ./deploy.sh pi-cam-kitchen
sudo ./deploy.sh pi-cam-garage
```

---

## 監控與維護

### 查看服務狀態

```bash
# Backend 服務
docker-compose ps
docker-compose logs -f backend

# Device Agent 服務
sudo systemctl status mumucam-agent
sudo journalctl -u mumucam-agent -f

# go2rtc 服務
sudo systemctl status go2rtc
sudo journalctl -u go2rtc -f
```

### 重啟服務

```bash
# Backend
docker-compose restart backend

# Device Agent
sudo systemctl restart mumucam-agent

# go2rtc
sudo systemctl restart go2rtc
```

### 檢查連線

```bash
# Backend API
curl http://localhost:8000/health
curl https://backend.your-domain.com/health

# go2rtc
curl http://127.0.0.1:1984/api/streams

# WebSocket（需要 wscat）
wscat -c wss://backend.your-domain.com/ws/device
```

### 資源監控

```bash
# CPU/Memory（Backend）
docker stats

# CPU/Memory（Raspberry Pi）
htop
# 或
top -p $(pidof python)

# 網路使用
sudo nethogs
```

### 更新 Agent

```bash
# 在 Raspberry Pi 上
cd /opt/mumucam
sudo git pull origin main
sudo systemctl restart mumucam-agent
```

---

## 故障排除

### 問題 1：Agent 無法連線到 Backend

**症狀**：

```
[ws] Connection error: [Errno 111] Connection refused
[ws] ⟳ Reconnecting in 1.0s (attempt 1)
```

**解決方法**：

1. 檢查 Backend URL 是否正確：

```bash
cat /etc/mumucam/agent.env | grep BACKEND_URL
ping backend.your-domain.com
```

2. 檢查 Backend 是否運行：

```bash
# 在 Backend Server
docker-compose ps
docker-compose logs backend
```

3. 檢查防火牆：

```bash
# 在 Backend Server
sudo ufw status
telnet backend.your-domain.com 8000
```

4. 測試 WebSocket：

```bash
wscat -c wss://backend.your-domain.com/ws/device
```

---

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

3. 測試 RTSP 攝影機：

```bash
ffprobe rtsp://admin:password@192.168.1.100:554/stream1
```

4. 查看 go2rtc 日誌：

```bash
sudo journalctl -u go2rtc -f
```

---

### 問題 3：Cloudflare Tunnel 斷線

**症狀**：

```
cloudflared: connection refused
Tunnel disconnected
```

**解決方法**：

1. 檢查 cloudflared 狀態：

```bash
sudo systemctl status cloudflared
sudo journalctl -u cloudflared -f
```

2. 重啟 cloudflared：

```bash
sudo systemctl restart cloudflared
```

3. 檢查 Cloudflare Dashboard：
   - 前往 https://dash.cloudflare.com
   - Zero Trust → Tunnels
   - 確認 tunnel 狀態為 "HEALTHY"

4. 重新安裝 Tunnel（如果損壞）：

```bash
# CLI 方式
cloudflared tunnel delete mumucam-backend
cloudflared tunnel create mumucam-backend
sudo nano /etc/cloudflared/config.yml  # 更新 credentials-file
sudo systemctl restart cloudflared

# Dashboard 方式
# 在 Dashboard 刪除舊 Tunnel，重新建立並重新安裝 Connector
```

---

### 問題 4：Device 顯示 Disconnected

**症狀**：

Web UI 顯示設備離線。

**解決方法**：

1. 檢查 Agent 是否運行：

```bash
sudo systemctl status mumucam-agent
```

2. 查看 Agent 日誌：

```bash
sudo journalctl -u mumucam-agent -n 100
```

3. 檢查網路連線：

```bash
ping 8.8.8.8
ping backend.your-domain.com
```

4. 手動執行 Agent（debug 模式）：

```bash
sudo systemctl stop mumucam-agent
sudo /opt/mumucam/venv/bin/python /opt/mumucam/agent.py --verbose
```

---

### 問題 5：WebRTC 無法連線（TURN 問題）

**症狀**：

瀏覽器無法播放視訊，ICE 連線失敗。

**解決方法**：

1. 檢查 TURN Server：

```bash
# 在 Backend Server
docker-compose logs coturn
```

2. 檢查防火牆是否開放 TURN ports：

```bash
sudo ufw status | grep 3478
sudo ufw status | grep 49152
```

3. 測試 TURN Server（使用 Trickle ICE）：

訪問：https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/

填入：
- **STUN or TURN URI**: `turn:backend.your-domain.com:3478`
- **TURN username**: 從 backend logs 取得
- **TURN password**: 從 backend logs 取得

點擊 "Add Server" → "Gather candidates"

4. 檢查 TURN_PUBLIC_HOST 設定：

```bash
# 在 Backend Server
cat .env | grep TURN_PUBLIC_HOST
# 應該是 backend.your-domain.com（如果使用 Cloudflare Tunnel）
```

---

## 完整檢查清單

### Backend Server 部署檢查清單

- [ ] Docker 和 Docker Compose 已安裝
- [ ] `.env` 檔案已設定（生產環境使用強密碼）
- [ ] 防火牆已開放必要 ports（8000, 3478, 49152-49252）
- [ ] `docker-compose up -d` 成功啟動所有服務
- [ ] 資料庫 migration 已執行（`alembic upgrade head`）
- [ ] Backend API 可訪問（`curl http://localhost:8000/health`）
- [ ] Cloudflare Tunnel 已設定並顯示 HEALTHY（如果使用）
- [ ] TURN Server 可連線（使用 Trickle ICE 測試）

### Device Agent 部署檢查清單

- [ ] Raspberry Pi 已更新並安裝必要套件
- [ ] go2rtc 已下載並配置（`/etc/go2rtc/go2rtc.yaml`）
- [ ] RTSP 攝影機可訪問或 Pi Camera 可用
- [ ] Device Agent 檔案已下載（`/opt/mumucam/agent.py`）
- [ ] Python 虛擬環境已建立並安裝依賴
- [ ] 環境變數檔已建立（`/etc/mumucam/agent.env`）
- [ ] DEVICE_ID 已設定且唯一
- [ ] BACKEND_URL 正確（測試連線成功）
- [ ] systemd services 已安裝並啟動
- [ ] `systemctl status go2rtc` 顯示 active (running)
- [ ] `systemctl status mumucam-agent` 顯示 active (running)
- [ ] Agent logs 顯示 "✓ Connected as device"
- [ ] Backend 可看到設備上線
- [ ] Web UI 可看到設備並播放視訊

---

## 支援與資源

- **專案文件**：`README.md`、`DEPLOYMENT.md`（本文件）
- **範例配置**：`device-agent/examples/`
- **部署腳本**：`device-agent/scripts/deploy.sh`
- **systemd 範例**：`device-agent/systemd/`

**日誌位置**：

- Backend: `docker-compose logs -f backend`
- Device Agent: `sudo journalctl -u mumucam-agent -f`
- go2rtc: `sudo journalctl -u go2rtc -f`
- Cloudflared: `sudo journalctl -u cloudflared -f`

**常用指令**：

```bash
# Backend
docker-compose ps
docker-compose logs -f
docker-compose restart backend

# Device Agent
sudo systemctl status mumucam-agent
sudo journalctl -u mumucam-agent -f
sudo systemctl restart mumucam-agent

# Cloudflare Tunnel
sudo systemctl status cloudflared
cloudflared tunnel list
```

---

**祝您部署順利！如有問題，請查看故障排除章節或檢查日誌。**
