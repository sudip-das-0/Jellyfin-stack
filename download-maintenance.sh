#!/usr/bin/env bash
# Dedupe queue/torrents first, then stalled-torrent recovery (single torrent per item only).
set -uo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/stack-paths.sh"
MAINT_LOCK="${STALL_LOG_DIR}/download-maintenance.lock"
MAINT_LOG="${STALL_LOG_DIR}/recovery-$(date +%Y-%m-%d).log"
DEFAULT_STEP_TIMEOUT="${MAINT_STEP_TIMEOUT_SEC:-900}"

exec 9>"$MAINT_LOCK"
if ! flock -n 9; then
  echo "[maintenance] skipped — previous run still active" >&2
  exit 0
fi

maint_log() {
  echo "$(date -Iseconds) [maintenance] $*" | tee -a "$MAINT_LOG" >&2
}

maint_run() {
  local label=$1
  local timeout_sec=$2
  shift 2
  maint_log "start: $label (timeout ${timeout_sec}s)"
  if timeout "$timeout_sec" "$@"; then
    maint_log "ok: $label"
    return 0
  fi
  local rc=$?
  if [[ "$rc" -eq 124 ]]; then
    maint_log "TIMEOUT: $label after ${timeout_sec}s — continuing chain"
  else
    maint_log "FAILED ($rc): $label"
  fi
  return 0
}

maint_run prune-stall-logs "$DEFAULT_STEP_TIMEOUT" \
  /usr/bin/python3 "${SCRIPTS_ROOT}/prune-stall-logs.py"
maint_run sync-manual-deletes 180 \
  /usr/bin/python3 "${SCRIPTS_ROOT}/sync-manual-deletes.py"
maint_run unmonitor-on-import 60 \
  /usr/bin/python3 "${SCRIPTS_ROOT}/unmonitor-on-import.py" --drain-queue
maint_run queue-dedupe "$DEFAULT_STEP_TIMEOUT" \
  /usr/bin/python3 "${SCRIPTS_ROOT}/queue-dedupe.py"
maint_run torrent-stall-recovery "$DEFAULT_STEP_TIMEOUT" \
  /usr/bin/python3 "${SCRIPTS_ROOT}/torrent-stall-recovery.py"
maint_run sonarr-prune-command-queue "$DEFAULT_STEP_TIMEOUT" \
  /usr/bin/python3 "${SCRIPTS_ROOT}/sonarr-prune-command-queue.py" || true
maint_run reconcile-torrents-library "$DEFAULT_STEP_TIMEOUT" \
  /usr/bin/python3 "${SCRIPTS_ROOT}/reconcile-torrents-library.py" --skip-dedupe-cleanup
maint_run cleanup-qbit-missing 120 \
  /usr/bin/python3 "${SCRIPTS_ROOT}/cleanup-qbit-missing.py"
maint_run cleanup-qbit-completed 120 \
  /usr/bin/python3 "${SCRIPTS_ROOT}/cleanup-qbit-completed.py"
maint_run cleanup-partial-series "$DEFAULT_STEP_TIMEOUT" \
  /usr/bin/python3 "${SCRIPTS_ROOT}/cleanup-partial-series.py"
maint_run lg-tv-topup-queue 300 \
  "${SCRIPTS_ROOT}/lg-tv-topup-queue.sh" || true
maint_run jellyfin-episode-images 300 \
  /usr/bin/python3 "${SCRIPTS_ROOT}/jellyfin-refresh-episode-images.py" --all-missing --limit-series 10
maint_run jellyfin-episode-image-queue 120 \
  "${SCRIPTS_ROOT}/jellyfin-process-episode-image-refresh.sh" || true
if ! timeout 300 /usr/bin/python3 "${SCRIPTS_ROOT}/series-season-completeness.py"; then
  /usr/bin/python3 "${SCRIPTS_ROOT}/sonarr-prune-command-queue.py" || true
  timeout 120 /usr/bin/python3 "${SCRIPTS_ROOT}/sonarr-queue-missing-if-due.py" || true
  maint_log "WARN: incomplete monitored TV season(s) — see ${STALL_LOG_DIR}/series-completeness.log"
fi
exit 0
