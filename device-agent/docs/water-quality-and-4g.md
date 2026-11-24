# 水質監測站串接 & 4G 斷線救援（diveguide / pi-cam-002）

## 現場網路

| 介面 | 位址 | 用途 |
|---|---|---|
| usb1 | DHCP（SIM7600 RNDIS，閘道 192.168.225.1） | 4G 對外 |
| eth0 | 10.10.0.1/24 | 攝影機 1（10.10.0.48） |
| eth2（USB 網卡 00:e0:4c:68:01:d5） | 192.168.0.100/24 | 攝影機 2（192.168.0.123） |
| eth1（USB 網卡 00:e0:4c:68:0e:32） | 192.168.0.1/32 | 水質監測站 JNC CB（192.168.0.88） |
| wlan0 | 10.42.0.1/24 | 熱點 |
| tailscale0 | 100.103.193.75 | 遠端維護 |

CB 出廠設定為 192.168.0.88/24、閘道 192.168.0.1，與攝影機 2 同網段。
Pi 在 eth1 只掛 `192.168.0.1/32` 並加一條 `192.168.0.88/32` 專用路由，
其餘 192.168.0.0/24 仍走 eth2，因此 CB 不需改設定。

### eth1 連線（NetworkManager，依網卡 MAC 綁定）

```bash
sudo nmcli con add type ethernet con-name WaterQuality ifname eth1 \
  ipv4.method manual ipv4.addresses 192.168.0.1/32 \
  ipv4.routes "192.168.0.88/32 src=192.168.0.1" \
  ipv4.never-default yes ipv6.method disabled \
  connection.autoconnect yes connection.autoconnect-priority 10
sudo nmcli con mod WaterQuality connection.interface-name "" \
  802-3-ethernet.mac-address 00:E0:4C:68:0E:32
```

### 服務

| 檔案 | 安裝位置 | 說明 |
|---|---|---|
| `scripts/mumucam-wq-net.sh` | `/usr/local/sbin/` | CB 上網 NAT（192.168.0.88 → usb+） |
| `systemd/mumucam-wq-net.service` | `/etc/systemd/system/` | 開機套用上面的 NAT |
| `systemd/wq-proxy.service` | `/etc/systemd/system/` | CB 網頁代理：`http://<Pi>:8088` |
| `scripts/mumucam-lte-watchdog.sh` | `/usr/local/sbin/` | 4G 斷線救援 |
| `systemd/mumucam-lte-watchdog.service` | `/etc/systemd/system/` | 常駐執行 watchdog |
| `scripts/mumucam-backup.sh` | `/usr/local/sbin/` | 系統備份到 `/mnt/usb/backup`（低優先權） |

另外需要 `/etc/sysctl.d/90-mumucam-forward.conf`：`net.ipv4.ip_forward=1`。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mumucam-wq-net mumucam-lte-watchdog wq-proxy
```

## 水質資料流

CB 網頁 `get_ChName.csv` / `get_ChValue.csv`
→ `water_quality.py`（agent 內每 60 秒讀取，只做 GET）
→ `system_info` 訊息中的 `water_quality`、`lte` 欄位
→ 後端存 Redis `device:sysinfo:<id>`（TTL 30 秒）
→ 網頁：觀看頁右側「水質監測」、系統設定頁「水質監測站 / 4G 連線」卡片。

CB 也透過 4G 自行連到 JNC SCADA（59.125.238.217:12345），與本系統無關。

## 4G 斷線救援（mumucam-lte-watchdog）

每 30 秒 ping 1.1.1.1 / 8.8.8.8 / 168.95.1.1（經 4G 網卡）：

| 斷線時間 | 動作 |
|---|---|
| 約 2 分鐘 | `nmcli con up` 重新啟用 4G 連線 |
| 約 5 分鐘 | 對 AT 埠送 `AT+CFUN=1,1` 重置模組（之後每 10 分鐘可再一次） |
| 約 20 分鐘 | 重開 Pi（開機 30 分鐘內不重開，每 6 小時最多一次） |

- 狀態檔：`/run/mumucam/lte-status`（含訊號 CSQ / CPSI）
- 紀錄：`journalctl -u mumucam-lte-watchdog`
- 模擬測試（不執行真正動作）：
  `LTE_WD_DRYRUN=1 LTE_WD_HOSTS=192.0.2.1 LTE_WD_SLEEP=0 LTE_WD_DIR=/tmp/lte-test bash mumucam-lte-watchdog.sh`

> 舊的 `net-watchdog`（`/usr/local/bin/net_watchdog.sh`）偵測的是 `wwan0`，
> 本機 4G 為 RNDIS `usb1`，該腳本實際上不會動作。

## USB 掛載保護（避免寫爆系統碟）

fstab 的 `/mnt/usb`（UUID 8d9fb24d-…）設有 `nofail`：USB 掛不上時系統照常開機，
但 `/mnt/usb` 只是 SD 卡上的空目錄。三層保護：

1. **錄影相關服務**（recorder ×2、playback、cleanup ×2）加 drop-in
   `systemd/require-usb.conf` → `/etc/systemd/system/<服務>.service.d/`：
   USB 沒掛就不啟動，掛載消失就立即停止。
2. **SD 卡上的 `/mnt/usb` 目錄設為 `chattr +i`**：USB 沒掛時任何程式都寫不進去
   （設定方式見 `require-usb.conf` 註解）。
3. **備份腳本**執行前檢查 `findmnt --mountpoint /mnt/usb`、USB UUID、剩餘空間，任一不符即中止。

USB 重新插上後：

```bash
sudo mount /mnt/usb
sudo systemctl start mumucam-recorder mumucam-recorder-cam2 mumucam-playback
```

## 遠端維護入口（Tailscale）

| 項目 | 位址 |
|---|---|
| SSH | `ssh dive@100.103.193.75` |
| 攝影機 1 / 2 後台 | `:8048` / `:8123` |
| 水質監測站後台 | `:8088` |
