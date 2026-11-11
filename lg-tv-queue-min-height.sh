#!/usr/bin/env bash
# Purpose: Queue library videos at or above MIN_HEIGHT for LG TV optimize.
# Runs: manual
# Depends: stack-paths.sh, hooks/lg-tv-queue-lib.sh
set -euo pipefail

MIN_H="${LG_TV_QUEUE_MIN_HEIGHT:-2160}"
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/stack-paths.sh"
# shellcheck source=hooks/lg-tv-queue-lib.sh
source "${HOOKS_DIR}/lg-tv-queue-lib.sh"

usage() {
  echo "Usage: lg-tv-queue-min-height.sh [--dry-run]"
  echo "  MIN_HEIGHT from env LG_TV_QUEUE_MIN_HEIGHT (default: 2160 = 4K UHD)."
  exit 0
}

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && usage

queued=0
skipped=0

while IFS= read -r src; do
  rel="${src#"$MEDIA_ROOT"/}"
  h=$(docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error -select_streams v:0 \
    -show_entries stream=height -of default=nw=1:nk=1 "/media/$rel" 2>/dev/null | tr -d '\r\n ')
  [[ "$h" =~ ^[0-9]+$ ]] || continue
  [[ "$h" -ge "$MIN_H" ]] || continue
  lg_tv_enqueue_path "$src" "$DRY" || true
  rc=$?
  if [[ "$rc" -eq 2 ]]; then
    skipped=$((skipped + 1))
  elif [[ "$rc" -eq 0 ]]; then
    queued=$((queued + 1))
  fi
done < <(lg_tv_find_videos "$MEDIA_ROOT" 2>/dev/null)

echo "min height=${MIN_H}p queued=${queued} already_in_queue=${skipped} dry_run=${DRY}"
lg_tv_maybe_start_worker "$queued" "$DRY"
