#!/usr/bin/env bash
# TwitchDiscordBot automatic updater for Linux (opt-in, see README.md "Automatic updates").
#
# Installed as $PREFIX/auto-update.sh and run as root by twitchdiscordbot-update.timer (daily).
# Run it by hand to check or update right away:
#   sudo /opt/twitchdiscordbot/auto-update.sh           install a newer release now, if there is one
#   sudo /opt/twitchdiscordbot/auto-update.sh --check   only report whether a newer release exists
#
# It downloads the newest release's -linux.tar.gz, verifies it against SHA256SUMS.txt, and runs that
# release's install.sh (the normal upgrade path, keeping .env and data/). If the upgrade fails, the
# previous program files are restored and the bot is started again. Logs go to the journal:
#   journalctl -u twitchdiscordbot-update
set -euo pipefail
# Same as an interactive root shell: the files the installer copies must stay readable for the bot user.
umask 022

SERVICE=twitchdiscordbot
UNIT_FILE="/etc/systemd/system/${SERVICE}.service"
PREFIX="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKEN_FILE="$PREFIX/updater/github-token"
LOCK_FILE="/run/${SERVICE}-update.lock"
VENV_PY="$PREFIX/.venv/bin/python"
MODE=update
BOT_USER=""

log() { printf '%s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

# Everything runs inside main(): bash has then read the whole script before the upgrade replaces this file.
main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --check) MODE=check ;;
            --user) BOT_USER="${2:?--user needs a name}"; shift ;;
            -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; return 0 ;;
            *) die "Unknown option: $1 (see --help)" ;;
        esac
        shift
    done
    [[ $EUID -eq 0 ]] || die "Please run as root: sudo $0"
    [[ -x $VENV_PY && -f $PREFIX/twitchbot/updater.py ]] || die "TwitchDiscordBot is not installed in $PREFIX."
    [[ -f $TOKEN_FILE ]] || die "No GitHub token in $TOKEN_FILE. Enable auto-update with: sudo $PREFIX/install.sh --enable-auto-update"
    if [[ -z $BOT_USER && -f $UNIT_FILE ]]; then
        BOT_USER="$(sed -n 's/^User=//p' "$UNIT_FILE" | head -n1)"
    fi
    BOT_USER="${BOT_USER:-twitchbot}"

    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        log "Another update is already running; nothing to do."
        return 0
    fi

    local code=0 work new_dir
    if [[ $MODE == check ]]; then
        updater check || code=$?
        [[ $code -eq 0 || $code -eq 3 ]] || return 1
        return 0
    fi

    # On disk (not a RAM-backed /tmp): the backup includes the virtual environment.
    work="$(mktemp -d "/var/tmp/${SERVICE}-update.XXXXXXXX")"
    # shellcheck disable=SC2064  # expand $work now
    trap "rm -rf '$work'" EXIT

    new_dir="$(updater download --dest "$work/new")" || code=$?
    if [[ $code -eq 3 ]]; then
        return 0
    elif [[ $code -ne 0 ]]; then
        die "Update check failed; the installed version keeps running."
    fi
    new_dir="$(printf '%s\n' "$new_dir" | tail -n1)"
    [[ -x $new_dir/install.sh && $new_dir == "$work/new/"* ]] || die "Downloaded release has no install.sh."

    log "Backing up the current program files"
    backup_files "$work/backup"
    # Release notes for the bot, which DMs them to server admins when the new version starts.
    # Written before the installer restarts the bot, as the bot user (data/ belongs to it).
    if [[ -f $work/new/update-notice.json && -d $PREFIX/data ]]; then
        # shellcheck disable=SC2016  # $1 is expanded by the inner sh
        as_bot sh -c 'cat >"$1.tmp" && chmod 640 "$1.tmp" && mv -f "$1.tmp" "$1"' sh \
            "$PREFIX/data/update-notice.json" <"$work/new/update-notice.json" ||
            log "Could not save the release notes for the update message"
    fi

    local args=(--yes --skip-check --prefix "$PREFIX" --user "$BOT_USER")
    [[ -f $UNIT_FILE ]] || args+=(--no-service)
    log "Installing $(cat "$new_dir/VERSION" 2>/dev/null || echo 'the new release')"
    # The release's installer upgrades in place: it keeps .env, data/, logs/, this auto-update setting and the
    # token, and exits non-zero if the bot does not stay up.
    if (cd / && env -u DISCORD_TOKEN -u TWITCH_APP_ID -u TWITCH_APP_SECRET -u OWNER_ID -u UPDATE_GITHUB_TOKEN \
        "$new_dir/install.sh" "${args[@]}" </dev/null); then
        log "Updated to $(cat "$PREFIX/VERSION" 2>/dev/null)."
        return 0
    fi

    log "The upgrade failed; restoring the previous version"
    as_bot rm -f "$PREFIX/data/update-notice.json" || true
    restore_files "$work/backup"
    die "Upgrade failed and was rolled back to $(cat "$PREFIX/VERSION" 2>/dev/null || echo 'the previous version')."
}

as_bot() {
    if command -v runuser >/dev/null 2>&1; then runuser -u "$BOT_USER" -- "$@"; else sudo -u "$BOT_USER" -- "$@"; fi
}

updater() {
    (cd / && "$VENV_PY" -I "$PREFIX/twitchbot/updater.py" --install-dir "$PREFIX" --token-file "$TOKEN_FILE" \
        --platform linux "$@")
}

program_entries() {  # everything in $PREFIX except settings, data, logs and the updater's own settings
    find "$PREFIX" -mindepth 1 -maxdepth 1 ! -name .env ! -name data ! -name logs ! -name updater -print0
}

backup_files() {
    local dest=$1 entry unit
    mkdir -p "$dest/files" "$dest/units"
    while IFS= read -r -d '' entry; do
        cp -a "$entry" "$dest/files/"
    done < <(program_entries)
    for unit in "$UNIT_FILE" "/etc/systemd/system/${SERVICE}-update.service" "/etc/systemd/system/${SERVICE}-update.timer"; do
        [[ -f $unit ]] && cp -a "$unit" "$dest/units/"
    done
    return 0
}

restore_files() {
    local src=$1 entry
    if command -v systemctl >/dev/null 2>&1; then systemctl stop "$SERVICE" 2>/dev/null || true; fi
    while IFS= read -r -d '' entry; do
        rm -rf "$entry"
    done < <(program_entries)
    cp -a "$src/files/." "$PREFIX/"
    cp -a "$src/units/." /etc/systemd/system/
    if command -v systemctl >/dev/null 2>&1 && [[ -f $UNIT_FILE ]]; then
        systemctl daemon-reload
        systemctl restart "$SERVICE" || true
    fi
}

main "$@"; exit $?
