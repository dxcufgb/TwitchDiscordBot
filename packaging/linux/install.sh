#!/usr/bin/env bash
# TwitchDiscordBot installer for Linux (systemd).
#
# Run from the extracted release folder:
#   sudo ./install.sh                 install or upgrade, ask for credentials, start the service
#   sudo ./install.sh --reconfigure   enter new credentials and restart
#   sudo ./install.sh --uninstall     remove the service and program files (keeps .env, data/, logs/)
#   sudo ./install.sh --uninstall --purge   also delete settings, data, logs and the service user
#   sudo ./install.sh --enable-auto-update    turn on daily automatic updates (asks for a GitHub token)
#   sudo ./install.sh --disable-auto-update   turn them off and delete the stored token
#
# Options:
#   --prefix DIR    install location            (default /opt/twitchdiscordbot)
#   --user NAME     system user running the bot (default twitchbot)
#   --no-service    don't install the systemd service (run start-bot.sh yourself)
#   --skip-check    don't verify the credentials against Discord/Twitch
#   --yes           never prompt: install missing packages, take credentials from the environment
#                   (DISCORD_TOKEN, TWITCH_APP_ID, TWITCH_APP_SECRET, optional OWNER_ID)
#   --auto-update   also turn on automatic updates; the GitHub token comes from UPDATE_GITHUB_TOKEN or a prompt
#   --no-auto-update  turn automatic updates off (by default an upgrade keeps the current choice)
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
AUTO_UPDATE=""  # "" = keep the current choice, 1 = turn on, 0 = turn off
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; }

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
        --auto-update) AUTO_UPDATE=1 ;;
        --no-auto-update) AUTO_UPDATE=0 ;;
        --enable-auto-update) ACTION=enable-update ;;
        --disable-auto-update) ACTION=disable-update ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done
PREFIX="${PREFIX%/}"
UNIT_FILE="/etc/systemd/system/${SERVICE}.service"
VENV_PY="$PREFIX/.venv/bin/python"
UPDATE_UNIT="/etc/systemd/system/${SERVICE}-update.service"
UPDATE_TIMER="/etc/systemd/system/${SERVICE}-update.timer"
UPDATER_DIR="$PREFIX/updater"
TOKEN_FILE="$UPDATER_DIR/github-token"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

confirm() {  # confirm "question" -> 0 for yes
    [[ $ASSUME_YES -eq 1 ]] && return 0
    local reply
    read -r -p "$1 [Y/n] " reply </dev/tty || return 1
    [[ -z "$reply" || "$reply" =~ ^[Yy] ]]
}

confirm_no() {  # confirm_no "question" -> 0 for yes; default no, and no when not interactive
    [[ $ASSUME_YES -eq 1 ]] && return 1
    local reply
    read -r -p "$1 [y/N] " reply </dev/tty || return 1
    [[ "$reply" =~ ^[Yy] ]]
}

as_bot() {
    if command -v runuser >/dev/null 2>&1; then runuser -u "$BOT_USER" -- "$@"; else sudo -u "$BOT_USER" -- "$@"; fi
}

has_systemd() { command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; }

# Program files belong to root and are not writable by the bot user: root runs the installer, the updater and the
# virtual environment's Python from here, so a compromised bot account must not be able to change them. The bot user
# only owns data/ and logs/ and can read .env (root:$BOT_USER 640).
secure_program_files() {
    local entry
    chown root:root "$PREFIX"
    chmod 755 "$PREFIX"
    while IFS= read -r -d '' entry; do
        chown -R root:root "$entry"  # -R without -L never follows symbolic links
        if [[ ! -L $entry ]]; then chmod -R go-w "$entry"; fi
    done < <(find "$PREFIX" -mindepth 1 -maxdepth 1 ! -name .env ! -name data ! -name logs ! -name updater -print0)
}

venv_is_root_only() {  # 0 if nothing in the virtual environment is owned or writable by anyone but root
    [[ -z "$(find "$PREFIX/.venv" ! -type l \( ! -user root -o -perm /002 -o \( -perm /020 ! -group root \) \) -print -quit 2>/dev/null)" ]]
}

[[ $EUID -eq 0 ]] || die "Please run as root: sudo $0 $*"

# ---------------------------------------------------------------- automatic updates (opt-in)
# A systemd timer runs $PREFIX/auto-update.sh daily as root. The repository is private, so it needs a GitHub
# token; it is kept in $TOKEN_FILE (root only, mode 600), separate from the bot's .env.
auto_update_enabled() { [[ -f $UPDATE_TIMER ]]; }

