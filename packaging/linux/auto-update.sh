#!/usr/bin/env bash
# TwitchDiscordBot automatic updater for Linux (opt-in, see README.md "Automatic updates").
#
# Installed as $PREFIX/auto-update.sh and run as root by twitchdiscordbot-update.timer (daily).
# Run it by hand to check or update right away (or use install.sh --check-update / --update-now):
#   sudo /opt/twitchdiscordbot/auto-update.sh                  install a newer release now, if there is one
#   sudo /opt/twitchdiscordbot/auto-update.sh --check          only report whether a newer release exists
#   sudo /opt/twitchdiscordbot/auto-update.sh --check-update   the same; exit 0 = up to date, 10 = newer release
#                                                              available, anything else = error
#   --prefix DIR   the installation to update (default: the folder this script is in)
# The GitHub token comes from $PREFIX/updater/github-token; without one, a token in INSTALL_GITHUB_TOKEN or
# UPDATE_GITHUB_TOKEN is used for this run only (never stored, never on a command line).
#
# It downloads the newest release's twitchdiscordbot-X.Y.Z-linux.tar.gz, verifies it against SHA256SUMS.txt,
# and runs that release's install.sh (the normal upgrade path, keeping .env and data/). If the upgrade fails,
# the previous program files are restored and the bot is started again; if that rollback fails too, it logs
# "ROLLBACK FAILED", keeps the backup under /var/lib/twitchdiscordbot-update/ and exits non-zero.
# Logs go to the journal:
#   journalctl -u twitchdiscordbot-update
set -euo pipefail
# Same as an interactive root shell: the files the installer copies must stay readable for the bot user.
umask 022

SERVICE=twitchdiscordbot
UNIT_FILE="/etc/systemd/system/${SERVICE}.service"
PREFIX="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK_FILE="/run/${SERVICE}-update.lock"
BACKUP_KEEP_DIR="/var/lib/${SERVICE}-update"
MODE=update
BOT_USER=""
ROLLBACK_ERRORS=()
TOKEN_TMP_DIR=""

