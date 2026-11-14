#!/usr/bin/env bash
# Re-queue LG optimize paths that hit max retries (after encode fixes).
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"
DEAD=$LG_TV_DEAD
QUEUE_HELPER="${HOOKS_DIR}/lg-tv-queue-path.sh"

[[ -s "$DEAD" ]] || exit 0

# Take the whole dead-letter log at once; entries whose file is gone are dropped.
mapfile -t lines < <(queue_drain "$DEAD")

_drop_retry() { awk -F '\t' -v p="$path" '$1 != p'; }
requeued=0
for line in "${lines[@]}"; do
  if [[ "$line" =~ :\ (.+\.(mkv|mp4))$ ]]; then
    path="${BASH_REMATCH[1]}"
    if [[ -f "$path" ]]; then
      queue_filter "$LG_TV_RETRIES" _drop_retry || true
      "$QUEUE_HELPER" "$path"
      requeued=$((requeued + 1))
      echo "requeued: $path"
      continue
    fi
  fi
done
echo "Re-queued $requeued file(s) from dead letter."
