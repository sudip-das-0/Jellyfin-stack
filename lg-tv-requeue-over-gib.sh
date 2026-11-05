#!/usr/bin/env bash
# Re-queue library files above LG_TV_MAX_GIB_PER_HOUR (default 3.5 GiB/h) for shrink/transcode.
set -euo pipefail
# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/stack-paths.sh

echo "storage target: max ${LG_TV_MAX_GIB_PER_HOUR} GiB/h, encode maxrate ${LG_TV_MAXRATE}"
exec /mnt/jellyfin/scripts/lg-tv-queue-unoptimized.sh "$@"
