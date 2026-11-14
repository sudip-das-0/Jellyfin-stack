#!/usr/bin/env bash
# Normalize lg-tv-optimize.queue paths to /mnt/jellyfin/... (dedupe /data/media aliases).
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"
[[ -f "$LG_TV_QUEUE" ]] || exit 0

queue_filter "$LG_TV_QUEUE" lg_tv_normalize_stream
echo "normalized queue: $(grep -c . "$LG_TV_QUEUE" || true) unique path(s)"