save_update_token() {
    local token="${UPDATE_GITHUB_TOKEN:-}" tmp
    if [[ -z $token && -s $TOKEN_FILE ]] && { [[ $ASSUME_YES -eq 1 ]] || ! confirm_no "Replace the stored GitHub token?"; }; then
        info "Keeping the stored GitHub token"
        return 0
    fi
    if [[ -z $token ]]; then
        [[ $ASSUME_YES -eq 0 ]] || die "Set UPDATE_GITHUB_TOKEN in the environment to turn on automatic updates with --yes."
        cat <<'TXT'

Automatic updates download new releases from the private GitHub repository dxcufgb/TwitchDiscordBot,
so they need a GitHub token (input is hidden). Create one at https://github.com/settings/personal-access-tokens:
Fine-grained token -> Resource owner dxcufgb -> Only select repositories: TwitchDiscordBot ->
Repository permissions: Contents = Read-only (nothing else). Pick an expiry date you will remember.
TXT
        read -r -s -p "GitHub token: " token </dev/tty || true; echo
        token="${token#"${token%%[![:space:]]*}"}"; token="${token%"${token##*[![:space:]]}"}"
        [[ -n $token ]] || die "A GitHub token is required for automatic updates."
    fi
    [[ $token =~ ^[A-Za-z0-9_]+$ ]] || die "That does not look like a GitHub token (letters, digits and _ only)."
    install -d -m 700 -o root -g root "$UPDATER_DIR"
    tmp="$(mktemp "$UPDATER_DIR/.github-token.XXXXXX")"
    printf '%s\n' "$token" >"$tmp"  # printf is a builtin: the token never appears in a process list
    chown root:root "$tmp"
    chmod 600 "$tmp"
    mv -f "$tmp" "$TOKEN_FILE"
    info "Saved the GitHub token in $TOKEN_FILE (readable by root only)"
}

secure_updater_dir() {
    [[ -d $UPDATER_DIR ]] || return 0
    chown -R root:root "$UPDATER_DIR"
    chmod 700 "$UPDATER_DIR"
    [[ ! -f $TOKEN_FILE ]] || chmod 600 "$TOKEN_FILE"
}

write_update_units() {
    local bot_user=$BOT_USER
    if [[ -f $UNIT_FILE ]]; then bot_user="$(sed -n 's/^User=//p' "$UNIT_FILE" | head -n1)"; bot_user="${bot_user:-$BOT_USER}"; fi
    cat >"$UPDATE_UNIT" <<UNIT
[Unit]
Description=TwitchDiscordBot - install new releases (automatic updates)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
# Root, because it runs the release's install.sh. Logs: journalctl -u ${SERVICE}-update
ExecStart=${PREFIX}/auto-update.sh --user ${bot_user}
TimeoutStartSec=30min
Environment=PIP_NO_CACHE_DIR=1
UMask=0022

# Hardening: it only needs to write the install folder and the systemd unit files.
NoNewPrivileges=yes
ProtectSystem=full
ReadWritePaths=${PREFIX} /etc/systemd/system
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
UNIT
    cat >"$UPDATE_TIMER" <<UNIT
[Unit]
Description=Daily check for TwitchDiscordBot updates

[Timer]
OnCalendar=daily
RandomizedDelaySec=4h
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    systemctl daemon-reload
    systemctl enable --now "${SERVICE}-update.timer" >/dev/null 2>&1
}

enable_auto_update() {
    has_systemd || die "Automatic updates need systemd."
    [[ -x $PREFIX/auto-update.sh ]] || die "$PREFIX has no auto-update.sh; install this release first (sudo ./install.sh --auto-update)."
    save_update_token
    secure_updater_dir
    write_update_units
    info "Automatic updates are on: a newer release is installed daily (check now: sudo $PREFIX/auto-update.sh --check)."
}

disable_auto_update() {
    if has_systemd && { [[ -f $UPDATE_TIMER ]] || [[ -f $UPDATE_UNIT ]]; }; then
        systemctl disable --now "${SERVICE}-update.timer" >/dev/null 2>&1 || true
        rm -f "$UPDATE_TIMER" "$UPDATE_UNIT"
        systemctl daemon-reload
    fi
    rm -f "$UPDATE_TIMER" "$UPDATE_UNIT"
    rm -rf "$UPDATER_DIR"
}

if [[ $ACTION == enable-update || $AUTO_UPDATE == 1 ]]; then
    has_systemd || die "Automatic updates need systemd."
    if [[ $ASSUME_YES -eq 1 && -z ${UPDATE_GITHUB_TOKEN:-} && ! -s $TOKEN_FILE ]]; then
        die "Set UPDATE_GITHUB_TOKEN in the environment to turn on automatic updates with --yes."
    fi
fi
if [[ $ACTION == enable-update ]]; then
    [[ -x $VENV_PY && -f $PREFIX/Main.py ]] || die "TwitchDiscordBot is not installed in $PREFIX."
    enable_auto_update
    exit 0
fi
if [[ $ACTION == disable-update ]]; then
    disable_auto_update
    info "Automatic updates are off and the GitHub token was deleted."
    exit 0
fi

# ---------------------------------------------------------------- uninstall
if [[ $ACTION == uninstall ]]; then
    if auto_update_enabled || [[ -f $UPDATE_UNIT || -d $UPDATER_DIR ]]; then
        info "Removing automatic updates and the stored GitHub token"
        disable_auto_update
    fi
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
    if [[ $PURGE -eq 1 && -d /var/lib/${SERVICE}-update ]]; then
        info "Deleting backups kept by a failed automatic update rollback"
        rm -rf "/var/lib/${SERVICE}-update"
    fi
    if [[ $PURGE -eq 1 ]] && id "$BOT_USER" >/dev/null 2>&1; then
        info "Removing user $BOT_USER"
        userdel "$BOT_USER" 2>/dev/null || warn "could not remove user $BOT_USER"
    fi
    info "Uninstalled."
    exit 0
fi

# ---------------------------------------------------------------- python
in_home_dir() {  # in_home_dir CMD -> 0 if the interpreter or its standard library lives under /home or /root
    # The service can't use such a Python: systemd's ProtectHome=yes hides those directories,
    # and the bot user usually can't read them anyway (pyenv, uv, conda installs).
    local path
    while IFS= read -r path; do
        case "$path" in /home/* | /root | /root/* | /run/user/*) return 0 ;; esac
    done < <("$1" -c 'import os, sys; print(os.path.realpath(sys.executable)); print(os.path.realpath(sys.base_prefix))' 2>/dev/null)
    return 1
}

python_ok() {  # python_ok CMD -> usable Python >= 3.10 with venv + ensurepip, installed outside home directories
    "$1" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1 && ! in_home_dir "$1"
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

HOME_PYTHON_HELP="The service can't use a Python inside a home directory (systemd's ProtectHome hides them and the bot user can't read them). Use a system-wide Python: your package manager's, or one installed under /usr/local or /opt."
if [[ -n ${PYTHON:-} ]] && command -v "$PYTHON" >/dev/null 2>&1 && in_home_dir "$PYTHON"; then
    die "PYTHON=$PYTHON is inside a home directory. $HOME_PYTHON_HELP"
fi
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

    # Ask once, on a fresh interactive install; upgrades keep whatever was chosen before.
    if [[ -z $AUTO_UPDATE && ! -f $PREFIX/Main.py && $ASSUME_YES -eq 0 ]] && has_systemd &&
        confirm_no "Install new releases automatically (daily; needs a read-only GitHub token)?"; then
        AUTO_UPDATE=1
    fi

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
    # Root writes and chowns these by name below; a symbolic link here would redirect that elsewhere.
    for f in .env data logs; do
        [[ ! -L $PREFIX/$f ]] || die "$PREFIX/$f is a symbolic link; remove it and run the installer again."
    done
    if [[ -e $PREFIX/.venv ]] && ! venv_is_root_only; then
        # Root runs this Python (pip, the updater): never trust one that someone else could have changed.
        info "Existing virtual environment was writable by other users; recreating it"
        rm -rf "$PREFIX/.venv"
    fi
    secure_program_files  # before copying: an earlier, looser layout must not keep its owner on replaced files
    if [[ $SRC_DIR != "$PREFIX" ]]; then
        info "Installing $(cat "$SRC_DIR/VERSION" 2>/dev/null || echo 'files') into $PREFIX"
        rm -rf "$PREFIX/twitchbot"
        cp -R "$SRC_DIR/twitchbot" "$PREFIX/"
        for f in Main.py requirements.txt VERSION CHANGES.md .env.example start-bot.sh install.sh README.md; do
            [[ -e $SRC_DIR/$f ]] && cp "$SRC_DIR/$f" "$PREFIX/$f"
        done
        if [[ -e $SRC_DIR/auto-update.sh ]]; then
            # Replace it with a new file (not in place): the updater may be running this very script.
            cp "$SRC_DIR/auto-update.sh" "$PREFIX/.auto-update.sh.new"
            mv -f "$PREFIX/.auto-update.sh.new" "$PREFIX/auto-update.sh"
        fi
        chmod 755 "$PREFIX/install.sh" "$PREFIX/start-bot.sh" "$PREFIX/auto-update.sh" 2>/dev/null || true
    fi
    install -d -m 750 -o "$BOT_USER" -g "$BOT_USER" "$PREFIX/data" "$PREFIX/logs"

    # Import settings from an earlier install made from a git checkout (scripts/install-service.sh).
    if [[ ! -f $PREFIX/.env && -f $UNIT_FILE ]]; then
        OLD_DIR="$(sed -n 's/^WorkingDirectory=//p' "$UNIT_FILE" | head -n1)"
        if [[ -n $OLD_DIR && $OLD_DIR != "$PREFIX" && -f $OLD_DIR/.env ]] &&
            confirm "Found an existing installation in $OLD_DIR. Import its settings and data?"; then
            info "Importing settings and data from $OLD_DIR"
            cp "$OLD_DIR/.env" "$PREFIX/.env"
            # Only into an empty data/: root must not copy into whatever the bot user may have put there.
            if [[ -d $OLD_DIR/data && -z "$(find "$PREFIX/data" -mindepth 1 -print -quit)" ]]; then
                cp -R "$OLD_DIR/data/." "$PREFIX/data/"
            elif [[ -d $OLD_DIR/data ]]; then
                warn "$PREFIX/data is not empty; not importing $OLD_DIR/data"
            fi
            # 1.x data files; the bot migrates them on first start if data/state.json doesn't exist yet.
            find "$OLD_DIR" -maxdepth 1 \( -name 'Streams.json' -o -name 'streams.json' -o -name '*-Stream.json' \) \
                -exec cp {} "$PREFIX/" \;
            chown -R "$BOT_USER:$BOT_USER" "$PREFIX/data"
        fi
    fi

    # The bot user must be able to run the interpreter (e.g. not a Python under a 0700 directory).
    # (run from / so the check doesn't depend on the current directory, e.g. a 0700 home folder)
    (cd / && as_bot "$PY" -c 'import venv' >/dev/null 2>&1) ||
        die "User $BOT_USER cannot run $PY. Make it readable and executable for all users, or use a system-wide Python (PYTHON=/path/to/python3)."

    if [[ -x $VENV_PY ]] && ! (cd / && as_bot "$VENV_PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1); then
        info "Existing virtual environment is broken, too old or not usable by $BOT_USER; recreating it"
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
    secure_program_files
fi

[[ -x $VENV_PY && -f $PREFIX/Main.py ]] || die "TwitchDiscordBot is not installed in $PREFIX; run the installer from the release folder first."
venv_is_root_only || die "The virtual environment in $PREFIX/.venv is writable by other users; run install.sh from the release folder to recreate it."

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
    Recommended: on the same Bot page turn on "Server Members Intent" (Privileged Gateway Intents) so update
    notices reach server admins too; without it they go to server owners only.
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

# ---------------------------------------------------------------- automatic updates
if [[ $AUTO_UPDATE == 1 ]]; then
    enable_auto_update
elif [[ $AUTO_UPDATE == 0 ]]; then
    if auto_update_enabled || [[ -d $UPDATER_DIR ]]; then
        disable_auto_update
        info "Automatic updates are off and the GitHub token was deleted."
    fi
elif auto_update_enabled && has_systemd; then
    if [[ -s $TOKEN_FILE ]]; then
        secure_updater_dir
        write_update_units  # refresh the units; the choice and the token stay as they are
        info "Keeping automatic updates on (turn off: sudo $PREFIX/install.sh --disable-auto-update)"
    else
        warn "Automatic updates are on but $TOKEN_FILE is missing; run: sudo $PREFIX/install.sh --enable-auto-update"
    fi
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
  Auto-update:   sudo $PREFIX/install.sh --enable-auto-update | --disable-auto-update

Next, in Discord: /setup channel, then /stream add <twitch name>.
TXT
