#!/usr/bin/env bash
# Queue media taller than MIN_HEIGHT for LG TV optimize (e.g. existing 4K after removing LG_TV_MAX_HEIGHT cap).
set -euo pipefail

MIN_H="${LG_TV_QUEUE_MIN_HEIGHT:-2160}"
# shellcheck source=stack-paths.sh
source /home/devdas/arr/scripts/stack-paths.sh
QUEUE=$LG_TV_QUEUE
MEDIA_ROOT=/mnt/jellyfin/media

usage() {
  echo "Usage: lg-tv-queue-min-height.sh [--dry-run]"
  echo "  MIN_HEIGHT from env LG_TV_QUEUE_MIN_HEIGHT (default: 2160 = 4K UHD)."
  exit 0
}

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && usage

mkdir -p "$(dirname "$QUEUE")"
touch "$QUEUE"

queued=0
skipped=0

while IFS= read -r -d '' src; do
  rel="${src#"$MEDIA_ROOT"/}"
  h=$(docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error -select_streams v:0 \
    -show_entries stream=height -of default=nw=1:nk=1 "/media/$rel" 2>/dev/null | tr -d '\r\n ')
  [[ "$h" =~ ^[0-9]+$ ]] || continue
  if [[ "$h" -lt "$MIN_H" ]]; then
    continue
  fi
  if grep -Fxq "$src" "$QUEUE" 2>/dev/null; then
    skipped=$((skipped + 1))
    continue
  fi
  if [[ "$DRY" -eq 1 ]]; then
    echo "would queue (${h}p): $src"
  else
    echo "$src" >> "$QUEUE"
  fi
  queued=$((queued + 1))
done < <(find "$MEDIA_ROOT/movies" "$MEDIA_ROOT/series" "$MEDIA_ROOT/anime" \
  -type f \( -name '*.mkv' -o -name '*.mp4' \) -print0 2>/dev/null)

echo "min height=${MIN_H}p queued=${queued} already_in_queue=${skipped} dry_run=${DRY}"
if [[ "$DRY" -eq 0 && "$queued" -gt 0 ]]; then
  systemctl --user start --no-block lg-tv-optimize.service 2>/dev/null || true
fi
