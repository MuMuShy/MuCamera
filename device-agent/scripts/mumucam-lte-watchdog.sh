#!/bin/bash
# 4G（SIM7600, RNDIS 模式）斷線自動救援
#   斷線 ~2 分鐘  -> 重新啟用 4G 連線（DHCP 重新取得）
#   斷線 ~5 分鐘  -> 送 AT+CFUN=1,1 重置 4G 模組（之後每 10 分鐘可再重置一次）
#   斷線 ~20 分鐘 -> 重開 Pi（每 6 小時最多一次，避免沒訊號時無限重開）
# 狀態寫在 /run/mumucam/lte-status（給遠端觀測用），紀錄看 journalctl -u mumucam-lte-watchdog

INTERVAL=30
CHECK_HOSTS=(1.1.1.1 8.8.8.8 168.95.1.1)
STATE_DIR=/var/lib/mumucam
STATUS=/run/mumucam/lte-status
REBOOT_STAMP=$STATE_DIR/lte-last-reboot
REBOOT_MIN_GAP=$((6 * 3600))
UPTIME_MIN_FOR_REBOOT=$((30 * 60))

# 測試用（模擬斷線、不真的執行動作）：
#   LTE_WD_DRYRUN=1 LTE_WD_HOSTS=192.0.2.1 LTE_WD_SLEEP=0.1 LTE_WD_DIR=/tmp/lte-test bash mumucam-lte-watchdog.sh
DRYRUN=${LTE_WD_DRYRUN:-0}
SLEEP=${LTE_WD_SLEEP:-$INTERVAL}
[ -n "${LTE_WD_HOSTS:-}" ] && read -r -a CHECK_HOSTS <<< "$LTE_WD_HOSTS"
if [ -n "${LTE_WD_DIR:-}" ]; then
    STATE_DIR=$LTE_WD_DIR; STATUS=$LTE_WD_DIR/lte-status; REBOOT_STAMP=$LTE_WD_DIR/lte-last-reboot
    UPTIME_MIN_FOR_REBOOT=0
fi

mkdir -p "$STATE_DIR" "$(dirname "$STATUS")"
log() { echo "[lte-watchdog] $*"; }
act() { if [ "$DRYRUN" = 1 ]; then log "DRYRUN: $*"; else "$@"; fi; }

lte_iface() {   # 4G 網卡名稱（rndis_host 驅動），名稱變動也找得到
    for d in /sys/class/net/*; do
        [ "$(basename "$(readlink -f "$d/device/driver" 2>/dev/null)")" = rndis_host ] && { basename "$d"; return; }
    done
}

at_port() {     # SIM7600 AT 指令埠：RNDIS 模式(9011)下為 USB 介面 x.4（GPS NMEA 是 x.3）
    for t in /sys/class/tty/ttyUSB*; do
        readlink -f "$t/device" | grep -qE ':1\.4/ttyUSB[0-9]+$' && { echo "/dev/$(basename "$t")"; return; }
    done
}

at_query() {    # 送 AT 查詢並回傳結果行（例：at_query AT+CSQ）
    local p; p=$(at_port); [ -z "$p" ] && return 1
    stty -F "$p" 115200 raw -echo 2>/dev/null
    ( sleep 0.2; printf '%s\r' "$1" > "$p" ) &
    timeout 2 cat "$p" 2>/dev/null | tr -d '\r' | grep -E '^\+' | head -1
}

SIGNAL_INFO=unknown
SIGNAL_TS=0
refresh_signal() {   # 每 5 分鐘更新一次訊號資訊
    local now; now=$(date +%s)
    [ $((now - SIGNAL_TS)) -lt 300 ] && return
    SIGNAL_TS=$now
    SIGNAL_INFO="$(at_query AT+CSQ) | $(at_query AT+CPSI?)"
}

has_internet() {
    local ifc=$1 h
    for h in "${CHECK_HOSTS[@]}"; do
        ping -I "$ifc" -c 1 -W 3 "$h" >/dev/null 2>&1 && return 0
    done
    return 1
}

modem_reset() {
    local p; p=$(at_port)
    if [ -z "$p" ]; then log "找不到 AT 埠，無法重置模組"; return 1; fi
    log "重置 4G 模組（AT+CFUN=1,1 -> $p）"
    if [ "$DRYRUN" = 1 ]; then log "DRYRUN: AT+CFUN=1,1 > $p"; return 0; fi
    printf 'AT+CFUN=1,1\r' > "$p" 2>/dev/null
}

write_status() {
    local ifc=$1 state=$2 fail=$3
    refresh_signal
    printf 'time=%s\nstate=%s\niface=%s\nip=%s\nfail_checks=%s\nlast_ok=%s\nmodem_usb=%s\nsignal=%s\n' \
        "$(date -Is)" "$state" "${ifc:-none}" \
        "$(ip -4 -o addr show "${ifc:-lo}" 2>/dev/null | awk '{print $4}')" \
        "$fail" "$LAST_OK" "$(lsusb -d 1e0e: >/dev/null 2>&1 && echo present || echo missing)" \
        "$SIGNAL_INFO" > "$STATUS.tmp"
    mv "$STATUS.tmp" "$STATUS"
}

FAIL=0
LAST_OK=never
LAST_MODEM_RESET=0
log "啟動"
while true; do
    IFC=$(lte_iface)
    if [ -n "$IFC" ] && has_internet "$IFC"; then
        [ "$FAIL" -gt 0 ] && log "4G 恢復（斷線約 $((FAIL * INTERVAL)) 秒）"
        FAIL=0
        LAST_OK=$(date -Is)
        write_status "$IFC" online 0
    else
        FAIL=$((FAIL + 1))
        DOWN=$((FAIL * INTERVAL))
        write_status "$IFC" offline "$FAIL"
        [ $((FAIL % 4)) -eq 1 ] && log "4G 無法上網（iface=${IFC:-無}），已持續 ${DOWN}s"
        NOW=$(date +%s)

        if [ "$FAIL" -eq 4 ]; then
            CONN=$(nmcli -t -f NAME,DEVICE con show --active | awk -F: -v d="$IFC" '$2==d{print $1}')
            CONN=${CONN:-$(nmcli -t -f NAME,DEVICE con show | awk -F: '$2=="usb1"||$2=="usb0"{print $1; exit}')}
            log "重新啟用 4G 連線：${CONN:-（找不到連線，改用 nmcli device connect）}"
            if [ -n "$CONN" ]; then act nmcli con up "$CONN" >/dev/null 2>&1; else act nmcli dev connect "${IFC:-usb1}" >/dev/null 2>&1; fi
        fi

        if [ "$DOWN" -ge 300 ] && [ $((NOW - LAST_MODEM_RESET)) -ge 600 ]; then
            modem_reset
            LAST_MODEM_RESET=$NOW
        fi

        if [ "$DOWN" -ge 1200 ]; then
            UP=$(cut -d. -f1 /proc/uptime)
            LAST_RB=$(cat "$REBOOT_STAMP" 2>/dev/null || echo 0)
            if [ "$UP" -ge "$UPTIME_MIN_FOR_REBOOT" ] && [ $((NOW - LAST_RB)) -ge "$REBOOT_MIN_GAP" ]; then
                log "4G 斷線超過 20 分鐘，重開 Pi"
                echo "$NOW" > "$REBOOT_STAMP"
                sync
                act systemctl reboot
                [ "$DRYRUN" = 1 ] && exit 0
            fi
        fi
    fi
    sleep "$SLEEP"
done
