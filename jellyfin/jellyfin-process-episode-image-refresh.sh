#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

QUEUE="${STALL_LOG_DIR}/jellyfin-episode-image-refresh.queue"
[[ -f "$QUEUE" ]] || exit 0

mapfile -t paths < <(sort -u "$QUEUE")
: > "$QUEUE"
[[ ${#paths[@]} -gt 0 ]] || exit 0

args=()
for p in "${paths[@]}"; do
  args+=(--path "$p")
done
/usr/bin/python3 /mnt/jellyfin/scripts/jellyfin/jellyfin-refresh-episode-images.py "${args[@]}"
