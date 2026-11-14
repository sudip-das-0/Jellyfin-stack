#!/usr/bin/env bash
# Append a new episode path; drain queue in background (one processor at a time).
set -euo pipefail
# shellcheck source=../lib/stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

QUEUE="${STALL_LOG_DIR}/jellyfin-episode-image-refresh.queue"
LOCK="${STALL_LOG_DIR}/jellyfin-episode-image-refresh.lock"
PROCESSOR="${JF_SCRIPTS}/jellyfin-process-episode-image-refresh.sh"

path="${1:-}"
[[ -n "$path" ]] || exit 0
hook_is_video_path "$path" || exit 0
case "$path" in
  "${MEDIA_ROOT}"/*|/data/media/*) ;;
  *) exit 0 ;;
esac

queue_append_unique "$QUEUE" "$path" || true

(flock -n "$LOCK" && exec "$PROCESSOR") >/dev/null 2>&1 &
