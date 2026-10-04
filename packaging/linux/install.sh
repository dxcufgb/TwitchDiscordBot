#!/usr/bin/env bash
# TwitchDiscordBot installer for Linux (systemd).
#
# Run from the extracted release folder:
#   sudo ./install.sh                 install or upgrade, ask for credentials, start the service
#   sudo ./install.sh --reconfigure   enter new credentials and restart
#   sudo ./install.sh --uninstall     remove the service and program files (keeps .env, data/, logs/)
#   sudo ./install.sh --uninstall --purge   also delete settings, data, logs and the service user
#
# Options:
#   --prefix DIR    install location            (default /opt/twitchdiscordbot)
#   --user NAME     system user running the bot (default twitchbot)
#   --no-service    don't install the systemd service (run start-bot.sh yourself)
#   --skip-check    don't verify the credentials against Discord/Twitch
#   --yes           never prompt: install missing packages, take credentials from the environment
#                   (DISCORD_TOKEN, TWITCH_APP_ID, TWITCH_APP_SECRET, optional OWNER_ID)
#
# After installing, the installer is also available as $PREFIX/install.sh.
set -euo pipefail

SERVICE=twitchdiscordbot
PREFIX=/opt/twitchdiscordbot
BOT_USER=twitchbot
ACTION=install
PURGE=0
SERVICE_ON=1
SKIP_CHECK=0
ASSUME_YES=0
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() { sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix) PREFIX="${2:?--prefix needs a directory}"; shift ;;
        --user) BOT_USER="${2:?--user needs a name}"; shift ;;
        --reconfigure) ACTION=reconfigure ;;
        --uninstall) ACTION=uninstall ;;
        --purge) PURGE=1 ;;
        --no-service) SERVICE_ON=0 ;;
        --skip-check) SKIP_CHECK=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done
PREFIX="${PREFIX%/}"
UNIT_FILE="/etc/systemd/system/${SERVICE}.service"
VENV_PY="$PREFIX/.venv/bin/python"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

confirm() {  # confirm "question" -> 0 for yes
    [[ $ASSUME_YES -eq 1 ]] && return 0
    local reply
    read -r -p "$1 [Y/n] " reply </dev/tty || return 1
    [[ -z "$reply" || "$reply" =~ ^[Yy] ]]
}

as_bot() {
    if command -v runuser >/dev/null 2>&1; then runuser -u "$BOT_USER" -- "$@"; else sudo -u "$BOT_USER" -- "$@"; fi
}

has_systemd() { command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; }

[[ $EUID -eq 0 ]] || die "Please run as root: sudo $0 $*"

# ---------------------------------------------------------------- uninstall
if [[ $ACTION == uninstall ]]; then
    if has_systemd && [[ -f $UNIT_FILE ]]; then
        info "Stopping and removing the $SERVICE service"
        systemctl disable --now "$SERVICE" 2>/dev/null || true
        rm -f "$UNIT_FILE"
        systemctl daemon-reload
    fi
    if [[ -d $PREFIX ]]; then
        if [[ $PURGE -eq 1 ]]; then
            info "Deleting $PREFIX (including settings, data and logs)"
            rm -rf "$PREFIX"
        else
            info "Removing program files from $PREFIX (keeping .env, data/ and logs/)"
            find "$PREFIX" -mindepth 1 -maxdepth 1 ! -name .env ! -name data ! -name logs -exec rm -rf {} +
        fi
    fi
    if [[ $PURGE -eq 1 ]] && id "$BOT_USER" >/dev/null 2>&1; then
        info "Removing user $BOT_USER"
        userdel "$BOT_USER" 2>/dev/null || warn "could not remove user $BOT_USER"
    fi
    info "Uninstalled."
    exit 0
fi

# ---------------------------------------------------------------- python
python_ok() {  # python_ok CMD -> usable Python >= 3.10 with venv + ensurepip
    "$1" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1
}

find_python() {
    local cand
    for cand in "${PYTHON:-}" python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
        [[ -n $cand ]] && command -v "$cand" >/dev/null 2>&1 && python_ok "$cand" && { command -v "$cand"; return 0; }
    done
    return 1
}

install_python() {
    local cmd
    if command -v apt-get >/dev/null 2>&1; then
        cmd="apt-get update && apt-get install -y python3 python3-venv"
    elif command -v dnf >/dev/null 2>&1; then
        cmd="dnf install -y python3"
    elif command -v yum >/dev/null 2>&1; then
        cmd="yum install -y python3"
    elif command -v zypper >/dev/null 2>&1; then
        cmd="zypper --non-interactive install python3"
    elif command -v pacman >/dev/null 2>&1; then
        cmd="pacman -Sy --noconfirm --needed python"
    elif command -v apk >/dev/null 2>&1; then
        cmd="apk add python3"
    else
        die "Python 3.10+ (with venv) is required and no supported package manager was found. Install it and re-run."
    fi
    confirm "Python 3.10+ with venv support is required. Install it now with: $cmd ?" ||
        die "Python 3.10+ is required."
    info "Installing Python"
    bash -c "$cmd"
}

