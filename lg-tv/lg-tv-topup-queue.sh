#!/usr/bin/env bash
# If the LG optimize queue is empty but work remains, enqueue the backlog (no-op when busy).
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

[[ -s "$LG_TV_QUEUE" ]] && exit 0
pgrep -f 'lg-tv-optimize-media\.sh' >/dev/null && exit 0
exec 201>"$LG_TV_LOCK"
flock -n 201 || exit 0
"${LGTV_SCRIPTS}/lg-tv-queue-unoptimized.sh"
