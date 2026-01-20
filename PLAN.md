# 錄影與回放系統實作計畫

## 概述

根據 `docs/recording_system.md` 規劃書，實作樹莓派端的錄影與回放系統。

## 架構設計

```
┌─────────────────────────────────────────────────────────────────┐
│                        Device Agent (Pi)                         │
├─────────────────────────────────────────────────────────────────┤
│  go2rtc ──RTSP──> ffmpeg (recorder) ──> .ts files               │
│                                              │                   │
│  indexer.py ─────────────────> recordings.db (SQLite)           │
│                                              │                   │
│  cleaner.py ─────────────────> 刪除過期/超量檔案                 │
│                                              │                   │
│  local_api (FastAPI) ────────> HLS 轉換 + 檔案索引 API          │
└─────────────────────────────────────────────────────────────────┘
                                   │
                                   │ sync metadata via heartbeat
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Backend Server                           │
├─────────────────────────────────────────────────────────────────┤
│  recordings API ─────────────> 索引查詢、權限檢查                │
│  proxy to device local_api ──> HLS 串流 (不存影片流量)          │
└─────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (Web)                           │
├─────────────────────────────────────────────────────────────────┤
│  recordings.js ──────────────> 錄影列表、時間篩選               │
│  HLS.js player ──────────────> 回放播放器                       │
└─────────────────────────────────────────────────────────────────┘
```

## 實作項目

### Phase 1: Device Agent 錄影模組

#### 1.1 建立 `device-agent/recorder.py`
```python
# FFmpeg 分段錄影模組
# - 從 go2rtc RTSP 拉流 (rtsp://127.0.0.1:8554/cam)
# - 使用 segment muxer 輸出 .ts 檔案
# - 檔名格式: YYYYmmdd_HHMMSS.ts
# - 分段時間可配置 (預設 5 分鐘)
# - 不轉碼 (-c copy)
```

**主要功能:**
- `RecorderManager` class
- `start()` - 啟動 ffmpeg subprocess
- `stop()` - 優雅停止錄影
- `is_running()` - 檢查狀態
- 自動重啟機制

**FFmpeg 命令:**
```bash
ffmpeg -rtsp_transport tcp \
  -i rtsp://127.0.0.1:8554/cam \
  -c copy \
  -f segment \
  -segment_time 300 \
  -segment_format mpegts \
  -strftime 1 \
  -reset_timestamps 1 \
  /opt/mumucam/recordings/cam/%Y%m%d_%H%M%S.ts
```

#### 1.2 建立 `device-agent/cleaner.py`
```python
# 雙條件清理模組
# 條件 A (優先): 容量超過 MAX_GIB → 刪除最舊檔案直到 TARGET_GIB
# 條件 B (次要): 容量未爆 → 依 RETENTION_DAYS 刪除過期檔案
```

**配置參數:**
- `MAX_GIB=50` - 容量上限
- `TARGET_GIB=40` - 安全線
- `RETENTION_DAYS=7` - 保留天數

#### 1.3 建立 `device-agent/indexer.py`
```python
# 錄影索引模組
# - 監控錄影目錄
# - 解析檔名取得時間戳
# - 儲存到 SQLite (recordings.db)
```

**SQLite Schema:**
```sql
CREATE TABLE recordings (
    id INTEGER PRIMARY KEY,
    filename TEXT UNIQUE NOT NULL,
    start_time DATETIME NOT NULL,
    duration_seconds INTEGER,
    file_size_bytes INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_start_time ON recordings(start_time);
```

#### 1.4 建立 `device-agent/local_api/server.py`
```python
# 本地 API 服務 (FastAPI)
# - GET /recordings - 列出錄影檔案
# - GET /recordings/{filename}/hls/playlist.m3u8 - HLS playlist
# - GET /recordings/{filename}/hls/{segment}.ts - HLS segment
# - GET /recordings/{filename}/download - 直接下載 .ts
```

**HLS 轉換策略:**
- 使用 ffmpeg 即時轉換 (on-demand)
- 快取 m3u8 和 segments 到 /tmp
- 設定 TTL 自動清理快取

### Phase 2: systemd 服務

#### 2.1 `mumucam-recorder.service`
```ini
[Unit]
Description=MuMuCam Recorder Service
After=mumucam-go2rtc.service
Requires=mumucam-go2rtc.service

[Service]
Type=simple
User=mumucam
ExecStart=/opt/mumucam/venv/bin/python /opt/mumucam/device-agent/recorder.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### 2.2 `mumucam-cleanup.service` + `mumucam-cleanup.timer`
```ini
# mumucam-cleanup.service
[Unit]
Description=MuMuCam Recording Cleanup

[Service]
Type=oneshot
User=mumucam
ExecStart=/opt/mumucam/venv/bin/python /opt/mumucam/device-agent/cleaner.py

