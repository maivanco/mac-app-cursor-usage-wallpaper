#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
BASE_WALLPAPER="$DATA_DIR/base_wallpaper.png"
VENV_DIR="$SCRIPT_DIR/.venv"
PLIST_LABEL="com.$(whoami).cursor-wallpaper"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/cursor-wallpaper.log"

REFRESH_BASE=0
if [[ "${1:-}" == "--refresh-base" ]]; then
  REFRESH_BASE=1
fi

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

ensure_config() {
  if [[ ! -f "$SCRIPT_DIR/config.env" ]]; then
    cp "$SCRIPT_DIR/config.env.example" "$SCRIPT_DIR/config.env"
    chmod 600 "$SCRIPT_DIR/config.env"
    log "Created config.env from template. Edit it before running updates."
  fi
}

install_python_deps() {
  if [[ ! -d "$VENV_DIR" ]]; then
    log "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
  fi
  log "Installing Python dependencies..."
  "$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
  "$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
}

capture_base_wallpaper() {
  mkdir -p "$DATA_DIR"
  if ! "$VENV_DIR/bin/python3" "$SCRIPT_DIR/capture_base.py" --output "$BASE_WALLPAPER"; then
    exit 1
  fi
  log "Saved base wallpaper to $BASE_WALLPAPER"
}

install_launch_agent() {
  local interval
  interval="$("$VENV_DIR/bin/python3" - "$SCRIPT_DIR/config.env" <<'PY'
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(sys.argv[1]), override=True)
print(int(os.getenv("UPDATE_INTERVAL", "3600")))
PY
)"

  mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"
  cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${SCRIPT_DIR}/update_wallpaper.sh</string>
  </array>
  <key>StartInterval</key>
  <integer>${interval}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_FILE}</string>
  <key>StandardErrorPath</key>
  <string>${LOG_FILE}</string>
</dict>
</plist>
EOF

  launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
  launchctl enable "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1 || true
  log "Installed launch agent: $PLIST_PATH (interval ${interval}s)"
}

chmod_scripts() {
  chmod +x "$SCRIPT_DIR/update_wallpaper.sh" "$SCRIPT_DIR/install.sh"
  chmod +x "$SCRIPT_DIR/fetch.py" "$SCRIPT_DIR/render.py" "$SCRIPT_DIR/capture_base.py"
}

main() {
  chmod_scripts
  ensure_config
  install_python_deps

  if [[ "$REFRESH_BASE" -eq 1 || ! -f "$BASE_WALLPAPER" ]]; then
    capture_base_wallpaper
  else
    log "Base wallpaper already exists. Use --refresh-base to recapture."
  fi

  install_launch_agent
  log "Install complete."
  log "Next steps:"
  log "  1. Edit $SCRIPT_DIR/config.env"
  log "  2. Run $SCRIPT_DIR/update_wallpaper.sh"
  log "  3. Tail logs with: tail -f $LOG_FILE"
}

main "$@"
