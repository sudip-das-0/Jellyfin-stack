#!/usr/bin/env bash
# Drain the episode image-refresh queue and refresh the affected series in Jellyfin.
set -euo pipefail
# shellcheck source=../lib/stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

QUEUE="${STALL_LOG_DIR}/jellyfin-episode-image-refresh.queue"
[[ -s "$QUEUE" ]] || exit 0

# queue_drain empties the queue under the same lock the hooks append with, so nothing is lost.
mapfile -t paths < <(queue_drain "$QUEUE" | sort -u)
[[ ${#paths[@]} -gt 0 ]] || exit 0

args=()
for p in "${paths[@]}"; do
  [[ -n "$p" ]] && args+=(--path "$p")
done
[[ ${#args[@]} -gt 0 ]] || exit 0

if ! /usr/bin/python3 "${JF_SCRIPTS}/jellyfin-refresh-episode-images.py" "${args[@]}"; then
  # Jellyfin down or API error: put the batch back so the next drain retries it.
  queue_append "$QUEUE" "${paths[@]}"
  exit 1
fi