# mumucam-cleanup.timer
[Unit]
Description=Run MuMuCam cleanup every hour

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

#### 2.3 `mumucam-playback.service`
```ini
[Unit]
Description=MuMuCam Playback API Service
After=network.target

[Service]
Type=simple
User=mumucam
ExecStart=/opt/mumucam/venv/bin/python -m uvicorn local_api.server:app --host 127.0.0.1 --port 8090
WorkingDirectory=/opt/mumucam/device-agent
Restart=always

[Install]
WantedBy=multi-user.target
```

### Phase 3: Backend API

#### 3.1 新增資料庫模型 `backend/app/models/recording.py`
```python
class Recording(Base):
    __tablename__ = "recordings"

    id = Column(Integer, primary_key=True)
    device_id = Column(String, ForeignKey("devices.id"), nullable=False)
    filename = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime)
    duration_seconds = Column(Integer)
    file_size_bytes = Column(BigInteger)
    status = Column(String, default="available")  # available, deleted, corrupted
    created_at = Column(DateTime, default=datetime.utcnow)
```

#### 3.2 新增 API 路由 `backend/app/routers/recordings.py`
```python
# GET /api/devices/{device_id}/recordings
# - 查詢參數: start_date, end_date, page, limit
# - 回傳錄影列表 (從 device local_api 同步)

# GET /api/devices/{device_id}/recordings/{filename}/stream
# - Proxy 到 device local_api 的 HLS endpoint

# GET /api/devices/{device_id}/recordings/{filename}/download
# - Proxy 到 device local_api 的下載 endpoint
```

### Phase 4: Frontend 回放頁面

#### 4.1 新增 `web/recordings.html`
- 錄影列表頁面
- 日期範圍篩選器
- 錄影卡片列表 (顯示時間、時長、大小)
- 點擊進入播放模式

#### 4.2 新增 `web/recordings.js`
```javascript
// 錄影列表管理
// - fetchRecordings(deviceId, startDate, endDate)
// - renderRecordingsList(recordings)
// - initHLSPlayer(videoElement, hlsUrl)
// - 使用 hls.js 播放 HLS 串流
```

#### 4.3 更新 `web/styles.css`
- 錄影列表樣式
- 播放器控制樣式
- 日期選擇器樣式

#### 4.4 更新 `web/index.html`
- 側邊欄「錄影回放」連結功能化

## 檔案結構

```
device-agent/
├── recorder.py          # [新增] FFmpeg 錄影模組
├── cleaner.py           # [新增] 清理模組
├── indexer.py           # [新增] 索引模組
├── local_api/
│   ├── __init__.py
│   └── server.py        # [新增] 本地回放 API
└── agent.py             # [修改] 整合錄影狀態到 heartbeat

backend/app/
├── models/
│   └── recording.py     # [新增] Recording model
├── routers/
│   └── recordings.py    # [新增] 錄影 API
└── main.py              # [修改] 註冊新路由

web/
├── recordings.html      # [新增] 錄影回放頁面
├── recordings.js        # [新增] 錄影功能 JS
├── styles.css           # [修改] 新增錄影相關樣式
└── index.html           # [修改] 側邊欄連結

systemd/
├── mumucam-recorder.service   # [新增]
├── mumucam-cleanup.service    # [新增]
├── mumucam-cleanup.timer      # [新增]
└── mumucam-playback.service   # [新增]
```

## 實作順序

1. **Device Agent 核心模組** (先在本地測試)
   - recorder.py
   - cleaner.py
   - indexer.py

2. **Device Agent Local API**
   - local_api/server.py
   - HLS 轉換功能

3. **systemd 服務檔案**
   - 建立所有服務檔案
   - 測試啟動順序

4. **Backend API**
   - Recording model + migration
   - recordings API router

5. **Frontend 回放頁面**
   - recordings.html + recordings.js
   - 整合 hls.js

6. **整合測試**
   - 端對端測試錄影→回放流程
   - 清理策略驗證

## 配置參數 (環境變數)

```bash
# Device Agent
RECORDING_ENABLED=true
RECORDING_SEGMENT_SECONDS=300
RECORDING_DIR=/opt/mumucam/recordings/cam
GO2RTC_RTSP_URL=rtsp://127.0.0.1:8554/cam

# Cleaner
CLEANUP_MAX_GIB=50
CLEANUP_TARGET_GIB=40
CLEANUP_RETENTION_DAYS=7

# Local API
LOCAL_API_PORT=8090
```

## 注意事項

1. **錄影不能影響直播** - ffmpeg 從 go2rtc 拉流，不直接連相機
2. **抗斷電** - 使用 .ts 格式，每個分段獨立完整
3. **不轉碼** - `-c copy` 保持原始品質，省 CPU
4. **權限** - 所有服務使用 mumucam 使用者
5. **HLS 轉換** - 按需轉換，不預先轉換所有檔案
