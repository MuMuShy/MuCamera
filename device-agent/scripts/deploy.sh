#!/bin/bash
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}MuMu Camera Device Agent 部署腳本${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}請使用 sudo 執行此腳本${NC}"
  exit 1
fi

# Get device ID
DEVICE_ID=$1
if [ -z "$DEVICE_ID" ]; then
  echo -e "${YELLOW}請輸入 Device ID (例如: pi-cam-001):${NC}"
  read DEVICE_ID
fi

if [ -z "$DEVICE_ID" ]; then
  echo -e "${RED}Device ID 不能為空${NC}"
  exit 1
fi

echo -e "${GREEN}Device ID: ${DEVICE_ID}${NC}"
echo ""

# Get backend URL
echo -e "${YELLOW}請輸入 Backend URL:${NC}"
echo "例如："
echo "  - wss://backend.your-domain.com/ws/device (Cloudflare Tunnel)"
echo "  - ws://192.168.1.100:8000/ws/device (直接連線)"
read BACKEND_URL

if [ -z "$BACKEND_URL" ]; then
  echo -e "${RED}Backend URL 不能為空${NC}"
  exit 1
fi

echo ""
echo -e "${GREEN}開始部署...${NC}"
echo ""

# Create directories
echo -e "${YELLOW}[1/8] 建立目錄...${NC}"
mkdir -p /opt/mumucam /etc/mumucam /etc/go2rtc

# Install system dependencies
echo -e "${YELLOW}[2/8] 安裝系統依賴...${NC}"
apt update
apt install -y python3 python3-pip python3-venv wget curl

# Download agent files
echo -e "${YELLOW}[3/8] 下載 agent 檔案...${NC}"
cd /opt/mumucam

# Check if files exist locally
if [ -f "./agent.py" ]; then
  echo "使用本地 agent.py"
  cp ./agent.py /opt/mumucam/agent.py
  cp ./requirements.txt /opt/mumucam/requirements.txt
else
  echo -e "${RED}找不到 agent.py，請先將檔案複製到當前目錄${NC}"
  exit 1
fi

# Create Python virtual environment
echo -e "${YELLOW}[4/8] 建立 Python 虛擬環境...${NC}"
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

# Create environment file
echo -e "${YELLOW}[5/8] 建立環境變數檔...${NC}"
cat > /etc/mumucam/agent.env <<EOF
DEVICE_ID=${DEVICE_ID}
BACKEND_URL=${BACKEND_URL}
GO2RTC_HTTP=http://127.0.0.1:1984
EOF
chmod 600 /etc/mumucam/agent.env

# Download and install go2rtc
echo -e "${YELLOW}[6/8] 下載並安裝 go2rtc...${NC}"
cd /opt

# Detect architecture
ARCH=$(uname -m)
if [ "$ARCH" = "aarch64" ]; then
  GO2RTC_URL="https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_arm64"
elif [ "$ARCH" = "armv7l" ]; then
  GO2RTC_URL="https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_arm"
elif [ "$ARCH" = "x86_64" ]; then
  GO2RTC_URL="https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_amd64"
else
  echo -e "${RED}不支援的架構: ${ARCH}${NC}"
  exit 1
fi

wget -O go2rtc ${GO2RTC_URL}
chmod +x go2rtc

# Create go2rtc config
echo -e "${YELLOW}[7/8] 建立 go2rtc 配置...${NC}"
cat > /etc/go2rtc/go2rtc.yaml <<EOF
api:
  listen: "127.0.0.1:1984"

streams:
  # 請編輯此檔案添加您的攝影機
  # 範例:
  # cam:
  #   - rtsp://admin:password@192.168.1.100:554/stream1

log:
  level: info
EOF

# Install systemd services
echo -e "${YELLOW}[8/8] 安裝 systemd services...${NC}"

# go2rtc service
cat > /etc/systemd/system/go2rtc.service <<'EOF'
[Unit]
Description=go2rtc Stream Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt
ExecStart=/opt/go2rtc -c /etc/go2rtc/go2rtc.yaml
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# agent service
cat > /etc/systemd/system/mumucam-agent.service <<'EOF'
[Unit]
Description=MuMu Camera Device Agent
After=network-online.target go2rtc.service
Wants=network-online.target
Requires=go2rtc.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/mumucam
EnvironmentFile=/etc/mumucam/agent.env
ExecStart=/opt/mumucam/venv/bin/python /opt/mumucam/agent.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
TimeoutStopSec=30
KillMode=mixed

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}部署完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}下一步：${NC}"
echo ""
echo "1. 編輯 go2rtc 配置添加攝影機："
echo -e "   ${GREEN}sudo nano /etc/go2rtc/go2rtc.yaml${NC}"
echo ""
echo "2. 啟動服務："
echo -e "   ${GREEN}sudo systemctl start go2rtc${NC}"
echo -e "   ${GREEN}sudo systemctl start mumucam-agent${NC}"
echo ""
echo "3. 設定開機自動啟動："
echo -e "   ${GREEN}sudo systemctl enable go2rtc${NC}"
echo -e "   ${GREEN}sudo systemctl enable mumucam-agent${NC}"
echo ""
echo "4. 查看日誌："
echo -e "   ${GREEN}sudo journalctl -u mumucam-agent -f${NC}"
echo ""
echo "5. 檢查狀態："
echo -e "   ${GREEN}sudo systemctl status mumucam-agent${NC}"
echo ""
