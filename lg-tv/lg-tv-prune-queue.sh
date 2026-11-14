#!/usr/bin/env bash
# Drop queue entries that no longer need optimize (ffprobe), and remove .lgopt.tmp junk lines.
set -euo pipefail
# shellcheck source=../lib/stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

[[ -f "$LG_TV_QUEUE" ]] || exit 0

mapfile -t paths < <(grep -v '.lgopt\.tmp' "$LG_TV_QUEUE" 2>/dev/null | sort -u || true)
tmp_count=$(grep -c '.lgopt\.tmp' "$LG_TV_QUEUE" 2>/dev/null || echo 0)
kept=0
pruned=0
new_lines=()

for path in "${paths[@]}"; do
  [[ -n "$path" ]] || continue
  if [[ ! -f "$path" ]]; then
    pruned=$((pruned + 1))
    continue
  fi
  rc=0
  /usr/bin/python3 "${LIB_DIR}/lg_tv_storage.py" --needs-optimize "$path" >/dev/null 2>&1 || rc=$?
  if [[ "$rc" -eq 1 ]]; then
    pruned=$((pruned + 1))
    [[ "$DRY" -eq 1 ]] && echo "prune (lg-friendly): $path"
    continue
  fi
  new_lines+=("$path")
  kept=$((kept + 1))
done

if [[ "$DRY" -eq 1 ]]; then
  echo "would_keep=${kept} would_prune=${pruned} tmp_junk=${tmp_count}"
  exit 0
fi

queue_drain "$LG_TV_QUEUE" >/dev/null || true
for path in "${new_lines[@]}"; do
  queue_append_unique "$LG_TV_QUEUE" "$path" || true
done

echo "kept=${kept} pruned=${pruned} removed_tmp_lines=${tmp_count}"
"$LGTV_SCRIPTS/lg-tv-normalize-queue.sh" 2>/dev/null || true