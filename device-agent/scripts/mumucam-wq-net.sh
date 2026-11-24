#!/bin/sh
# 水質監測站（JNC，192.168.0.88，接 eth1 那張 USB 網卡）上網用 NAT
# 設備出廠設定：IP 192.168.0.88/24、閘道 192.168.0.1。
# Pi 在 eth1 掛 192.168.0.1/32 + 192.168.0.88/32 專用路由（由 NetworkManager「WaterQuality」連線負責），
# 這裡只負責轉發與 NAT 到 4G（usb+）。192.168.0.0/24 其餘位址仍走 eth2（攝影機 2）。
# 可重複執行（-C 檢查後才新增）。
set -e
WQ_IP=192.168.0.88

sysctl -q -w net.ipv4.ip_forward=1

ensure() {
    table=$1; chain=$2; shift 2
    iptables -t "$table" -C "$chain" "$@" 2>/dev/null || iptables -t "$table" -A "$chain" "$@"
}

ensure nat    POSTROUTING -s "$WQ_IP/32" -o usb+ -j MASQUERADE
ensure filter FORWARD     -s "$WQ_IP/32" -o usb+ -j ACCEPT
ensure filter FORWARD     -d "$WQ_IP/32" -i usb+ -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
