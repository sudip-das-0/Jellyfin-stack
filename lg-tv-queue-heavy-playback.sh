#!/usr/bin/env bash
# Queue large / remux / m2ts movies for lg-tv-optimize (TV-friendly H.264 + stereo AAC .mkv).
set -euo pipefail
# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/stack-paths.sh

MOVIES="/mnt/jellyfin/media/movies"
MIN_GB="${LG_TV_HEAVY_MIN_GB:-10}"
QUEUE_HELPER="${HOOKS_DIR}/lg-tv-queue-path.sh"
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && {
  echo "Usage: lg-tv-queue-heavy-playback.sh [--dry-run]"
  echo "  Queues movies >= \${LG_TV_HEAVY_MIN_GB:-10} GiB, *.m2ts, or REMUX in filename."
  echo "  Process with: lg-tv-optimize.service (same queue as library optimize)."
  exit 0
}

export LG_TV_QUEUE_NO_START=1
min_bytes=$((MIN_GB * 1024 * 1024 * 1024))
queued=0
skipped=0

while IFS= read -r -d '' path; do
  base=${path##*/}
  low=${base,,}
  ext=${base##*.}
  ext_low=${ext,,}
  size=$(stat -c '%s' "$path" 2>/dev/null || echo 0)
  heavy=0
  [[ "$size" -ge "$min_bytes" ]] && heavy=1
  [[ "$ext_low" == "m2ts" || "$ext_low" == "ts" ]] && heavy=1
  [[ "$low" == *remux* ]] && heavy=1
  [[ "$heavy" -eq 1 ]] || continue

  if grep -Fxq "$path" "$LG_TV_SKIPPED" 2>/dev/null; then
    skipped=$((skipped + 1))
    continue
  fi
  if [[ "$DRY" -eq 1 ]]; then
    echo "would queue ($(numfmt --to=iec-i --suffix=B "$size" 2>/dev/null || echo "${size}B")): $path"
    queued=$((queued + 1))
    continue
  fi
  if grep -Fxq "$path" "$LG_TV_QUEUE" 2>/dev/null; then
    skipped=$((skipped + 1))
    continue
  fi
  "$QUEUE_HELPER" "$path"
  queued=$((queued + 1))
done < <(find "$MOVIES" -type f \( -name '*.mkv' -o -name '*.mp4' -o -name '*.m2ts' -o -name '*.ts' \) -print0 2>/dev/null)

echo "heavy_playback queued=${queued} skipped=${skipped} min_gib=${MIN_GB} dry_run=${DRY}"
if [[ "$DRY" -eq 0 && "$queued" -gt 0 ]]; then
  systemctl --user start --no-block lg-tv-optimize.service 2>/dev/null || true
fi
