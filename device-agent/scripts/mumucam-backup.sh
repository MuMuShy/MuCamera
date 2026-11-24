#!/bin/bash
# Pi 系統備份（低優先權執行）：
#   1) 設定包 config-*.tar.gz（幾 MB，含密碼/金鑰，請妥善保存）
#   2) 完整系統 rootfs-*.tar.zst（不含錄影與暫存）
# 還原：燒一張新的 Raspberry Pi OS → 解開 rootfs 覆蓋 → 重開機
set -e
DEST=/mnt/usb/backup
STAMP=$(date +%Y%m%d-%H%M)
mkdir -p "$DEST"
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
