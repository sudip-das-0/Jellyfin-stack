#!/usr/bin/env bash
# Queue media under /mnt/jellyfin/media that still need LG TV optimize (remux/transcode).
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"
if ! lg_tv_optimize_enabled; then
  echo "LG_TV_OPTIMIZE_ENABLED=0 — not queueing library"
  exit 0
fi

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && {
  echo "Usage: lg-tv-queue-unoptimized.sh [--dry-run]"
  exit 0
}

export LG_TV_QUEUE_NO_START=1

paths_file=$(mktemp)
count_file=$(mktemp)
/usr/bin/python3 "${LIB_DIR}/lg_tv_storage.py" --scan-unoptimized "$MEDIA_ROOT" \
  >"$paths_file" 2>"$count_file"

stats=$(tr -d '\n' < "$count_file" 2>/dev/null || true)
count=$(printf '%s' "$stats" | sed -n 's/.*needs_optimize=\([0-9][0-9]*\).*/\1/p')
count=${count:-0}
rm -f "$count_file"

added=0
skipped=0
if [[ "$DRY" -eq 0 ]]; then
  systemctl --user stop lg-tv-optimize.service lg-tv-new-media-watch.service 2>/dev/null || true
  sleep 1
fi
merge_tmp=$(mktemp)
touch "$LG_TV_SKIPPED" 2>/dev/null || true
while IFS= read -r path; do
  [[ -n "$path" && -f "$path" ]] || continue
  if grep -Fxq "$path" "$LG_TV_SKIPPED" 2>/dev/null; then
    if /usr/bin/python3 "${LIB_DIR}/lg_tv_storage.py" "$path" "${LG_TV_MAX_GIB_PER_HOUR:-0}" >/dev/null 2>&1; then
      skipped=$((skipped + 1))
      continue
    fi
    grep -vxF "$path" "$LG_TV_SKIPPED" > "${LG_TV_SKIPPED}.tmp" 2>/dev/null \
      && mv "${LG_TV_SKIPPED}.tmp" "$LG_TV_SKIPPED" || true
  fi
  if [[ "$DRY" -eq 1 ]]; then
    echo "would queue: $path"
    added=$((added + 1))
    continue
  fi
  if grep -Fxq "$path" "$merge_tmp" 2>/dev/null; then
    skipped=$((skipped + 1))
    continue
  fi
  echo "$path" >>"$merge_tmp"
  added=$((added + 1))
done < "$paths_file"
rm -f "$paths_file"

if [[ "$DRY" -eq 0 ]]; then
  sort -u "$merge_tmp" | awk '!seen[$0]++' >"${merge_tmp}.out"
  mv -f "${merge_tmp}.out" "$LG_TV_QUEUE"
  chmod 600 "$LG_TV_QUEUE"
fi
rm -f "$merge_tmp"

"$LGTV_SCRIPTS/lg-tv-normalize-queue.sh" 2>/dev/null || true

echo "${stats:-needs_optimize=${count}} newly_queued=${added} already_in_queue=${skipped} dry_run=${DRY}"
if [[ "$DRY" -eq 0 ]]; then
  systemctl --user start --no-block lg-tv-optimize.service lg-tv-new-media-watch.service 2>/dev/null || true
fi
