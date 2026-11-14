#!/usr/bin/env bash
# Shared SSD mount + docker stack helpers (guard + recover).
set -euo pipefail

_jellyfin_drive_lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/stack-paths.sh
source "${_jellyfin_drive_lib}/../lib/stack-paths.sh"
unset _jellyfin_drive_lib

JELLYFIN_GUARD_CONTAINERS=(qbittorrent sonarr radarr jellyfin)
JELLYFIN_STACK_CONTAINERS=(qbittorrent sonarr radarr prowlarr bazarr jellyseerr jellyfin)
JELLYFIN_ENSURE_MOUNT="${HOST_DIR}/ensure-jellyfin-media-mount.sh"

jellyfin_mount_ssd() {
  if mountpoint -q "$JELLYFIN_MNT" 2>/dev/null; then
    return 0
  fi
  mkdir -p "$JELLYFIN_MNT"
  if sudo -n systemctl start mnt-jellyfin.mount 2>/dev/null; then
    sleep 1
    mountpoint -q "$JELLYFIN_MNT" && return 0
  fi
  if sudo -n mount "UUID=${JELLYFIN_UUID}" "$JELLYFIN_MNT" 2>/dev/null; then
    return 0
  fi
  sudo -n mount "$JELLYFIN_MNT" 2>/dev/null
}

jellyfin_remount_ssd() {
  if mountpoint -q "$JELLYFIN_MNT" \
    && "$JELLYFIN_ENSURE_MOUNT" >/dev/null 2>&1; then
    return 0
  fi
  if sudo -n systemctl restart mnt-jellyfin.mount 2>/dev/null; then
    sleep 2
    if mountpoint -q "$JELLYFIN_MNT" \
      && "$JELLYFIN_ENSURE_MOUNT" >/dev/null 2>&1; then
      return 0
    fi
  fi
  sudo -n umount -l "$JELLYFIN_MNT" 2>/dev/null || true
  sleep 1
  jellyfin_mount_ssd || return 1
  "$JELLYFIN_ENSURE_MOUNT"
}

# Mount if needed, remount when host or qBit bind is stale; ends with ensure-jellyfin-media-mount.
jellyfin_prepare_volume() {
  if ! mountpoint -q "$JELLYFIN_MNT" 2>/dev/null; then
    jellyfin_mount_ssd || return 1
  elif ! test -r "$TORRENTS_ROOT"; then
    jellyfin_remount_ssd || return 1
  elif docker ps -q -f name=^qbittorrent$ 2>/dev/null | grep -q . \
    && ! docker exec qbittorrent test -r /data/torrents 2>/dev/null; then
    jellyfin_remount_ssd || return 1
  elif ! "$JELLYFIN_ENSURE_MOUNT" >/dev/null 2>&1; then
    jellyfin_remount_ssd || return 1
  fi
  "$JELLYFIN_ENSURE_MOUNT"
}

jellyfin_docker_start_or_restart() {
  local c="$1"
  if docker ps -q -f "name=^${c}$" 2>/dev/null | grep -q .; then
    docker restart "$c"
  else
    docker start "$c"
  fi
}

jellyfin_restart_stack() {
  local skip_jellyfin="${1:-0}"
  local c
  for c in "${JELLYFIN_STACK_CONTAINERS[@]}"; do
    if [[ "$skip_jellyfin" == "1" && "$c" == jellyfin ]]; then
      continue
    fi
    jellyfin_docker_start_or_restart "$c"
  done
}

jellyfin_start_guard_stack() {
  local c any=0
  for c in "${JELLYFIN_GUARD_CONTAINERS[@]}"; do
    if ! docker ps -q -f "name=^${c}$" 2>/dev/null | grep -q .; then
      docker start "$c" >/dev/null 2>&1 && any=1 || true
    fi
  done
  (( any )) && sleep 3
}

jellyfin_download_stack_down() {
  local c
  for c in qbittorrent sonarr radarr; do
    docker ps -q -f "name=^${c}$" 2>/dev/null | grep -q . || return 0
  done
  return 1
}

jellyfin_qbit_pause_all() {
  command -v python3 >/dev/null 2>&1 || return 0
  [[ -r "${LIB_DIR}/arr_common.py" ]] || return 0
  docker ps -q -f name=^qbittorrent$ 2>/dev/null | grep -q . || return 0
  python3 <<PY || true
import os, sys, urllib.parse
sys.path.insert(0, os.environ.get("SCRIPTS_ROOT", "/mnt/jellyfin/scripts") + "/lib")
from arr_common import qbit_json
body = urllib.parse.urlencode({"hashes": "all"}).encode()
qbit_json("/api/v2/torrents/pause", method="POST", data=body)
PY
}