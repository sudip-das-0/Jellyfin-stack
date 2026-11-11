#!/usr/bin/env bash
# Purpose: Scheduled download pipeline (dedupe → stall recovery → reconcile → cleanup).
# Runs: torrent-stall-recovery.timer (30 min)
# Depends: stack-paths.sh, ensure-jellyfin-media-mount.sh
set -uo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/lib/stack-paths.sh"
if ! "${HOST_DIR}/ensure-jellyfin-media-mount.sh" >/dev/null 2>&1; then
  echo "[maintenance] skipped — jellyfin SSD not ready (see drive-guard)" >&2
  exit 0
fi
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

# --- Log hygiene + library sync ---
maint_run prune-stall-logs "$DEFAULT_STEP_TIMEOUT" \
  /usr/bin/python3 "${MAINT_DIR}/prune-stall-logs.py"
maint_run jellyfin-delete-guard-sanitize 60 \
  /usr/bin/python3 "${JF_SCRIPTS}/jellyfin-delete-guard.py" --sanitize-all
maint_run jellyfin-delete-guard-ghosts 120 \
  /usr/bin/python3 "${JF_SCRIPTS}/jellyfin-delete-guard.py" --purge-ghosts --ghost-limit 50
maint_run sync-manual-deletes 180 \
  /usr/bin/python3 "${MAINT_DIR}/sync-manual-deletes.py"
maint_run unmonitor-on-import 60 \
  /usr/bin/python3 "${MAINT_DIR}/unmonitor-on-import.py" --drain-queue
# --- qBit queue health ---
maint_run queue-dedupe "$DEFAULT_STEP_TIMEOUT" \
  /usr/bin/python3 "${MAINT_DIR}/queue-dedupe.py"
maint_run torrent-stall-recovery "$DEFAULT_STEP_TIMEOUT" \
  /usr/bin/python3 "${MAINT_DIR}/torrent-stall-recovery.py"
maint_run sonarr-prune-command-queue "$DEFAULT_STEP_TIMEOUT" \
  /usr/bin/python3 "${MAINT_DIR}/sonarr-prune-command-queue.py" || true
# Optional host-only policies (temp/ is gitignored)
shopt -s nullglob
for f in "${TEMP_DIR}/"*-download-policy.py; do
  maint_run "$(basename "$f" .py)" 60 /usr/bin/python3 "$f" || true
done
shopt -u nullglob
# --- Import orphans + qBit hygiene ---
maint_run reconcile-torrents-library "$DEFAULT_STEP_TIMEOUT" \
  /usr/bin/python3 "${MAINT_DIR}/reconcile-torrents-library.py" --skip-dedupe-cleanup
maint_run cleanup-qbit-missing 120 \
  /usr/bin/python3 "${MAINT_DIR}/cleanup-qbit-missing.py"
maint_run cleanup-qbit-completed 120 \
  /usr/bin/python3 "${MAINT_DIR}/cleanup-qbit-completed.py"
maint_run cleanup-partial-series "$DEFAULT_STEP_TIMEOUT" \
  /usr/bin/python3 "${MAINT_DIR}/cleanup-partial-series.py"
# Subtitle timing: LAPSE plugin (new items auto-sync; bulk from apply-lapse-plugin.py)
# --- LG TV + Jellyfin metadata ---
maint_run lg-tv-topup-queue 300 \
  "${LGTV_SCRIPTS}/lg-tv-topup-queue.sh" || true
maint_run jellyfin-episode-images 300 \
  /usr/bin/python3 "${JF_SCRIPTS}/jellyfin-refresh-episode-images.py" --all-missing --limit-series 10
maint_run jellyfin-episode-image-queue 120 \
  "${JF_SCRIPTS}/jellyfin-process-episode-image-refresh.sh" || true
if ! timeout 300 /usr/bin/python3 "${MAINT_DIR}/series-season-completeness.py"; then
  /usr/bin/python3 "${MAINT_DIR}/sonarr-prune-command-queue.py" || true
  timeout 120 /usr/bin/python3 "${MAINT_DIR}/sonarr-search-incomplete-seasons.py" || true
  timeout 120 /usr/bin/python3 "${MAINT_DIR}/sonarr-queue-missing-if-due.py" || true
  shopt -s nullglob
  for f in "${TEMP_DIR}/"*-download-policy.py; do
    timeout 120 /usr/bin/python3 "$f" --search-seasons || true
  done
  shopt -u nullglob
  maint_log "WARN: incomplete monitored TV season(s) — see ${STALL_LOG_DIR}/series-completeness.log"
fi
exit 0
