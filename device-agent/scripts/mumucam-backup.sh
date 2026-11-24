#!/bin/bash
# Pi 系統備份（低優先權執行）：
#   1) 設定包 config-*.tar.gz（幾 MB，含密碼/金鑰，請妥善保存）
#   2) 完整系統 rootfs-*.tar.zst（不含錄影與暫存）
# 還原：燒一張新的 Raspberry Pi OS → 解開 rootfs 覆蓋 → 重開機
set -e
MNT=${BACKUP_MNT:-/mnt/usb}
DEST=$MNT/backup
# 備份目標 USB 的 UUID（見 /etc/fstab）；換 USB 時用 BACKUP_USB_UUID 覆寫
USB_UUID=${BACKUP_USB_UUID:-8d9fb24d-666c-4067-81e3-9bfb049c430e}
MARGIN_KB=$((2 * 1024 * 1024))   # 額外保留 2G
STAMP=$(date +%Y%m%d-%H%M)

die() { echo "[backup] 中止：$*" >&2; exit 1; }

# 1) /mnt/usb 必須是掛載點。fstab 有 nofail，USB 沒掛上時 /mnt/usb 只是 SD 卡上的空目錄，
#    此時寫入會把系統碟塞滿，所以一律不跑。
findmnt --mountpoint "$MNT" >/dev/null || die "$MNT 沒有掛載 USB"

# 2) 必須是指定的那顆 USB，且不能和系統碟同一個裝置
SRC=$(findmnt -n -o SOURCE --mountpoint "$MNT")
UUID=$(findmnt -n -o UUID --mountpoint "$MNT")
ROOT_SRC=$(findmnt -n -o SOURCE /)
[ "$UUID" = "$USB_UUID" ] || die "$MNT 掛的是 $SRC（UUID=$UUID），不是預期的 USB（$USB_UUID）"
[ "$SRC" != "$ROOT_SRC" ] || die "$MNT 與系統碟是同一個裝置（$SRC）"

# 3) 剩餘空間需大於系統已用空間 + 緩衝（壓縮後實際會小很多，這裡取保守值）
ROOT_USED_KB=$(df --output=used -k / | tail -1 | tr -d ' ')
AVAIL_KB=$(df --output=avail -k "$MNT" | tail -1 | tr -d ' ')
[ "$AVAIL_KB" -gt $((ROOT_USED_KB + MARGIN_KB)) ] || \
    die "USB 剩餘 $((AVAIL_KB / 1024))M，不足 $(((ROOT_USED_KB + MARGIN_KB) / 1024))M"

echo "[backup] 目標 $SRC（$MNT，剩餘 $((AVAIL_KB / 1024 / 1024))G）"
mkdir -p "$DEST"
# 再確認一次：備份目錄確實落在 USB 檔案系統上
[ "$(findmnt -n -o TARGET -T "$DEST")" = "$MNT" ] || die "$DEST 不在 $MNT 檔案系統上"
cd /

echo "[backup] config bundle"
tar -czf "$DEST/config-$STAMP.tar.gz" --ignore-failed-read \
    --exclude='opt/mumucam/venv' --exclude='*/__pycache__' --exclude='opt/mumucam/recordings' \
    opt/mumucam etc/mumucam home/dive/go2rtc \
    etc/systemd/system etc/NetworkManager/system-connections etc/dnsmasq.d etc/dnsmasq.conf \
    etc/udev/rules.d etc/sysctl.d etc/iptables etc/hostname etc/hosts etc/fstab \
    usr/local/sbin usr/local/bin var/lib/mumucam boot/firmware/config.txt boot/firmware/cmdline.txt \
    2>/dev/null || true
ls -la "$DEST/config-$STAMP.tar.gz"

echo "[backup] full rootfs"
tar -cf - --one-file-system --numeric-owner --ignore-failed-read \
    --exclude=./proc --exclude=./sys --exclude=./dev --exclude=./run --exclude=./tmp \
    --exclude=./mnt --exclude=./media --exclude=./lost+found --exclude=./swapfile \
    --exclude=./var/swap --exclude=./var/cache/apt/archives --exclude=./var/tmp \
    --exclude='./home/*/.cache' --exclude='./root/.cache' --exclude='./var/lib/docker/overlay2' \
    . 2>/dev/null | zstd -T2 -3 -q -o "$DEST/rootfs-$STAMP.tar.zst"
tar -C /boot/firmware -czf "$DEST/bootfs-$STAMP.tar.gz" . 2>/dev/null || true

# 只保留最近 2 份
ls -1t "$DEST"/rootfs-*.tar.zst | tail -n +3 | xargs -r rm -f
ls -1t "$DEST"/bootfs-*.tar.gz | tail -n +3 | xargs -r rm -f
ls -1t "$DEST"/config-*.tar.gz | tail -n +6 | xargs -r rm -f
ls -la "$DEST"
echo "[backup] done"