log() { printf '%s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

# Everything runs inside main(): bash has then read the whole script before the upgrade replaces this file.
main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --check) MODE=check ;;
            --check-update) MODE=check-update ;;
            --user) BOT_USER="${2:?--user needs a name}"; shift ;;
            --prefix) PREFIX="${2:?--prefix needs a directory}"; PREFIX="${PREFIX%/}"; shift ;;
            -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; return 0 ;;
            *) die "Unknown option: $1 (see --help)" ;;
        esac
        shift
    done
    TOKEN_FILE="$PREFIX/updater/github-token"
    VENV_PY="$PREFIX/.venv/bin/python"
    [[ $EUID -eq 0 ]] || die "Please run as root: sudo $0"
    [[ -x $VENV_PY && -f $PREFIX/twitchbot/updater.py ]] || die "TwitchDiscordBot is not installed in $PREFIX."
    # Root runs this Python and updater.py: refuse if anyone but root could have changed them.
    if [[ -n "$(find "$PREFIX/.venv" "$PREFIX/twitchbot" ! -type l \( ! -user root -o -perm /002 -o \( -perm /020 ! -group root \) \) -print -quit)" ]]; then
        die "Program files in $PREFIX are writable by other users; run install.sh from a release folder to repair them."
    fi
    if [[ -z $BOT_USER && -f $UNIT_FILE ]]; then
        BOT_USER="$(sed -n 's/^User=//p' "$UNIT_FILE" | head -n1)"
    fi
    BOT_USER="${BOT_USER:-twitchbot}"
    if [[ ! -f $TOKEN_FILE ]]; then
        token_from_environment ||
            die "No GitHub token in $TOKEN_FILE. Enable auto-update with: sudo $PREFIX/install.sh --enable-auto-update (or set UPDATE_GITHUB_TOKEN for this run only)"
    fi

    local code=0 work new_dir
    if [[ $MODE == check-update ]]; then
        # Read-only, so no lock: a running update must not turn this into "up to date".
        updater check || code=$?
        case $code in
            0) return 10 ;;  # a newer release is available
            3) return 0 ;;   # up to date
            *) return 1 ;;
        esac
    fi

    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        log "Another update is already running; nothing to do."
        return 0
    fi

    if [[ $MODE == check ]]; then
        updater check || code=$?
        [[ $code -eq 0 || $code -eq 3 ]] || return 1
        return 0
    fi

    # On disk (not a RAM-backed /tmp): the backup includes the virtual environment.
    work="$(mktemp -d "/var/tmp/${SERVICE}-update.XXXXXXXX")"
    # shellcheck disable=SC2064  # expand $work now
    trap "rm -rf '$work'; remove_token_copy" EXIT

    new_dir="$(updater download --dest "$work/new")" || code=$?
    if [[ $code -eq 3 ]]; then
        return 0
    elif [[ $code -ne 0 ]]; then
        die "Update check failed; the installed version keeps running."
    fi
    new_dir="$(printf '%s\n' "$new_dir" | tail -n1)"
    [[ -x $new_dir/install.sh && $new_dir == "$work/new/"* ]] || die "Downloaded release has no install.sh."

    # Whether the bot was running, so a rollback can check that it runs again.
    local was_running=0
    if command -v systemctl >/dev/null 2>&1 && [[ -f $UNIT_FILE ]]; then
        case "$(systemctl is-active "$SERVICE" 2>/dev/null || true)" in
            active | activating | reloading) was_running=1 ;;
        esac
    fi

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
        -u INSTALL_GITHUB_TOKEN "$new_dir/install.sh" "${args[@]}" </dev/null); then
        log "Updated to $(cat "$PREFIX/VERSION" 2>/dev/null)."
        return 0
    fi

    log "The upgrade failed; restoring the previous version"
    as_bot rm -f "$PREFIX/data/update-notice.json" || true
    # Every step records its own failure instead of aborting (set -e), so the verdict below always runs.
    restore_files "$work/backup" "$was_running"
    verify_rollback "$work/backup" "$was_running"
    if [[ ${#ROLLBACK_ERRORS[@]} -gt 0 ]]; then
        rollback_failed "$work"
        return 1
    fi
    die "Upgrade failed and was rolled back to $(cat "$PREFIX/VERSION" 2>/dev/null || echo 'the previous version')."
}

rollback_error() { ROLLBACK_ERRORS+=("$*"); }

# No stored token (automatic updates are off): use one from the environment for this run only. It is written to a
# root-only temporary file because updater.py reads the token from a file, never from its command line.
token_from_environment() {
    local token="${UPDATE_GITHUB_TOKEN:-${INSTALL_GITHUB_TOKEN:-}}"
    [[ -n $token ]] || return 1
    [[ $token =~ ^[A-Za-z0-9_]+$ ]] || die "The GitHub token in the environment does not look like one (letters, digits and _ only)."
    TOKEN_TMP_DIR="$(mktemp -d)"  # mode 700, owned by root
    trap remove_token_copy EXIT  # before writing: a failed write must not leave the folder behind
    TOKEN_FILE="$TOKEN_TMP_DIR/github-token"
    (umask 077 && printf '%s\n' "$token" >"$TOKEN_FILE")  # printf is a builtin: not in any process list
}

# shellcheck disable=SC2317,SC2329  # called from the EXIT traps (SC2317 on older shellcheck)
remove_token_copy() {
    if [[ -n $TOKEN_TMP_DIR ]]; then rm -rf "$TOKEN_TMP_DIR"; fi
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

restore_files() {  # restore_files BACKUP WAS_RUNNING; failures go to ROLLBACK_ERRORS, never abort
    local src=$1 was_running=$2 entry
    if command -v systemctl >/dev/null 2>&1; then systemctl stop "$SERVICE" 2>/dev/null || true; fi
    while IFS= read -r -d '' entry; do
        rm -rf "$entry" || rollback_error "could not remove $entry"
    done < <(program_entries)
    cp -a "$src/files/." "$PREFIX/" || rollback_error "could not copy the program files back into $PREFIX"
    cp -a "$src/units/." /etc/systemd/system/ || rollback_error "could not restore the systemd unit files"
    if command -v systemctl >/dev/null 2>&1 && [[ -f $UNIT_FILE ]]; then
        systemctl daemon-reload || rollback_error "systemctl daemon-reload failed"
        if ! systemctl restart "$SERVICE" && [[ $was_running -eq 1 ]]; then
            rollback_error "systemctl restart $SERVICE failed"
        fi
    fi
    return 0
}

verify_rollback() {  # verify_rollback BACKUP WAS_RUNNING
    local src=$1 was_running=$2
    if [[ -f $src/files/VERSION ]] && ! cmp -s "$src/files/VERSION" "$PREFIX/VERSION"; then
        rollback_error "$PREFIX/VERSION does not match the backup"
    fi
    if [[ ! -x $VENV_PY || ! -f $PREFIX/Main.py ]]; then
        rollback_error "the restored installation is incomplete (no Main.py or virtual environment)"
    fi
    if [[ $was_running -eq 1 ]]; then
        # Same idea as install.sh: give it a moment, then it must still be up (Restart= would show "activating").
        sleep 10
        if ! systemctl is-active --quiet "$SERVICE"; then
            rollback_error "the $SERVICE service is not running again"
        fi
    fi
    return 0
}

rollback_failed() {  # rollback_failed WORKDIR: report, keep the backup, print manual recovery steps
    local work=$1 keep="" error
    if install -d -m 700 -o root -g root "$BACKUP_KEEP_DIR" 2>/dev/null; then
        keep="$BACKUP_KEEP_DIR/backup-$(date +%Y%m%d-%H%M%S)"
        if ! mv "$work/backup" "$keep" 2>/dev/null; then keep=""; fi
    fi
    if [[ -z $keep ]]; then
        trap - EXIT  # could not move it: keep the whole work folder instead of deleting it
        trap remove_token_copy EXIT  # (but never a temporary copy of the token)
        keep="$work/backup"
    fi
    for error in "${ROLLBACK_ERRORS[@]}"; do
        log "ROLLBACK FAILED: $error"
    done
    cat <<TXT
ROLLBACK FAILED: the upgrade failed and the previous version could not be fully restored.
The backup of the previous program files was kept in: $keep
Manual recovery, as root:
  systemctl stop $SERVICE
  find $PREFIX -mindepth 1 -maxdepth 1 ! -name .env ! -name data ! -name logs ! -name updater -exec rm -rf {} +
  cp -a $keep/files/. $PREFIX/
  cp -a $keep/units/. /etc/systemd/system/ && systemctl daemon-reload
  systemctl start $SERVICE && journalctl -u $SERVICE -n 50 --no-pager
Or run install.sh from the extracted release folder of the version you want. Delete the backup afterwards.
TXT
}

main "$@"; exit $?
