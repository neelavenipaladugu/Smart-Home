#!/usr/bin/env bash
# PiHome installer for Raspberry Pi OS (Bookworm, 64-bit) on a Raspberry Pi 4.
# Usage:  cd ~/pihome && bash scripts/install.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HUB_DIR="$PROJECT_DIR/hub"
VENV="$HUB_DIR/.venv"
USER_NAME="$(whoami)"

echo "==> PiHome installer  (project: $PROJECT_DIR)"

echo "==> Installing system packages..."
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev python3-pip \
  mosquitto mosquitto-clients \
  python3-picamera2 python3-lgpio libgpiod2 \
  libopenblas-dev libportaudio2 git

echo "==> Enabling SPI (RFID reader) and the camera..."
sudo raspi-config nonint do_spi 0 || true
sudo raspi-config nonint do_i2c 0 || true

echo "==> Configuring Mosquitto MQTT broker (LAN access)..."
sudo tee /etc/mosquitto/conf.d/pihome.conf > /dev/null <<'EOF'
listener 1883
allow_anonymous true
# For better security create users with: sudo mosquitto_passwd -c /etc/mosquitto/passwd <user>
# then replace allow_anonymous with:
#   allow_anonymous false
#   password_file /etc/mosquitto/passwd
EOF
sudo systemctl enable --now mosquitto
sudo systemctl restart mosquitto

echo "==> Creating Python virtual environment..."
# --system-site-packages lets the venv use apt's picamera2 + lgpio
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install --upgrade pip wheel
"$VENV/bin/pip" install -r "$HUB_DIR/requirements-pi.txt"

echo "==> Adding $USER_NAME to hardware groups..."
sudo usermod -aG gpio,spi,i2c,video,audio "$USER_NAME" || true

echo "==> Installing systemd service (auto-start on boot)..."
sed -e "s|__USER__|$USER_NAME|g" -e "s|__HUB_DIR__|$HUB_DIR|g" \
  "$PROJECT_DIR/scripts/pihome.service" | sudo tee /etc/systemd/system/pihome.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable pihome
sudo systemctl restart pihome

IP="$(hostname -I | awk '{print $1}')"
echo ""
echo "✅ PiHome installed!"
echo "   Dashboard:  http://$IP:8000"
echo "   Logs:       journalctl -u pihome -f"
echo "   Config:     $HUB_DIR/config.yaml  (then: sudo systemctl restart pihome)"
echo "   Reboot once so the new group permissions apply:  sudo reboot"
