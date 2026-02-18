# LiveKit SFU 整合紀錄

**專案：** MuMu Camera 水下直播系統
**日期：** 2026-02-18
**版本：** v2.1.0

---

## 1. 背景與目的

MuMu Camera 原本使用 P2P WebRTC 架構，每個觀看者直接連到樹莓派的 go2rtc。當透過 4G 上傳（約 4 Mbps）時，4 人同時觀看就會導致頻寬不足、不斷重連。

整合 LiveKit SFU 後，Pi 只需上傳 1 路串流到 Server，Server 負責分發給所有觀看者，大幅降低 Pi 端的上傳壓力。

---

## 2. 架構變更

### 2.1 P2P 模式（原本，LIVEKIT_ENABLED=false）

```
Pi go2rtc ──WebRTC P2P──→ 觀看者 1（1 路上傳）
Pi go2rtc ──WebRTC P2P──→ 觀看者 2（2 路上傳）
Pi go2rtc ──WebRTC P2P──→ 觀看者 3（3 路上傳）
Pi go2rtc ──WebRTC P2P──→ 觀看者 N（N 路上傳）
```

### 2.2 LiveKit 模式（新增，LIVEKIT_ENABLED=true）

```
Pi go2rtc ──RTSP──→ FFmpeg ──RTMP──→ LiveKit Ingress ──WebRTC──→ 觀看者 1
                                                       ──WebRTC──→ 觀看者 2
                                                       ──WebRTC──→ 觀看者 N
                                                     （Pi 只有 1 路上傳）
```

PTZ 雲台控制、GPS 定位、錄影回放等功能繼續走現有 WebSocket 通道，不受影響。

---

## 3. 設計原則

- **雙模式共存**：透過 `LIVEKIT_ENABLED` 環境變數控制，`false` 時系統行為完全不變
- **零侵入**：LiveKit 相關程式碼全部有 `if LIVEKIT_ENABLED` 判斷，關閉時不載入
- **隨時回退**：改 `LIVEKIT_ENABLED=false` 重啟 backend 即可回到 P2P
- **Pi 端免設定**：Pi 不需要任何 LiveKit 設定，Backend 會自動通知 Pi 開始推流

---

## 4. 修改檔案清單

### 4.1 新建檔案

| 檔案 | 說明 |
|------|------|
| `backend/app/livekit_service.py` | LiveKit 服務模組：Token 產生、Ingress 建立/刪除 |
| `livekit/livekit.yaml` | LiveKit Server 設定檔（API Key、端口、RTC 設定） |

### 4.2 修改檔案

| 檔案 | 修改內容 |
|------|---------|
| `backend/app/config.py` | 新增 6 個 `LIVEKIT_*` 設定項 |
| `backend/app/websocket_handler.py` | Device 上線建立 ingress、離線清理、Viewer 觀看回傳 LiveKit token |
| `backend/app/main.py` | 新增 3 個 HTTP endpoint |
| `backend/requirements.txt` | 新增 `livekit-api>=0.7.0` |
| `device-agent/agent.py` | 新增 `FFmpegRTMPPusher` 類別、處理 `livekit_ingress` 和 `livekit_switch_stream` 訊息 |
| `device-agent/Dockerfile` | 安裝 FFmpeg、改為 `COPY *.py .` |
| `web/webrtc.js` | 雙模式支援：自動偵測模式、LiveKit Room 連線、串流切換 |
| `web/index.html` | 加入 LiveKit JS SDK（CDN） |
| `docker-compose.yml` | 新增 `livekit` 和 `livekit-ingress` 服務（profile: livekit） |
| `nginx/nginx.conf` | 新增 LiveKit WebSocket 代理路由 |
| `.env.example` | 新增 LiveKit 環境變數說明 |

---

