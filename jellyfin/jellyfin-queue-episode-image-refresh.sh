#!/usr/bin/env bash
# Append a new episode path; drain queue in background (one processor at a time).
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

QUEUE="${STALL_LOG_DIR}/jellyfin-episode-image-refresh.queue"
LOCK="${STALL_LOG_DIR}/jellyfin-episode-image-refresh.lock"
PROCESSOR="/mnt/jellyfin/scripts/jellyfin/jellyfin-process-episode-image-refresh.sh"

path="${1:-}"
[[ -n "$path" ]] || exit 0
case "$path" in
  *.mkv|*.mp4|*.m2ts|*.ts|*.M2TS|*.TS) ;;
  *) exit 0 ;;
esac
case "$path" in
  /mnt/jellyfin/media/*|/data/media/*) ;;
  *) exit 0 ;;
esac

mkdir -p "$(dirname "$QUEUE")"
echo "$path" >> "$QUEUE"

(flock -n "$LOCK" && exec "$PROCESSOR") &