PY="$(find_python || true)"
if [[ -z $PY ]]; then
    install_python
    PY="$(find_python || true)"
    [[ -n $PY ]] || die "No Python 3.10+ with venv support available after installing (your distribution may ship an older Python; install 3.10+ and set PYTHON=/path/to/python3.x)."
fi
info "Using $("$PY" --version) at $PY"

# ---------------------------------------------------------------- files
if [[ $ACTION == install ]]; then
    [[ -f $SRC_DIR/Main.py && -d $SRC_DIR/twitchbot ]] || die "Run this script from the extracted release folder."

    if ! id "$BOT_USER" >/dev/null 2>&1; then
        info "Creating system user $BOT_USER"
        useradd --system --home-dir "$PREFIX" --no-create-home \
            --shell "$(command -v nologin || echo /usr/sbin/nologin)" "$BOT_USER"
    fi

    if has_systemd && systemctl is-active --quiet "$SERVICE"; then
        info "Stopping the running bot for the upgrade"
        systemctl stop "$SERVICE"
    fi

    install -d -m 755 "$PREFIX"
    if [[ $SRC_DIR != "$PREFIX" ]]; then
        info "Installing $(cat "$SRC_DIR/VERSION" 2>/dev/null || echo 'files') into $PREFIX"
        rm -rf "$PREFIX/twitchbot"
        cp -R "$SRC_DIR/twitchbot" "$PREFIX/"
        for f in Main.py requirements.txt VERSION .env.example start-bot.sh install.sh README.md; do
            [[ -e $SRC_DIR/$f ]] && cp "$SRC_DIR/$f" "$PREFIX/$f"
        done
        chown -R root:root "$PREFIX/twitchbot"
        chmod 755 "$PREFIX/install.sh" "$PREFIX/start-bot.sh" 2>/dev/null || true
    fi
    install -d -m 750 -o "$BOT_USER" -g "$BOT_USER" "$PREFIX/data" "$PREFIX/logs"

    # Import settings from an earlier install made from a git checkout (scripts/install-service.sh).
    if [[ ! -f $PREFIX/.env && -f $UNIT_FILE ]]; then
        OLD_DIR="$(sed -n 's/^WorkingDirectory=//p' "$UNIT_FILE" | head -n1)"
        if [[ -n $OLD_DIR && $OLD_DIR != "$PREFIX" && -f $OLD_DIR/.env ]] &&
            confirm "Found an existing installation in $OLD_DIR. Import its settings and data?"; then
            info "Importing settings and data from $OLD_DIR"
            cp "$OLD_DIR/.env" "$PREFIX/.env"
            [[ -d $OLD_DIR/data ]] && cp -R "$OLD_DIR/data/." "$PREFIX/data/"
            # 1.x data files; the bot migrates them on first start if data/state.json doesn't exist yet.
            find "$OLD_DIR" -maxdepth 1 \( -name 'Streams.json' -o -name 'streams.json' -o -name '*-Stream.json' \) \
                -exec cp {} "$PREFIX/" \;
            chown -R "$BOT_USER:$BOT_USER" "$PREFIX/data"
        fi
    fi

    if [[ -x $VENV_PY ]] && ! "$VENV_PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
        info "Existing virtual environment is broken or too old; recreating it"
        rm -rf "$PREFIX/.venv"
    fi
    if [[ ! -x $VENV_PY ]]; then
        info "Creating virtual environment"
        "$PY" -m venv "$PREFIX/.venv"
    fi
    info "Installing Python packages"
    "$VENV_PY" -m pip install --disable-pip-version-check --no-input -q -r "$PREFIX/requirements.txt"
    sha256sum "$PREFIX/requirements.txt" | cut -d' ' -f1 >"$PREFIX/.venv/.requirements.sha256"
    "$VENV_PY" -m compileall -q "$PREFIX/twitchbot" >/dev/null || true
fi

[[ -x $VENV_PY && -f $PREFIX/Main.py ]] || die "TwitchDiscordBot is not installed in $PREFIX; run the installer from the release folder first."

# ---------------------------------------------------------------- configuration
env_has() {  # env_has KEY... -> 0 if any key has a non-empty value in .env
    local key
    for key in "$@"; do
        grep -Eq "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=[[:space:]]*['\"]?[^'\"[:space:]]" "$PREFIX/.env" 2>/dev/null && return 0
    done
    return 1
}

