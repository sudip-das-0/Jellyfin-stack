#!/usr/bin/env bash
# Poll jellyfin USB SSD; stop stack when mount bad; restore when healthy.
set -uo pipefail
GUARD_HOME="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=jellyfin-drive-lib.sh
source "${GUARD_HOME}/jellyfin-drive-lib.sh"

export SCRIPTS_ROOT
LOG="${GUARD_LOG:-${HOME}/arr/jellyfin-drive-guard/drive-guard.log}"
STATE="${GUARD_STATE:-${HOME}/arr/jellyfin-drive-guard/drive-guard.state}"
HEAL_STAMP="${GUARD_STATE%.state}/heal.stamp"
UUID_DEV="/dev/disk/by-uuid/${JELLYFIN_UUID}"
HEAL_MIN_INTERVAL="${DRIVE_GUARD_HEAL_INTERVAL_SEC:-30}"
GUARD_POLL_SEC="${DRIVE_GUARD_POLL_SEC:-5}"

log() {
  mkdir -p "$(dirname "$LOG")"
  echo "$(date -Iseconds) [jellyfin-drive-guard] $*" >>"$LOG"
}

read_state() { [[ -f "$STATE" ]] && cat "$STATE" || echo "healthy"; }
write_state() { mkdir -p "$(dirname "$STATE")"; echo "$1" >"$STATE"; }

ssd_present() { [[ -e "$UUID_DEV" ]]; }

heal_rate_limited() {
  local now last=0
  now=$(date +%s)
  [[ -f "$HEAL_STAMP" ]] && last=$(cat "$HEAL_STAMP" 2>/dev/null || echo 0)
  (( now - last < HEAL_MIN_INTERVAL )) && return 0
  echo "$now" >"$HEAL_STAMP"
  return 1
}

restore_stack() {
  if [[ -x "${HOST_DIR}/recover-jellyfin-drive.sh" ]]; then
    log "running ${HOST_DIR}/recover-jellyfin-drive.sh"
    if RECOVER_SKIP_JELLYFIN=0 "${HOST_DIR}/recover-jellyfin-drive.sh" >>"$LOG" 2>&1; then
      return 0
    fi
    log "recover-jellyfin-drive failed — docker start (guard set)"
  fi
  jellyfin_start_guard_stack
}

restore_if_needed() {
  local prev="$1"
  if [[ "$prev" != "healthy" ]]; then
    log "SSD ready — restoring media stack"
    restore_stack
    return
  fi
  jellyfin_download_stack_down || return 0
  heal_rate_limited && return 0
  log "mount healthy but download stack down — healing"
  restore_stack
}

prev=$(read_state)

if ssd_present; then
  jellyfin_mount_ssd 2>/dev/null || true
  if mountpoint -q "$JELLYFIN_MNT" 2>/dev/null \
    && ! "$JELLYFIN_ENSURE_MOUNT" >/dev/null 2>&1; then
    log "stale mount at ${JELLYFIN_MNT} — remount"
    jellyfin_remount_ssd >>"$LOG" 2>&1 || true
  fi
fi

if "$JELLYFIN_ENSURE_MOUNT" >/dev/null 2>&1; then
  restore_if_needed "$prev"
  write_state healthy
  exit 0
fi

if [[ "$prev" == "healthy" ]]; then
  log "SSD not ready — waiting for UUID=${JELLYFIN_UUID} (poll every ${GUARD_POLL_SEC}s)"
fi
jellyfin_qbit_pause_all
for c in "${JELLYFIN_GUARD_CONTAINERS[@]}"; do
  if docker ps -q -f "name=^${c}$" 2>/dev/null | grep -q .; then
    docker stop "$c" >/dev/null 2>&1 || true
    log "stopped container $c"
  fi
done
write_state waiting
exit 0