#!/usr/bin/env bash
# launchctl-load.sh — idempotent loader for com.agentos.memory-service
# Usage: bash deploy/launchctl-load.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_SRC="$REPO_ROOT/deploy/com.agentos.memory-service.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.agentos.memory-service.plist"
LOG_DIR="$HOME/.agentos/logs"
VENV_DIR="$REPO_ROOT/.venv"

uid="$(id -u)"
domain="gui/$uid"
label="com.agentos.memory-service"

mkdir -p "$LOG_DIR"
mkdir -p "$(dirname "$PLIST_DEST")"

if [ ! -d "$VENV_DIR" ]; then
  echo "[install] creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
echo "[install] installing project (editable) into $VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT"

cp "$PLIST_SRC" "$PLIST_DEST"

# Bootout if loaded; ignore failure (not loaded yet)
launchctl bootout "$domain" "$PLIST_DEST" 2>/dev/null || true
launchctl bootstrap "$domain" "$PLIST_DEST"
launchctl kickstart -k "$domain/$label"

echo "[install] $label loaded"
echo "[install] tail log: tail -f $LOG_DIR/memory-service.log"
