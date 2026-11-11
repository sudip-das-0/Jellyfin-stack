#!/usr/bin/env bash
# Poll for jellyfin USB SSD; pause stack while absent. Quiet unless state changes.
set -uo pipefail
GUARD_HOME="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=stack-paths.sh
source "${GUARD_HOME}/../lib/stack-paths.sh"

export SCRIPTS_ROOT
LOG="${GUARD_LOG:-${HOME}/arr/jellyfin-drive-guard/drive-guard.log}"
STATE="${GUARD_STATE:-${HOME}/arr/jellyfin-drive-guard/drive-guard.state}"
RECOVER_STAMP="${GUARD_HOME}/drive-guard.recover-stamp"
UUID_DEV="/dev/disk/by-uuid/${JELLYFIN_UUID}"
DOWNLOADERS=(qbittorrent sonarr radarr jellyfin)
RECOVER_MIN_INTERVAL="${DRIVE_GUARD_RECOVER_INTERVAL_SEC:-300}"
MOUNT_UNIT="mnt-jellyfin.mount"

log() {
  local line
  line="$(date -Iseconds) [jellyfin-drive-guard] $*"
  mkdir -p "$(dirname "$LOG")"
  echo "$line" >>"$LOG"
}

read_state() {
  [[ -f "$STATE" ]] && cat "$STATE" || echo "healthy"
}

write_state() {
  mkdir -p "$(dirname "$STATE")"
  echo "$1" >"$STATE"
}

ssd_block_device_present() {
  [[ -e "$UUID_DEV" ]]
}

try_mount_ssd() {
  if mountpoint -q "$JELLYFIN_MNT" 2>/dev/null; then
    return 0
  fi
  if ! ssd_block_device_present; then
    return 1
  fi
  mkdir -p "$JELLYFIN_MNT"
  if sudo -n mount "UUID=${JELLYFIN_UUID}" "$JELLYFIN_MNT" 2>/dev/null; then
    log "mounted UUID=${JELLYFIN_UUID} at ${JELLYFIN_MNT}"
    return 0
  fi
  # fstab / polkit: allow passwordless or let admin configure sudoers for mount
  if sudo mount "UUID=${JELLYFIN_UUID}" "$JELLYFIN_MNT" 2>/dev/null; then
    log "mounted UUID=${JELLYFIN_UUID} at ${JELLYFIN_MNT}"
    return 0
  fi
  return 1
}

remount_if_stale() {
  if ! mountpoint -q "$JELLYFIN_MNT" 2>/dev/null; then
    return 1
  fi
  if "${GUARD_HOME}/ensure-jellyfin-media-mount.sh" >/dev/null 2>&1; then
    return 0
  fi
  log "stale mount at ${JELLYFIN_MNT} — lazy unmount and remount when disk is present"
  sudo umount -l "$JELLYFIN_MNT" 2>/dev/null || true
  try_mount_ssd || return 1
  "${GUARD_HOME}/ensure-jellyfin-media-mount.sh" >/dev/null 2>&1
}

stop_stack() {
  local c
  for c in "${DOWNLOADERS[@]}"; do
    if docker ps -q -f "name=^${c}$" 2>/dev/null | grep -q .; then
      docker stop "$c" >/dev/null 2>&1 || true
      log "stopped container $c"
    fi
  done
}

start_stack() {
  local c
  for c in "${DOWNLOADERS[@]}"; do
    if ! docker ps -q -f "name=^${c}$" 2>/dev/null | grep -q .; then
      docker start "$c" >/dev/null 2>&1 || true
      log "started container $c"
    fi
  done
}

qbit_pause_all() {
  command -v python3 >/dev/null 2>&1 || return 0
  [[ -r "${LIB_DIR}/arr_common.py" ]] || return 0
  python3 <<PY || true
import os, sys, urllib.parse
sys.path.insert(0, os.environ.get("SCRIPTS_ROOT", "/mnt/jellyfin/scripts") + "/lib")
from arr_common import qbit_json
body = urllib.parse.urlencode({"hashes": "all"}).encode()
qbit_json("/api/v2/torrents/pause", method="POST", data=body)
PY
}

maybe_recover_mount() {
  local now last=0
  now=$(date +%s)
  [[ -f "$RECOVER_STAMP" ]] && last=$(cat "$RECOVER_STAMP" 2>/dev/null || echo 0)
  (( now - last < RECOVER_MIN_INTERVAL )) && return 0
  echo "$now" >"$RECOVER_STAMP"
  if [[ -x "${HOST_DIR}/recover-jellyfin-drive.sh" ]]; then
    log "running ${HOST_DIR}/recover-jellyfin-drive.sh"
    RECOVER_SKIP_JELLYFIN=0 "${HOST_DIR}/recover-jellyfin-drive.sh" >>"$LOG" 2>&1 || \
      log "recover-jellyfin-drive failed"
  else
    start_stack
    sleep 5
    qbit_pause_all >/dev/null 2>&1 || true
  fi
}

# --- main (always exit 0 so systemd does not spam failures while SSD is unplugged) ---

prev=$(read_state)
poll_state="waiting"

if ssd_block_device_present; then
  try_mount_ssd || true
  remount_if_stale || true
fi

if "${GUARD_HOME}/ensure-jellyfin-media-mount.sh" >/dev/null 2>&1; then
  poll_state="healthy"
  if [[ "$prev" != "healthy" ]]; then
    log "SSD ready — restoring media stack"
    maybe_recover_mount
  fi
  write_state healthy
  exit 0
fi

poll_state="waiting"
if [[ "$prev" == "healthy" ]]; then
  log "SSD not ready — waiting for UUID=${JELLYFIN_UUID} (poll every 5s)"
fi

if docker ps -q -f name=^qbittorrent$ 2>/dev/null | grep -q .; then
  qbit_pause_all >/dev/null 2>&1 || true
fi
stop_stack
write_state waiting
exit 0
