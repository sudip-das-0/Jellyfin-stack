#!/usr/bin/env bash
# Queue every .m2ts/.ts under the library for remux → .mkv (movies, series, anime).
set -euo pipefail
# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/stack-paths.sh

MEDIA=/mnt/jellyfin/media
QUEUE_HELPER="${HOOKS_DIR}/lg-tv-queue-path.sh"
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && {
  echo "Usage: lg-tv-queue-m2ts.sh [--dry-run]"
  exit 0
}

export LG_TV_QUEUE_NO_START=1
queued=0
skipped=0

while IFS= read -r path; do
  [[ -n "$path" ]] || continue
  if grep -Fxq "$path" "$LG_TV_SKIPPED" 2>/dev/null; then
    skipped=$((skipped + 1))
    continue
  fi
  if grep -Fxq "$path" "$LG_TV_QUEUE" 2>/dev/null; then
    skipped=$((skipped + 1))
    continue
  fi
  if [[ "$DRY" -eq 1 ]]; then
    echo "would queue: $path"
    queued=$((queued + 1))
    continue
  fi
  "$QUEUE_HELPER" "$path"
  queued=$((queued + 1))
done < <(lg_tv_find_videos "$MEDIA" 2>/dev/null | awk 'tolower($0) ~ /\.(m2ts|ts)$/')

echo "m2ts_queue queued=${queued} skipped=${skipped} dry_run=${DRY}"
if [[ "$DRY" -eq 0 && "$queued" -gt 0 ]]; then
  systemctl --user start --no-block lg-tv-optimize.service 2>/dev/null || true
fi
