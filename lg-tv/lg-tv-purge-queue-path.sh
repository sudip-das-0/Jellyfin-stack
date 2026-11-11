#!/usr/bin/env bash
# Remove every queue line for a path (handles duplicate/stale entries).
set -euo pipefail
# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/lib/stack-paths.sh
path="${1:-}"
[[ -n "$path" ]] || exit 0
[[ -f "$LG_TV_QUEUE" ]] || exit 0

tmp=$(mktemp "${LG_TV_DIR}/.purge.XXXXXX")
if grep -vxF "$path" "$LG_TV_QUEUE" >"$tmp" 2>/dev/null; then
  if [[ -s "$tmp" ]]; then
    mv -f "$tmp" "$LG_TV_QUEUE"
  else
    : >"$LG_TV_QUEUE"
    rm -f "$tmp"
  fi
else
  rm -f "$tmp"
fi
