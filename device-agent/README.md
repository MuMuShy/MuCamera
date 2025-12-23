# MuMu Camera Device Agent - go2rtc Proxy Mode

Lightweight device agent that proxies HTTP requests to local go2rtc instance.

## 📖 完整文檔

- **[DEPLOYMENT.md](./DEPLOYMENT.md)** - 完整部署指南（含 Cloudflare Tunnel）⭐ **推薦先看這個**
- [README.md](./README.md) - 本文件（快速參考）

## 🚀 快速開始

### 選擇部署方式

| 方式 | 適用情境 | 難度 |
|------|---------|------|
| **Cloudflare Tunnel** | 無固定 IP、需要安全連線、多地點 | ⭐⭐⭐ |
| 直接連線 | 內網測試、已有固定 IP | ⭐⭐ |
| 本地測試 | 開發測試 | ⭐ |

### 一鍵部署（Raspberry Pi）

```bash
# 下載部署腳本
wget https://raw.githubusercontent.com/YOUR_REPO/device-agent/scripts/deploy.sh
chmod +x deploy.sh

# 執行部署（會互動式詢問參數）
sudo ./deploy.sh

# 或直接指定參數
sudo ./deploy.sh pi-cam-001
```

### 手動部署

```bash
# 1. 安裝依賴
sudo apt update && sudo apt install -y python3 python3-pip python3-venv

# 2. 建立目錄並下載
sudo mkdir -p /opt/mumucam
cd /opt/mumucam
# 複製 agent.py 和 requirements.txt 到此目錄

# 3. 建立 Python 環境
sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt

# 4. 設定環境變數
sudo mkdir -p /etc/mumucam
sudo nano /etc/mumucam/agent.env
```

環境變數範例：
```bash
DEVICE_ID=pi-cam-001
BACKEND_URL=wss://backend.your-domain.com/ws/device
GO2RTC_HTTP=http://127.0.0.1:1984
DEVICE_SECRET=optional-secret-key
```

```bash
# 5. 測試運行
sudo /opt/mumucam/venv/bin/python /opt/mumucam/agent.py --verbose
```

### 使用 Docker

```bash
# 建立環境變數檔
cat > agent.env <<EOF
DEVICE_ID=pi-cam-001
BACKEND_URL=wss://backend.your-domain.com/ws/device
GO2RTC_HTTP=http://127.0.0.1:1984
EOF

# 執行
docker run -d \
  --name mumucam-agent \
  --network host \
  --env-file agent.env \
  --restart unless-stopped \
  mumucam-agent
```

---

## 📋 環境變數

| 變數 | 必填 | 預設值 | 說明 |
|------|------|--------|------|
| `DEVICE_ID` | ✅ | - | 唯一裝置識別碼 |
| `BACKEND_URL` | ❌ | `ws://localhost:8000/ws/device` | Backend WebSocket URL |
| `DEVICE_SECRET` | ❌ | - | 裝置認證密鑰（可選） |
| `GO2RTC_HTTP` | ❌ | `http://127.0.0.1:1984` | go2rtc HTTP API URL |

### Backend URL 範例

```bash
# 本地測試
BACKEND_URL=ws://localhost:8000/ws/device

# 直接連線（HTTP）
BACKEND_URL=ws://192.168.1.100:8000/ws/device

# 直接連線（HTTPS/WSS）
BACKEND_URL=wss://your-domain.com/ws/device

# Cloudflare Tunnel（推薦）
BACKEND_URL=wss://backend.your-domain.com/ws/device
```

---

## 🔧 systemd 服務（開機自動啟動）

### 安裝服務

```bash
# 複製 service 檔案
sudo cp systemd/mumucam-agent.service /etc/systemd/system/
sudo cp systemd/go2rtc.service /etc/systemd/system/

# 重新載入
sudo systemctl daemon-reload

# 啟動服務
sudo systemctl start mumucam-agent
sudo systemctl start go2rtc

# 開機自動啟動
sudo systemctl enable mumucam-agent
sudo systemctl enable go2rtc
```

### 管理服務

```bash
# 查看狀態
sudo systemctl status mumucam-agent

# 查看日誌
sudo journalctl -u mumucam-agent -f

# 重啟服務
sudo systemctl restart mumucam-agent

# 停止服務
sudo systemctl stop mumucam-agent
```

---

## 📁 檔案結構

