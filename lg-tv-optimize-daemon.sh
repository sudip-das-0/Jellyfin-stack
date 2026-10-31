#!/usr/bin/env bash
# Always-on LG TV queue worker: drain back-to-back, then wait for new queue entries.
set -euo pipefail
# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/stack-paths.sh
QUEUE=$LG_TV_QUEUE
LOG=$LG_TV_LOG
PROCESS="${SCRIPTS_ROOT}/lg-tv-process-queue.sh"
IDLE_POLL="${LG_TV_OPTIMIZE_IDLE_POLL_SEC:-2}"

export LG_TV_OPTIMIZE_DRAIN=1
export LG_TV_OPTIMIZE_PER_RUN=1
export LG_TV_OPTIMIZE_GAP_SEC=0

mkdir -p "$(dirname "$QUEUE")" "$(dirname "$LOG")"
touch "$QUEUE"

log() { echo "$(date -Iseconds) [daemon] $*" >> "$LOG"; }

drain_queue() {
  # Do not start a second job while an encode script is still running (avoids overlapping ffmpeg).
  if pgrep -f 'lg-tv-optimize-media\.sh' >/dev/null 2>&1; then
    return 0
  fi
  # Empty queue → process-queue exits immediately (non-blocking for idle loop).
  "$PROCESS" || true
}

log "started (idle poll ${IDLE_POLL}s; inotify on queue when available)"

while true; do
  drain_queue
  if command -v inotifywait >/dev/null 2>&1; then
    inotifywait -q -e modify,create,close_write,move_self,delete_self "$QUEUE" 2>/dev/null || sleep "$IDLE_POLL"
  else
    sleep "$IDLE_POLL"
  fi
done
