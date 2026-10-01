#!/usr/bin/env bash
# Starts the Twitch Discord bot on Linux/macOS.
#
# - Always runs from the repository folder (the bot reads/writes .env and
#   *.json files relative to the working directory).
# - Creates a virtual environment in .venv on first run and (re)installs
#   requirements.txt whenever it changes.
# - Use --watch to restart the bot automatically if it exits/crashes.
#   (Not needed under systemd, which restarts it itself.)
#
# Environment overrides:
#   PYTHON=python3.11   interpreter used to create the venv
#   RESTART_DELAY=10    seconds to wait between restarts in --watch mode
set -euo pipefail

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BOT_DIR"

PYTHON="${PYTHON:-python3}"
VENV_DIR="$BOT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"
REQ_FILE="$BOT_DIR/requirements.txt"
REQ_STAMP="$VENV_DIR/.requirements.sha256"
RESTART_DELAY="${RESTART_DELAY:-10}"

WATCH=0
for arg in "$@"; do
    case "$arg" in
        --watch) WATCH=1 ;;
        -h|--help)
            echo "Usage: $0 [--watch]"
            exit 0 ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

log() { echo "[start-bot] $(date '+%Y-%m-%d %H:%M:%S') $*"; }

if [[ ! -f .env ]]; then
    log "ERROR: .env not found in $BOT_DIR (needs TOKEN, TWITCH-APP-ID, TWITCH-APP-SECRET)." >&2
    exit 1
fi

if [[ ! -x "$VENV_PY" ]]; then
    command -v "$PYTHON" >/dev/null 2>&1 || { log "ERROR: '$PYTHON' not found. Install Python 3 or set PYTHON=..." >&2; exit 1; }
    log "Creating virtual environment in .venv"
    "$PYTHON" -m venv "$VENV_DIR"
fi

if [[ -f "$REQ_FILE" ]]; then
    want="$(sha256sum "$REQ_FILE" | cut -d' ' -f1)"
    have="$(cat "$REQ_STAMP" 2>/dev/null || true)"
    if [[ "$want" != "$have" ]]; then
        log "Installing requirements"
        "$VENV_PY" -m pip install --disable-pip-version-check -q -r "$REQ_FILE"
        echo "$want" > "$REQ_STAMP"
    fi
fi

export PYTHONUNBUFFERED=1

if [[ "$WATCH" -eq 0 ]]; then
    log "Starting bot"
    exec "$VENV_PY" Main.py
fi

trap 'log "Stopping"; kill "${child:-}" 2>/dev/null || true; exit 0' INT TERM
while true; do
    log "Starting bot"
    "$VENV_PY" Main.py &
    child=$!
    set +e; wait "$child"; code=$?; set -e
    log "Bot exited with code $code, restarting in ${RESTART_DELAY}s"
    sleep "$RESTART_DELAY"
done