ask() {  # ask VAR "prompt" secret(0/1) required(0/1)
    local var=$1 prompt=$2 secret=$3 required=$4 value=""
    if [[ $ASSUME_YES -eq 1 ]]; then
        value="${!var:-}"
        [[ -n $value || $required -eq 0 ]] || die "$var must be set in the environment when using --yes."
    else
        while :; do
            if [[ $secret -eq 1 ]]; then
                read -r -s -p "$prompt: " value </dev/tty; echo
            else
                read -r -p "$prompt: " value </dev/tty
            fi
            value="${value#"${value%%[![:space:]]*}"}"; value="${value%"${value##*[![:space:]]}"}"
            [[ -n $value || $required -eq 0 ]] && break
            echo "  A value is required."
        done
    fi
    printf -v "$var" '%s' "$value"
}

secure_env() {  # readable by root and the bot's group only
    chown "root:$BOT_USER" "$PREFIX/.env"
    chmod 640 "$PREFIX/.env"
}

write_env() {
    (umask 077 && touch "$PREFIX/.env")
    # python-dotenv's set_key quotes values safely and keeps any other settings already in the file.
    ENV_FILE="$PREFIX/.env" "$VENV_PY" - <<'PYEOF'
import os
from dotenv import set_key
for key in ("DISCORD_TOKEN", "TWITCH_APP_ID", "TWITCH_APP_SECRET", "OWNER_ID"):
    value = os.environ.get("NEW_" + key, "")
    if value:
        set_key(os.environ["ENV_FILE"], key, value, quote_mode="always")
PYEOF
    secure_env  # set_key replaces the file, so (re)apply ownership and mode afterwards
}

configure() {
    cat <<'TXT'

Credentials (input for secrets is hidden):
  * Discord bot token:  https://discord.com/developers/applications -> your app -> Bot -> Reset Token
  * Twitch Client ID and secret: https://dev.twitch.tv/console/apps -> your app -> Manage
TXT
    ask DISCORD_TOKEN "Discord bot token" 1 1
    ask TWITCH_APP_ID "Twitch Client ID" 0 1
    ask TWITCH_APP_SECRET "Twitch Client secret" 1 1
    ask OWNER_ID "Your Discord user ID for /restart (optional, Enter to skip)" 0 0
    NEW_DISCORD_TOKEN=$DISCORD_TOKEN NEW_TWITCH_APP_ID=$TWITCH_APP_ID NEW_TWITCH_APP_SECRET=$TWITCH_APP_SECRET \
        NEW_OWNER_ID=$OWNER_ID write_env
    unset DISCORD_TOKEN TWITCH_APP_SECRET
}

if [[ $ACTION == reconfigure ]] || ! env_has DISCORD_TOKEN TOKEN || ! env_has TWITCH_APP_SECRET TWITCH-APP-SECRET; then
    configure
else
    info "Keeping existing settings in $PREFIX/.env (use --reconfigure to change them)"
    secure_env
fi

if [[ $SKIP_CHECK -eq 0 ]]; then
    while :; do
        info "Checking credentials with Discord and Twitch"
        if (cd "$PREFIX" && as_bot "$VENV_PY" Main.py --check); then
            break
        fi
        if [[ $ASSUME_YES -eq 1 ]]; then
            die "Credential check failed (use --skip-check to install anyway)."
        fi
        if confirm "Enter the credentials again?"; then
            configure
        elif confirm "Continue anyway?"; then
            break
        else
            die "Aborted."
        fi
    done
fi

# ---------------------------------------------------------------- service
if [[ $SERVICE_ON -eq 0 ]] || ! has_systemd; then
    [[ $SERVICE_ON -eq 1 ]] && warn "systemd was not found, so no service was installed."
    info "Done. Start the bot with:  sudo -u $BOT_USER $PREFIX/start-bot.sh --watch"
    exit 0
fi

info "Installing systemd service $SERVICE"
cat >"$UNIT_FILE" <<UNIT
[Unit]
Description=TwitchDiscordBot - Twitch live notifications for Discord
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${BOT_USER}
Group=${BOT_USER}
WorkingDirectory=${PREFIX}
ExecStart=${VENV_PY} ${PREFIX}/Main.py
# /restart in Discord exits the bot; systemd starts it again (also after crashes).
Restart=always
RestartSec=10
# Exit code 2 = bad configuration or token rejected by Discord: retrying can't help, so don't loop.
RestartPreventExitStatus=2
Environment=PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Hardening: the bot may only write to its data and log folders.
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ReadWritePaths=${PREFIX}/data ${PREFIX}/logs
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
systemctl restart "$SERVICE"
sleep 5
if systemctl is-active --quiet "$SERVICE"; then
    info "TwitchDiscordBot $(cat "$PREFIX/VERSION" 2>/dev/null) is running and will start at boot."
else
    warn "The service did not stay up. Recent log output:"
    journalctl -u "$SERVICE" -n 30 --no-pager || true
    exit 1
fi

cat <<TXT

  Status:        systemctl status $SERVICE
  Logs:          journalctl -u $SERVICE -f      (also $PREFIX/logs/bot.log)
  Restart:       sudo systemctl restart $SERVICE
  Change creds:  sudo $PREFIX/install.sh --reconfigure
  Upgrade:       run install.sh from a newer release folder
  Uninstall:     sudo $PREFIX/install.sh --uninstall [--purge]

Next, in Discord: /setup channel, then /stream add <twitch name>.
TXT