## 5. 新增 API Endpoint

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/config/streaming-mode` | 回傳 `{"mode": "p2p"}` 或 `{"mode": "livekit"}`，前端據此選擇連線方式 |
| GET | `/api/devices/{device_id}/livekit-token` | 取得 LiveKit 觀看 token（需要 JWT 認證 + 設備擁有權） |
| POST | `/api/devices/{device_id}/livekit-switch-stream` | 通知設備切換串流源（cam/cam_sub/cam2/cam2_sub），影響所有觀看者 |

---

## 6. 設定說明

### 6.1 VPS / Backend 伺服器設定

所有 LiveKit 設定都在 VPS 上的 `.env` 檔案：

```env
# ===== LiveKit 設定 =====

# 開關（true = LiveKit 模式，false = P2P 模式）
LIVEKIT_ENABLED=true

# API 認證（與 livekit/livekit.yaml 一致）
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=devsecret

# Docker 容器間內部通訊（不用改）
LIVEKIT_URL=ws://livekit:7880

# ⭐ 瀏覽器連接 LiveKit 用的公網地址（必須改）
# 範例：
#   有域名 + SSL：wss://cam.yourdomain.com:7880
#   只有 IP：     ws://203.x.x.x:7880
LIVEKIT_PUBLIC_URL=wss://你的域名:7880

# Docker 內部 RTMP 入流（不用改）
LIVEKIT_RTMP_URL=rtmp://livekit-ingress:1935/live
```

### 6.2 防火牆需開放端口

| 端口 | 協議 | 用途 |
|------|------|------|
| 7880 | TCP | LiveKit WebSocket 信令 |
| 7881 | TCP | LiveKit RTC over TCP |
| 7882 | UDP | LiveKit WebRTC 媒體 |
| 1935 | TCP | RTMP Ingress（僅 Docker 內部，可不對外開） |

### 6.3 樹莓派設定

**樹莓派不需要任何 LiveKit 相關設定。**

運作流程：
1. Pi 的 agent 連上 Backend WebSocket（跟以前一樣）
2. Backend 發現 `LIVEKIT_ENABLED=true` → 建立 ingress → 發送 `livekit_ingress` 訊息給 Pi
3. Pi 收到 RTMP URL 和 Stream Key → 自動啟動 FFmpeg 推流
4. Pi 斷線時 Backend 自動清理 ingress

Pi 上現有的設定檔完全不用動：

| 檔案 | 內容 | 是否需修改 |
|------|------|-----------|
| `/etc/mumucam/agent.env` | DEVICE_ID、BACKEND_URL、GO2RTC_HTTP | 不用改 |
| `/etc/systemd/system/mumucam-agent.service` | ONVIF、GPS 等設定 | 不用改 |

**唯一要做的事：更新 agent.py 程式碼 + 確認有裝 ffmpeg。**

---

## 7. 部署步驟

### 7.1 VPS 端部署

```bash
# 1. 更新程式碼
cd /path/to/MuCamera
git pull

# 2. 編輯 .env
#    設定 LIVEKIT_ENABLED=true
#    設定 LIVEKIT_PUBLIC_URL=wss://你的域名:7880

# 3. 開防火牆（以 ufw 為例）
sudo ufw allow 7880/tcp
sudo ufw allow 7881/tcp
sudo ufw allow 7882/udp

# 4. 啟動（含 LiveKit）
docker-compose --profile livekit up -d --build

# 5. 執行 migration（如有需要）
docker-compose exec backend alembic upgrade head

# 6. 確認服務狀態
docker-compose ps
# 應該看到 mumu-livekit 和 mumu-livekit-ingress 在運行
```

### 7.2 樹莓派端更新

```bash
# 1. SSH 進入 Pi
ssh pi@樹莓派IP

# 2. 更新程式碼
cd /opt/mumucam/device-agent
sudo git pull
# 或手動將新版 agent.py 複製過去

# 3. 確認有裝 ffmpeg
which ffmpeg || sudo apt-get install -y ffmpeg

# 4. 重啟 agent
sudo systemctl restart mumucam-agent

