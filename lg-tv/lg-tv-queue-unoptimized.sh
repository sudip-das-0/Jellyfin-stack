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
unskip_tmp=$(mktemp)
touch "$LG_TV_SKIPPED" 2>/dev/null || true

declare -A skipset=() queued=()
while IFS= read -r path; do
  [[ -n "$path" ]] && skipset["$path"]=1
done < "$LG_TV_SKIPPED"

while IFS= read -r path; do
  [[ -n "$path" && -f "$path" ]] || continue
  if [[ -n "${skipset[$path]:-}" ]]; then
  rc=0
    /usr/bin/python3 "${LIB_DIR}/lg_tv_storage.py" --needs-optimize "$path" >/dev/null 2>&1 || rc=$?
    if [[ "$rc" -eq 1 ]]; then
      # Still LG-friendly — keep on optimize.skipped
      skipped=$((skipped + 1))
      continue
    fi
    if [[ "$rc" -eq 0 ]]; then
      echo "$path" >>"$unskip_tmp"
    fi
    # rc 2 = probe fail: queue anyway (optimize script will log errors)
  fi
  if [[ "$DRY" -eq 1 ]]; then
    echo "would queue: $path"
    added=$((added + 1))
    continue
  fi
  if [[ -n "${queued[$path]:-}" ]]; then
    skipped=$((skipped + 1))
    continue
  fi
  queued["$path"]=1
  echo "$path" >>"$merge_tmp"
  added=$((added + 1))
done < "$paths_file"
rm -f "$paths_file"

if [[ "$DRY" -eq 0 ]]; then
  if [[ -s "$unskip_tmp" ]]; then
    _drop_unskipped() { awk 'NR == FNR { drop[$0]; next } !($0 in drop)' "$unskip_tmp" -; }
    queue_filter "$LG_TV_SKIPPED" _drop_unskipped || true
  fi
  # Merge into whatever is already queued (hooks may have appended while the scan ran).
  touch "$LG_TV_QUEUE"
  _merge_queue() { cat; cat "$merge_tmp"; }
  queue_filter "$LG_TV_QUEUE" _merge_queue
fi
rm -f "$merge_tmp" "$unskip_tmp"

"$LGTV_SCRIPTS/lg-tv-normalize-queue.sh" 2>/dev/null || true

echo "${stats:-needs_optimize=${count}} newly_queued=${added} already_in_queue=${skipped} dry_run=${DRY}"
if [[ "$DRY" -eq 0 ]]; then
  systemctl --user start --no-block lg-tv-optimize.service lg-tv-new-media-watch.service 2>/dev/null || true
fi
