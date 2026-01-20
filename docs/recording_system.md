錄影與回放系統實作

你是一位 Linux / Video Streaming / IoT Edge System 架構工程師，請協助我設計並實作一套 樹莓派端的錄影與回放系統，重點是 穩定、省效能、可長期無人值守運作。

專案背景

裝置：Raspberry Pi（長時間運作）

攝影機：IP Camera（RTSP，HEVC/H.265，4K 15fps）

串流中介：go2rtc

錄影工具：ffmpeg

服務管理：systemd

執行使用者：mumucam（系統帳號，非登入用）

專案根目錄（我們要統一收斂）：/opt/mumucam

目前架構重點：

相機 RTSP 只允許拉一次

go2rtc 負責單一拉流與分流（直播 + 錄影）

ffmpeg 錄影 必須從 go2rtc 拉（rtsp://127.0.0.1:8554/cam）

錄影 不轉碼（-c copy）

直播不能被錄影影響

已確認現況（重要）
go2rtc 目前仍在舊路徑（尚未搬到 /opt）

go2rtc 執行檔（目前在這）：

/home/dive/go2rtc/go2rtc


go2rtc 設定檔（目前在這）：

/home/dive/go2rtc/go2rtc.yaml


目前 go2rtc.yaml 內容（摘要）：

streams 名稱：cam
-來源：rtsp://admin:***@10.10.0.48:554/Streaming/Channels/102

api listen：:1984

webrtc listen：:8555

go2rtc RTSP 出口已驗證可用

ffprobe 可成功讀取：

rtsp://127.0.0.1:8554/cam


串流資訊：

Video: HEVC (Main) 3840x2512 15fps

Audio: pcm_mulaw 8000Hz mono

目前沒有重複拉流

ss -ntp | grep "10.10.0.48:554" 只有 1 條 ESTAB

我要你幫我完成的內容（一步一步）
1️⃣ 錄影儲存架構（收斂到 /opt/mumucam）

使用 ffmpeg 分段錄影（建議 .ts，抗斷電）

分段時間可配置（預設 5 分鐘）

錄影目錄（統一放這）：

/opt/mumucam/recordings/cam/


檔名使用時間戳（YYYYmmdd_HHMMSS）

2️⃣ systemd 服務（全部用 mumucam）

錄影服務：

開機自動啟動

crash 自動重啟

使用 mumucam 使用者

ffmpeg 的 input 必須是：

rtsp://127.0.0.1:8554/cam


不允許直接拉相機 RTSP（避免第二路拉流）

清理服務：

使用 systemd timer 定期執行

不影響錄影進程

3️⃣ 雙條件清理策略（容量優先）

請實作 容量優先 的清理邏輯：

條件 A（優先）：

若錄影資料夾 超過容量上限（MAX_GIB）

直接刪除「最舊檔案」

一直刪到低於安全線（TARGET_GIB）

完全無視保留天數

條件 B（次要）：

若容量未爆

再依「保留天數（RETENTION_DAYS）」刪除過期檔案

清理邏輯必須：

安全

可重複執行

不會誤刪新檔

適合長期無人值守設備

回放架構需求 (可先產生md規劃檔案 後續實做)

請幫我規劃「回放系統」的架構方向：

Pi 本地儲存錄影檔

Backend 只負責：

索引（時間 / camera / 檔案）

權限判斷

不要讓 backend 轉發影片流量

建議方案：

TS → HLS（m3u8）即時或延後轉換

或提供下載用 MP4 轉檔流程

請說明：

回放 vs 下載 的差異

什麼時候該用 HLS

什麼時候只提供檔案下載