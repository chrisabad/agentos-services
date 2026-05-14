#!/usr/bin/env bash
# launchctl-load.sh — idempotent loader for all agentos-services
# Usage: bash deploy/launchctl-load.sh [service...]
#   service: memory | notifications | reports | broker (default: memory notifications reports)
#
# Creates ~/.agentos/services.env if missing. Populate it with:
#   export PAPERCLIP_BOARD_KEY=pcp_board_...

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$HOME/.agentos/logs"
VENV_DIR="$REPO_ROOT/.venv"
SERVICES_ENV="$HOME/.agentos/services.env"

uid="$(id -u)"
domain="gui/$uid"

mkdir -p "$LOG_DIR"
mkdir -p "$(dirname "$SERVICES_ENV")"

if [ ! -f "$SERVICES_ENV" ]; then
  echo "[install] WARNING: $SERVICES_ENV not found — creating stub"
  echo "# agentos-services shared env — sourced by launchd plists at startup" > "$SERVICES_ENV"
  echo "# Populate PAPERCLIP_BOARD_KEY before starting services" >> "$SERVICES_ENV"
  echo "export PAPERCLIP_BOARD_KEY=" >> "$SERVICES_ENV"
  chmod 600 "$SERVICES_ENV"
  echo "[install] WARNING: set PAPERCLIP_BOARD_KEY in $SERVICES_ENV then re-run"
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "[install] creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
echo "[install] installing project (editable) into $VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT"

if [ $# -eq 0 ]; then
  ACTIVE_SERVICES=(memory notifications reports)
else
  ACTIVE_SERVICES=("$@")
fi

for svc in "${ACTIVE_SERVICES[@]}"; do
  label="com.agentos.$svc-service"
  plist_src="$REPO_ROOT/deploy/$label.plist"
  plist_dest="$HOME/Library/LaunchAgents/$label.plist"

  if [ ! -f "$plist_src" ]; then
    echo "[install] skipping $svc — no plist at $plist_src"
    continue
  fi

  mkdir -p "$(dirname "$plist_dest")"
  cp "$plist_src" "$plist_dest"

  launchctl bootout "$domain" "$plist_dest" 2>/dev/null || true
  launchctl bootstrap "$domain" "$plist_dest"
  launchctl kickstart -k "$domain/$label"

  echo "[install] $label loaded"
done

echo "[install] done — tail logs: tail -f $LOG_DIR/*.log"
