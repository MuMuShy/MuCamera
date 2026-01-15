# MuMu Camera 部署指南

## 架構概覽

```
                                    ┌─────────────────┐
                                    │   Raspberry Pi  │
                                    │   (Device Agent)│
                                    └────────┬────────┘
                                             │ WebSocket
                                             ▼
┌──────────┐     HTTPS/WSS      ┌─────────────────────────────────┐
│  Browser │ ◄─────────────────►│           Nginx (:80)           │
│ (Viewer) │                    │  - Static files (web/)          │
└──────────┘                    │  - Proxy /api/* → backend:8000  │
                                │  - Proxy /ws/*  → backend:8000  │
                                └─────────────────┬───────────────┘
                                                  │
                                                  ▼
                                ┌─────────────────────────────────┐
                                │        Backend (:8000)          │
                                │  - FastAPI                      │
                                │  - WebSocket handlers           │
                                │  - Device proxy                 │
                                └─────────────────┬───────────────┘
                                                  │
                                    ┌─────────────┼─────────────┐
                                    ▼             ▼             ▼
                              ┌──────────┐ ┌──────────┐ ┌──────────┐
                              │ Postgres │ │  Redis   │ │  Coturn  │
                              │  (:5432) │ │  (:6379) │ │  (:3478) │
                              └──────────┘ └──────────┘ └──────────┘
```

## 本地開發

### 啟動服務
```bash
docker-compose up -d
```

### 訪問
- Web UI: http://localhost:8080
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs

### 啟動模擬器 (可選)
```bash
docker-compose --profile sim up -d device-sim
```

---

## Cloudflare Tunnel 部署 (目前使用)

### 所需 Tunnel 設定

只需要 **一個 tunnel**：

| Hostname | Service | 說明 |
|----------|---------|------|
| camera.linehero.dev | http://localhost:8080 | Nginx (前端 + API 代理) |

### 不需要的 Tunnel (可刪除)

以下 tunnel 已不需要，因為 nginx 會代理所有請求：
- `camerabackend.linehero.dev`
- `cameraviewer.linehero.dev`

### Cloudflare Tunnel 設定建議

在 tunnel 設定中啟用：
- HTTP/2
- WebSocket support (預設應該已啟用)

---

## VM 部署 (Production)

### 前置需求

1. 一台有公網 IP 的 VM
2. 域名指向 VM IP
3. Docker 和 Docker Compose 已安裝

### 部署步驟

#### 1. Clone 專案
```bash
git clone https://github.com/MuMuShy/MuCamera.git
cd MuCamera/mumu-cam
```

#### 2. 修改環境變數
```bash
cp .env.example .env
vim .env
```

修改以下設定：
```env
# 改成你的公網 IP 或域名
TURN_PUBLIC_HOST=your-domain.com

# 改成安全的密鑰
JWT_SECRET=your-secure-jwt-secret
TURN_SECRET=your-secure-turn-secret

# 加入你的域名
BACKEND_CORS_ORIGINS=http://localhost,https://your-domain.com
```

#### 3. 啟動服務
```bash
docker-compose up -d
```

#### 4. 檢查服務狀態
```bash
docker-compose ps
docker-compose logs -f
```

### 需要開放的 Port

| Port | Protocol | 用途 |
|------|----------|------|
| 80 | TCP | HTTP (Nginx) |
| 443 | TCP | HTTPS (如果自己處理 SSL) |
| 3478 | TCP/UDP | TURN server signaling |
| 49152-49252 | UDP | TURN media relay |

### 防火牆設定 (Ubuntu/Debian)
```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 3478/tcp
sudo ufw allow 3478/udp
sudo ufw allow 49152:49252/udp
```

---

## SSL 設定選項

### 選項 1: Cloudflare Proxy (推薦)

最簡單的方式，讓 Cloudflare 處理 SSL：

1. DNS 設定 A record 指向 VM IP
2. 開啟 Cloudflare Proxy (橙色雲朵)
3. SSL/TLS 設定為 "Flexible" 或 "Full"

### 選項 2: Let's Encrypt + Certbot

需要修改 nginx 配置，加入 SSL 支援：

```bash
# 安裝 certbot
sudo apt install certbot python3-certbot-nginx

# 取得證書
sudo certbot --nginx -d your-domain.com
```

### 選項 3: 使用 Traefik/Caddy

替換 nginx，這些工具有內建的自動 SSL：

```yaml
# docker-compose.override.yml 範例 (Traefik)
services:
  traefik:
    image: traefik:v2.10
    command:
      - "--certificatesresolvers.letsencrypt.acme.email=your@email.com"
      - "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json"
      - "--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - "./letsencrypt:/letsencrypt"
      - "/var/run/docker.sock:/var/run/docker.sock:ro"
```

---

## Raspberry Pi Device Agent 部署

### 安裝
```bash
# 在 Pi 上
cd /opt
sudo git clone https://github.com/MuMuShy/MuCamera.git
cd MuCamera/mumu-cam/device-agent

# 建立虛擬環境
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 設定
```bash
# 複製設定檔
cp examples/agent.env /opt/mumucam/.env

# 編輯設定
vim /opt/mumucam/.env
```

```env
BACKEND_URL=wss://camera.linehero.dev/ws/device
DEVICE_ID=pi-cam-001
```

### Systemd Service
```bash
sudo cp examples/mumucam-agent.service /etc/systemd/system/
sudo systemctl enable mumucam-agent
sudo systemctl start mumucam-agent
```

### 檢查狀態
```bash
sudo systemctl status mumucam-agent
sudo journalctl -u mumucam-agent -f
```

---

## 故障排除

### WebRTC 502 錯誤
- 檢查 nginx logs: `docker-compose logs nginx`
- 通常是 header 衝突問題，已在 `backend/app/main.py` 修復

### Device 離線 (503)
- 確認 Pi 上的 agent 有運行
- 檢查 WebSocket 連線: `journalctl -u mumucam-agent -f`

### CORS 錯誤
- 確認 `.env` 中的 `BACKEND_CORS_ORIGINS` 包含你的域名
- 重啟 backend: `docker-compose restart backend`

### TURN Server 問題
- 確認 `TURN_PUBLIC_HOST` 設定正確
- 確認防火牆開放 3478 和 49152-49252 端口
- 測試: https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/
