#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
GENERATED_DIR="$SCRIPT_DIR/generated"
STATE_FILE="$DATA_DIR/state.json"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/cursor-wallpaper.log"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python3"

mkdir -p "$DATA_DIR" "$GENERATED_DIR" "$LOG_DIR"

if [[ -x "$VENV_PYTHON" ]]; then
  PYTHON="$VENV_PYTHON"
else
  PYTHON="python3"
fi

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"
}

read_state_value() {
  local key="$1"
  "$PYTHON" - "$STATE_FILE" "$key" <<'PY'
import json
import sys

state_path, key = sys.argv[1], sys.argv[2]
try:
    with open(state_path, encoding="utf-8") as handle:
        state = json.load(handle)
except (FileNotFoundError, json.JSONDecodeError):
    raise SystemExit(0)
value = state.get(key)
if isinstance(value, str) and value:
    print(value)
PY
}

write_state() {
  local last_generated="$1"
  local previous_generated="${2:-}"
  "$PYTHON" - "$STATE_FILE" "$last_generated" "$previous_generated" <<'PY'
import json
import sys
from datetime import datetime, timezone

state_path, last_generated, previous_generated = sys.argv[1], sys.argv[2], sys.argv[3]
state = {}
try:
    with open(state_path, encoding="utf-8") as handle:
        state = json.load(handle)
except FileNotFoundError:
    pass

if previous_generated:
    state["previous_generated"] = previous_generated
elif "last_generated" in state and state["last_generated"] != last_generated:
    state["previous_generated"] = state["last_generated"]

state["last_generated"] = last_generated
state["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat()
with open(state_path, "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2)
PY
}

# Keep the newest image plus one previous fallback; delete older ones.
cleanup_generated_dir() {
  local keep_current="$1"
  local keep_previous="${2:-}"
  local removed=0

  shopt -s nullglob
  for file in "$GENERATED_DIR"/*; do
    [[ -f "$file" ]] || continue
    if [[ "$file" == "$keep_current" || ( -n "$keep_previous" && "$file" == "$keep_previous" ) ]]; then
      continue
    fi
    rm -f "$file"
    log "Deleted old generated image: $file"
    removed=$((removed + 1))
  done
  shopt -u nullglob

  if [[ "$removed" -eq 0 ]]; then
    log "No old generated images to delete."
  fi
}

set_wallpaper() {
  local image_path="$1"
  # Prefer AppKit via Swift so launchd can update wallpaper without
  # Terminal -> System Events Automation permission.
  if /usr/bin/swift "$SCRIPT_DIR/set_wallpaper.swift" "$image_path"; then
    return 0
  fi

  log "Swift wallpaper setter failed; trying AppleScript fallback..."
  /usr/bin/osascript <<EOF
tell application "System Events"
  set picture of every desktop to POSIX file "$image_path"
end tell
EOF
}

restore_previous_wallpaper() {
  local reason="$1"
  local fallback=""
  local candidate

  for candidate in "$(read_state_value last_generated)" "$(read_state_value previous_generated)"; do
    if [[ -n "$candidate" && -f "$candidate" ]]; then
      fallback="$candidate"
      break
    fi
  done

  if [[ -z "$fallback" ]]; then
    log "$reason No previous generated wallpaper available to restore."
    return 1
  fi

  log "$reason Restoring previous wallpaper: $fallback"
  if set_wallpaper "$fallback"; then
    log "Previous wallpaper restored."
    return 0
  fi

  log "Failed to restore previous wallpaper: $fallback"
  return 1
}

fail_and_restore() {
  local reason="$1"
  restore_previous_wallpaper "$reason" || true
  exit 1
}

main() {
  if [[ ! -f "$SCRIPT_DIR/config.env" ]]; then
    log "Missing config.env. Copy config.env.example to config.env and fill in your values."
    exit 1
  fi

  if [[ ! -f "$DATA_DIR/base_wallpaper.png" ]]; then
    log "Missing base wallpaper. Run ./install.sh first."
    exit 1
  fi

  if ! "$PYTHON" - "$DATA_DIR/base_wallpaper.png" <<'PY'
import sys
from pathlib import Path
from PIL import Image

path = Path(sys.argv[1])
with Image.open(path) as img:
    if img.size == (1920, 1080) and img.convert("RGB").getpixel((10, 10)) == (30, 60, 120):
        raise SystemExit(1)
PY
  then
    log "Base wallpaper is still the placeholder image."
    log "Set your real wallpaper in System Settings -> Wallpaper, then run:"
    log "  ./install.sh --refresh-base"
    exit 1
  fi

  PREVIOUS_IMAGE="$(read_state_value last_generated || true)"
  if [[ -n "$PREVIOUS_IMAGE" && ! -f "$PREVIOUS_IMAGE" ]]; then
    PREVIOUS_IMAGE=""
  fi

  log "Fetching Cursor usage data..."
  if ! "$PYTHON" "$SCRIPT_DIR/fetch.py" --output "$DATA_DIR/latest.json"; then
    fail_and_restore "Fetch failed."
  fi

  NEW_IMAGE="$GENERATED_DIR/cursor-wallpaper-$(date '+%Y%m%d-%H%M%S').png"
  log "Rendering watermark to $NEW_IMAGE"
  if ! "$PYTHON" "$SCRIPT_DIR/render.py" --output "$NEW_IMAGE"; then
    rm -f "$NEW_IMAGE"
    fail_and_restore "Render failed."
  fi

  log "Setting desktop wallpaper..."
  if ! set_wallpaper "$NEW_IMAGE"; then
    rm -f "$NEW_IMAGE"
    fail_and_restore "Failed to set wallpaper."
  fi

  cleanup_generated_dir "$NEW_IMAGE" "$PREVIOUS_IMAGE"
  write_state "$NEW_IMAGE" "$PREVIOUS_IMAGE"
  log "Wallpaper updated successfully."
}

main "$@"