# 5. 看 log 確認 LiveKit 推流啟動
journalctl -u mumucam-agent -f
# 成功的話會看到：
#   [livekit] Received ingress info, starting RTMP push
#   [ffmpeg] Starting: ffmpeg -rtsp_transport tcp -i rtsp://127.0.0.1:8554/cam_sub ...
#   [ffmpeg] Started PID=xxxxx
```

---

## 8. 驗證方式

### 8.1 基本驗證

1. 開瀏覽器 → 登入系統 → 觀看攝影機
2. 打開瀏覽器 Console（F12），應該看到：
   - `[mode] Streaming mode: livekit`
   - `[livekit] Connected to room: device-xxxxx`
3. 影像正常顯示

### 8.2 多人觀看測試

1. 開多個瀏覽器分頁同時觀看同一台攝影機
2. 不應出現卡頓或重連（因為 Pi 只上傳 1 路）

### 8.3 功能驗證

| 功能 | 預期結果 |
|------|---------|
| 影像播放 | 正常顯示 |
| 多人同時觀看 | 不卡頓 |
| PTZ 雲台控制 | 正常（走 WebSocket） |
| 攝影機切換（cam/cam2） | 正常（透過 switch-stream API） |
| 畫質切換（SD/HD） | 正常（透過 switch-stream API） |
| GPS 定位 | 正常（走 WebSocket） |
| 錄影回放 | 正常（走 WebSocket proxy） |
| 設備斷線重連 | FFmpeg 自動重新推流 |

### 8.4 回退測試

```bash
# 改 .env: LIVEKIT_ENABLED=false
# 重啟 backend
docker-compose restart backend
# 確認 P2P 模式正常運作
```

---

## 9. 效能比較

| 項目 | P2P 模式 | LiveKit 模式 |
|------|---------|-------------|
| Pi 上傳頻寬 | N x 2Mbps（N=觀看人數） | 固定 ~2Mbps |
| Server CPU | 幾乎為 0 | 低（SFU 只轉發不轉碼） |
| Server 下行頻寬 | 幾乎為 0 | N x 2Mbps |
| 延遲 | 最低（P2P 直連） | +50~100ms |
| 最大同時觀看 | 受 Pi 4G 頻寬限制（~2人） | 受 Server 頻寬限制（數十人） |

---

## 10. 已知限制

1. **單一 ingress**：所有觀看者看同一個串流。切換攝影機/畫質會影響所有觀看者
2. **RTMP ingress 轉碼**：LiveKit Ingress 端會做 RTMP → WebRTC 轉碼，Server 建議 4 核以上
3. **延遲略增**：比 P2P 直連多 50~100ms（經過 Server 中轉）

---

## 11. 回退方案

隨時可以回到 P2P 模式：

```bash
# 1. 修改 .env
LIVEKIT_ENABLED=false

# 2. 重啟 backend
docker-compose restart backend

# 3. Pi 端不用動（收不到 livekit_ingress 訊息就不會啟動 FFmpeg）
```

---

## 12. 檔案結構

```
MuCamera/
├── .env.example                          # 環境變數範本（含 LiveKit 設定）
├── docker-compose.yml                    # 新增 livekit + livekit-ingress 服務
├── livekit/
│   └── livekit.yaml                      # [新建] LiveKit Server 設定
├── nginx/
│   └── nginx.conf                        # 新增 LiveKit 代理路由
├── backend/
│   ├── requirements.txt                  # 新增 livekit-api
│   └── app/
│       ├── config.py                     # 新增 LIVEKIT_* 設定
│       ├── livekit_service.py            # [新建] Token/Ingress 管理
│       ├── websocket_handler.py          # LiveKit 分支邏輯
│       └── main.py                       # 新增 3 個 endpoint
├── device-agent/
│   ├── Dockerfile                        # 安裝 FFmpeg
│   └── agent.py                          # FFmpegRTMPPusher + 訊息處理
└── web/
    ├── index.html                        # 加入 LiveKit SDK
    └── webrtc.js                         # 雙模式支援
```
