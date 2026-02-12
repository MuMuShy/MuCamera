# USB 錄影儲存設定指南

## 適用環境

- 裝置：Raspberry Pi (diveguide / pi-cam-002)
- USB：ADATA 58G USB 隨身碟
- 掛載點：`/mnt/usb`
- 錄影路徑：`/mnt/usb/recordings/cam`
- 服務執行使用者：`mumucam`

## 1. 檢查 USB 狀態

```bash
lsblk          # 列出所有 block devices
lsblk -f       # 含檔案系統類型、UUID
dmesg | tail   # kernel 偵測訊息
```

## 2. 格式化為 ext4

```bash
# 確認 USB 未掛載
sudo umount /dev/sda1

# 格式化（-J size=16 縮小 journal，避免 USB 供電不足導致中斷）
sudo mkfs.ext4 -L mumucam -J size=16 /dev/sda1

# 驗證
sudo blkid -p /dev/sda1
# 應顯示 TYPE="ext4"
```

> 注意：如果格式化過程中 SSH 斷線，可能是 USB 供電不足，建議使用有外接電源的 USB Hub。

## 3. 掛載與 fstab 設定

```bash
sudo mkdir -p /mnt/usb
sudo mount /dev/sda1 /mnt/usb
```

`/etc/fstab` 加入：

```
UUID=8d9fb24d-666c-4067-81e3-9bfb049c430e  /mnt/usb  ext4  defaults,noatime,nofail  0  2
```

參數說明：
- `noatime`：不更新存取時間，減少寫入，延長 USB 壽命
- `nofail`：USB 不在時系統仍能正常開機

```bash
sudo systemctl daemon-reload
sudo umount /mnt/usb
sudo mount -a       # 測試 fstab
df -h /mnt/usb      # 確認掛載成功
```

## 4. 目錄與權限

```bash
sudo mkdir -p /mnt/usb/recordings/cam
sudo chown -R mumucam:mumucam /mnt/usb/recordings
```

## 5. 修改服務設定

### 環境檔 `/etc/mumucam/agent.env`

```ini
DEVICE_ID=pi-cam-002
BACKEND_URL=wss://camera.ihousing.tw/ws/device
GO2RTC_HTTP=http://127.0.0.1:1984
RECORDING_DIR=/mnt/usb/recordings/cam
CLEANUP_MAX_GIB=45
CLEANUP_TARGET_GIB=38
```

### Systemd service 檔案

三個 service 都有寫死 `RECORDING_DIR`，需要一起改（`agent.env` 會被 service 裡的 `Environment=` 覆蓋）：

```bash
# mumucam-recorder.service
Environment="RECORDING_DIR=/mnt/usb/recordings/cam"

# mumucam-playback.service
Environment="RECORDING_DIR=/mnt/usb/recordings/cam"

# mumucam-cleanup.service
Environment="RECORDING_DIR=/mnt/usb/recordings/cam"
Environment="CLEANUP_MAX_GIB=45"
Environment="CLEANUP_TARGET_GIB=38"
Environment="CLEANUP_RETENTION_DAYS=7"
```

修改指令：

```bash
sudo sed -i 's|RECORDING_DIR=/opt/mumucam/recordings/cam|RECORDING_DIR=/mnt/usb/recordings/cam|' \
  /etc/systemd/system/mumucam-recorder.service \
  /etc/systemd/system/mumucam-playback.service \
  /etc/systemd/system/mumucam-cleanup.service
```

## 6. 重啟服務

```bash
sudo systemctl daemon-reload
sudo systemctl restart mumucam-recorder
sudo systemctl restart mumucam-playback
sudo systemctl restart mumucam-cleanup.timer

sudo systemctl status mumucam-recorder --no-pager
sudo systemctl status mumucam-playback --no-pager
```

## 7. 驗證

```bash
# 確認錄影寫入 USB
ls -la /mnt/usb/recordings/cam/

# 確認磁碟使用量
df -h /mnt/usb

# 測試回放 API
curl http://localhost:8090/recordings
```

## 8. USB 健康監控

```bash
# 檢查檔案系統錯誤（需先 umount）
sudo umount /mnt/usb
sudo fsck.ext4 -n /dev/sda1
sudo mount /mnt/usb

# I/O 錯誤檢查
dmesg | grep -i "error\|fail" | grep -i "sd"
```

## 常見問題

| 問題 | 原因 | 解決 |
|------|------|------|
| Permission denied | 服務用 `mumucam` 使用者，目錄權限不對 | `sudo chown -R mumucam:mumucam /mnt/usb/recordings` |
| mkfs 中斷/SSH 斷線 | USB 供電不足 | 用 `-J size=16` 或有源 USB Hub |
| 開機掛載失敗 | fstab UUID 錯誤 | `sudo blkid -p /dev/sda1` 取正確 UUID |
| 開機卡住 | fstab 少了 `nofail` | 用備份 `sudo cp /etc/fstab.bak /etc/fstab` |
| agent.env 設定沒生效 | service 裡 `Environment=` 覆蓋了 | 需同步改 service 檔案 |

## 原始設定（改動前）

- 錄影路徑：`/opt/mumucam/recordings/cam`
- Cleanup：MAX=35G, TARGET=30G
- 變更日期：2026-02-12
