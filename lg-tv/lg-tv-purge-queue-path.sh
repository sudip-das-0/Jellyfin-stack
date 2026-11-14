#!/usr/bin/env bash
# Remove every queue line for a path (handles duplicate/stale entries).
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"
path="${1:-}"
[[ -n "$path" ]] || exit 0

# grep -v exits 1 when nothing is left, which is a valid (empty) queue.
_without_path() { grep -vxF -- "$path" || true; }
queue_filter "$LG_TV_QUEUE" _without_path
