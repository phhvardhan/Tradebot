#!/usr/bin/env bash
# Run ON the server (Ubuntu 22.04 or 24.04), from the project folder, as your normal login user:  bash deploy/setup-server.sh
# Installs Python's runtime pieces and pdftotext, the server and watchdog as systemd services, and Tailscale for private access.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
sudo apt-get update -q
sudo apt-get install -y -q python3 poppler-utils curl
for unit in signaldesk.service signaldesk-watchdog.service signaldesk-watchdog.timer; do
  sed -e "s|__USER__|$USER_NAME|g" -e "s|__DIR__|$DIR|g" "$DIR/deploy/$unit" | sudo tee "/etc/systemd/system/$unit" > /dev/null
done
[ -f "$DIR/.env" ] && chmod 600 "$DIR/.env"
sudo systemctl daemon-reload
sudo systemctl enable --now signaldesk.service signaldesk-watchdog.timer
command -v tailscale > /dev/null || curl -fsSL https://tailscale.com/install.sh | sh
cat <<MSG

Signal Desk is running:   systemctl status signaldesk     logs: journalctl -u signaldesk -f
Next, private access from your phone and laptop (both need the Tailscale app, same account):
  sudo tailscale up
  sudo tailscale serve --bg 8765
  tailscale serve status        # prints https://<this-machine>.<your-tailnet>.ts.net
Put that host name in .env as SIGNALDESK_HOSTS=<this-machine>.<your-tailnet>.ts.net, then: sudo systemctl restart signaldesk
MSG
