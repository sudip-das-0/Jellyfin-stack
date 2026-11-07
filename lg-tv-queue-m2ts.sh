#!/usr/bin/env bash
# Purpose: Queue .m2ts/.ts under the library for LG remux.
# Runs: manual
# Depends: stack-paths.sh, hooks/lg-tv-queue-lib.sh
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/stack-paths.sh"
# shellcheck source=hooks/lg-tv-queue-lib.sh
source "${HOOKS_DIR}/lg-tv-queue-lib.sh"

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
  lg_tv_enqueue_path "$path" "$DRY" || true
  rc=$?
  if [[ "$rc" -eq 2 ]]; then
    skipped=$((skipped + 1))
  elif [[ "$rc" -eq 0 ]]; then
    queued=$((queued + 1))
  fi
done < <(lg_tv_find_videos "$MEDIA_ROOT" 2>/dev/null | awk 'tolower($0) ~ /\.(m2ts|ts)$/')

echo "m2ts_queue queued=${queued} skipped=${skipped} dry_run=${DRY}"
lg_tv_maybe_start_worker "$queued" "$DRY"
