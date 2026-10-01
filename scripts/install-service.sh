#!/usr/bin/env bash
# Installs (or removes) a systemd service so the bot starts on boot and is
# restarted automatically if it crashes.
#
#   sudo ./scripts/install-service.sh              # install + start
#   sudo ./scripts/install-service.sh --uninstall  # stop + remove
#
# The service runs as the user who invoked sudo (override with BOT_USER=name).
# Logs:    journalctl -u twitchdiscordbot -f
# Control: sudo systemctl {start|stop|restart|status} twitchdiscordbot
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-twitchdiscordbot}"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOT_USER="${BOT_USER:-${SUDO_USER:-$(id -un)}}"

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo: sudo $0 $*" >&2
    exit 1
fi

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$UNIT_FILE"
    systemctl daemon-reload
    echo "Removed $SERVICE_NAME."
    exit 0
fi

if [[ "$BOT_USER" == "root" ]]; then
    echo "Refusing to run the bot as root. Set BOT_USER=<user> or run via sudo from a normal account." >&2
    exit 1
fi

chmod +x "$BOT_DIR/start-bot.sh"

cat > "$UNIT_FILE" <<UNIT
[Unit]
Description=Twitch Discord notification bot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${BOT_USER}
WorkingDirectory=${BOT_DIR}
ExecStart=${BOT_DIR}/start-bot.sh
Restart=always
RestartSec=10
# The bot's !restart-bot command re-execs in place; a real crash is handled by Restart=.

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
echo "Installed $UNIT_FILE (user: $BOT_USER, dir: $BOT_DIR)."
echo "Follow logs with: journalctl -u $SERVICE_NAME -f"
