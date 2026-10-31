#!/usr/bin/env bash
# Record all existing library videos in watcher.baseline (stops poll-mode re-queue spam).
set -euo pipefail
# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/stack-paths.sh
MEDIA=/mnt/jellyfin/media
BASELINE=$LG_TV_WATCHER_BASELINE

mkdir -p "$(dirname "$BASELINE")"
tmp=$(mktemp)
lg_tv_find_videos "$MEDIA" 2>/dev/null | sort -u > "$tmp"
if [[ -f "$BASELINE" ]]; then
  cat "$BASELINE" >> "$tmp"
fi
sort -u "$tmp" -o "$BASELINE"
rm -f "$tmp"
echo "watcher baseline: $(wc -l < "$BASELINE") paths"