```
device-agent/
├── agent.py                    # 主程式
├── requirements.txt            # Python 依賴
├── Dockerfile                  # Docker 映像檔
├── README.md                   # 本文件
├── DEPLOYMENT.md              # 完整部署指南 ⭐
├── systemd/
│   ├── mumucam-agent.service  # Agent systemd service
│   └── go2rtc.service         # go2rtc systemd service
├── examples/
│   ├── agent.env              # 環境變數範例
│   ├── go2rtc.yaml            # go2rtc 配置範例
│   └── cloudflared-backend.yaml  # Cloudflare Tunnel 配置
└── scripts/
    └── deploy.sh              # 一鍵部署腳本
```

---

## 🏗️ 架構

```
┌─────────────────────────────────────────────────────────────────┐
│                         Raspberry Pi                            │
│                                                                 │
│  ┌──────────────┐          ┌────────────┐                      │
│  │ Device Agent │◄────────►│  go2rtc    │                      │
│  │  (Python)    │   HTTP   │  (1984)    │                      │
│  └──────┬───────┘          └─────┬──────┘                      │
│         │                         │                             │
│         │ WebSocket               │ RTSP                        │
│         │ (wss://)                │                             │
└─────────┼─────────────────────────┼─────────────────────────────┘
          │                         │
          │                    ┌────▼─────┐
          │                    │ IP Camera│
          │                    │  (RTSP)  │
          │                    └──────────┘
          │
          │ Cloudflare Tunnel (optional)
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Backend Server                             │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ FastAPI  │  │ Postgres │  │  Redis   │  │  Coturn  │       │
│  │  (8000)  │  │  (5432)  │  │  (6379)  │  │  (3478)  │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🎯 功能特性

- ✅ **自動裝置註冊** - 啟動時自動向 backend 註冊
- ✅ **WebSocket 持久連線** - 維持與 backend 的連線
- ✅ **能力回報** - 每 30 秒回報 go2rtc streams
- ✅ **HTTP 代理** - 代理 backend 對 go2rtc 的 HTTP 請求
- ✅ **自動重連** - 指數退避重連（1s → 30s max）
- ✅ **go2rtc 健康監控** - 每 10 秒檢查 go2rtc 狀態
- ✅ **訊息佇列** - 斷線時暫存訊息，重連後重送
- ✅ **優雅關閉** - 正確清理資源和任務
- ✅ **狀態管理** - 清楚的連線狀態追蹤
- ✅ **並發代理請求** - 支援同時處理多個代理請求

---

## 📊 日誌

所有日誌都有前綴標籤：

- `[ws]` - WebSocket 連線事件
- `[go2rtc]` - go2rtc API 互動
- `[proxy]` - HTTP 代理請求

範例：
```
2024-12-24 10:00:00 - __main__ - INFO - [ws] ✓ Connected as device: pi-cam-001
2024-12-24 10:00:15 - __main__ - DEBUG - [ws] ♥ Heartbeat sent
2024-12-24 10:00:30 - __main__ - DEBUG - [go2rtc] ✓ Reported capabilities (2 streams)
2024-12-24 10:01:00 - __main__ - INFO - [proxy] GET /api/streams (rid=abc123)
2024-12-24 10:01:00 - __main__ - INFO - [proxy] GET /api/streams → 200 (1234 bytes)
```

---

## 🐛 故障排除

### Agent 無法連線

```bash
# 檢查環境變數
cat /etc/mumucam/agent.env

# 測試 Backend 連線
ping backend.your-domain.com

# 查看詳細日誌
sudo /opt/mumucam/venv/bin/python /opt/mumucam/agent.py --verbose
```

### go2rtc 無法啟動

```bash
# 檢查 go2rtc 狀態
sudo systemctl status go2rtc

# 測試 go2rtc API
curl http://127.0.0.1:1984/api/streams

# 查看配置
sudo cat /etc/go2rtc/go2rtc.yaml
```

### 查看更多故障排除

請參考 [DEPLOYMENT.md](./DEPLOYMENT.md#故障排除) 的完整故障排除指南。

---

## 📚 更多資源

- [完整部署指南](./DEPLOYMENT.md) - 含 Cloudflare Tunnel 完整設定
- [systemd 服務檔](./systemd/) - 開機自動啟動設定
- [配置範例](./examples/) - go2rtc、環境變數、Cloudflare 配置
- [一鍵部署腳本](./scripts/deploy.sh) - 自動化部署

---

## 🆘 需要幫助？

1. 先查看 [DEPLOYMENT.md](./DEPLOYMENT.md)
2. 檢查日誌：`sudo journalctl -u mumucam-agent -f`
3. 檢查 go2rtc：`curl http://127.0.0.1:1984/api/streams`
4. 檢查網路：`ping backend.your-domain.com`

---

## 📝 授權

MIT License
