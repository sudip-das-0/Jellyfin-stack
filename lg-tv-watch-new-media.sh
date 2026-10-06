#!/usr/bin/env bash
# Queue LG TV optimize for newly added media only (existing library ignored at first start).
set -euo pipefail
MEDIA=/mnt/jellyfin/media
QUEUE_HELPER=/mnt/jellyfin/scripts/lg-tv-queue-path.sh
# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/stack-paths.sh
BASELINE=$LG_TV_WATCHER_BASELINE
POLL_SEC=45

mkdir -p "$(dirname "$BASELINE")"
if [[ ! -f "$BASELINE" ]]; then
  find "$MEDIA" -type f \( -name '*.mkv' -o -name '*.mp4' \) 2>/dev/null | sort -u > "$BASELINE"
  echo "LG TV watcher: recorded $(wc -l < "$BASELINE") existing files (will not optimize)"
fi

queue_if_new() {
  local file=$1
  grep -Fxq "$file" "$BASELINE" 2>/dev/null && return 0
  echo "$file" >> "$BASELINE"
  "$QUEUE_HELPER" "$file"
}

if command -v inotifywait >/dev/null 2>&1; then
  echo "LG TV new-media watcher (inotify) on $MEDIA"
  inotifywait -m -r -e close_write -e moved_to --format '%w%f' "$MEDIA" | while read -r file; do
    case "$file" in
      *.mkv|*.mp4) queue_if_new "$file" ;;
    esac
  done
else
  echo "LG TV new-media watcher (poll every ${POLL_SEC}s; install inotify-tools for instant queue)" >&2
  while true; do
    while IFS= read -r file; do
      case "$file" in
        *.mkv|*.mp4) queue_if_new "$file" ;;
      esac
    done < <(find "$MEDIA" -type f \( -name '*.mkv' -o -name '*.mp4' \) 2>/dev/null || true)
    sleep "$POLL_SEC"
  done
fi
