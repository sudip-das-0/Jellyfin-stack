#!/usr/bin/env bash
# Re-queue LG optimize paths that hit max retries (after encode fixes).
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"
DEAD=$LG_TV_DEAD
RETRIES=$LG_TV_RETRIES
QUEUE_HELPER="${HOOKS_DIR}/lg-tv-queue-path.sh"

[[ -f "$DEAD" ]] || exit 0

tmp=$(mktemp)
requeued=0
while IFS= read -r line; do
  [[ "$line" =~ :\ (.+\.(mkv|mp4))$ ]] || continue
  path="${BASH_REMATCH[1]}"
  [[ -f "$path" ]] || continue
  if [[ -f "$RETRIES" ]]; then
    awk -F '\t' -v p="$path" '$1 != p { print }' "$RETRIES" > "${RETRIES}.tmp" && mv -f "${RETRIES}.tmp" "$RETRIES"
  fi
  "$QUEUE_HELPER" "$path"
  requeued=$((requeued + 1))
  echo "requeued: $path"
done < "$DEAD"
: > "$DEAD"
echo "Re-queued $requeued file(s) from dead letter."
