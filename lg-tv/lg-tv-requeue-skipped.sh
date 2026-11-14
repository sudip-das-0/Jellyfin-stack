#!/usr/bin/env bash
# Re-queue paths from optimize.skipped (policy change). LG-friendly entries stay skipped.
set -euo pipefail
# shellcheck source=../lib/stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

if [[ "$DRY" -eq 1 ]]; then
  [[ -s "$LG_TV_SKIPPED" ]] || { echo "optimize.skipped is empty"; exit 0; }
  mapfile -t paths < "$LG_TV_SKIPPED"
else
  [[ -s "$LG_TV_SKIPPED" ]] || { echo "optimize.skipped is empty"; exit 0; }
  mapfile -t paths < <(queue_drain "$LG_TV_SKIPPED")
fi
requeued=0
kept=0
dropped=0

for path in "${paths[@]}"; do
  [[ -n "$path" ]] || continue
  if [[ ! -f "$path" ]]; then
    dropped=$((dropped + 1))
    continue
  fi
  rc=0
  /usr/bin/python3 "${LIB_DIR}/lg_tv_storage.py" --needs-optimize "$path" >/dev/null 2>&1 || rc=$?
  if [[ "$rc" -eq 1 ]]; then
    if [[ "$DRY" -eq 0 ]]; then
      queue_append_unique "$LG_TV_SKIPPED" "$path" || true
    fi
    kept=$((kept + 1))
    continue
  fi
  if [[ "$DRY" -eq 1 ]]; then
    echo "would requeue: $path"
    requeued=$((requeued + 1))
    continue
  fi
  queue_append_unique "$LG_TV_QUEUE" "$path" && requeued=$((requeued + 1))
done

echo "requeued=${requeued} kept_lg_friendly=${kept} missing=${dropped} dry_run=${DRY}"
[[ "$DRY" -eq 0 && "$requeued" -gt 0 ]] &&
  systemctl --user start --no-block lg-tv-optimize.service 2>/dev/null || true