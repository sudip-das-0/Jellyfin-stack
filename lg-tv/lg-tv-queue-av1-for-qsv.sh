#!/usr/bin/env bash
# Queue AV1 library files for QSV transcode (Naruto + any still-AV1 after CPU libx264 runs).
# Prepends paths so they run before the general backlog. Idempotent.
set -euo pipefail
# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/lib/stack-paths.sh

QUEUE=$LG_TV_QUEUE
RETRIES=$LG_TV_RETRIES
SKIPPED=$LG_TV_SKIPPED
LOG=$LG_TV_LOG

log() { echo "$(date -Iseconds) $*" | tee -a "$LOG"; }

vcodec_for() {
  local host_path=$1
  local rel="${host_path#/mnt/jellyfin/media/}"
  docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error \
    -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 \
    "/media/$rel" 2>/dev/null | tr -d '[:space:]'
}

prepend_queue() {
  local -n _paths=$1
  [[ ${#_paths[@]} -eq 0 ]] && return 0
  local tmp
  tmp=$(mktemp "${LG_TV_DIR}/.prepend.XXXXXX")
  {
    printf '%s\n' "${_paths[@]}"
    grep -vxFf <(printf '%s\n' "${_paths[@]}") "$QUEUE" 2>/dev/null || true
  } | awk 'NF && !seen[$0]++' > "$tmp"
  mv -f "$tmp" "$QUEUE"
}

clear_retries() {
  local path=$1
  [[ -f "$RETRIES" ]] || return 0
  awk -F '\t' -v p="$path" '$1 != p { print }' "$RETRIES" > "${RETRIES}.tmp" \
    && mv -f "${RETRIES}.tmp" "$RETRIES"
}

mkdir -p "$(dirname "$QUEUE")"
touch "$QUEUE" "$SKIPPED"

declare -a prepend=()

while IFS= read -r line; do
  [[ "$line" =~ encode\ tier\ ok\ \(libx264\):\ (.+\.mkv)$ ]] || continue
  path="${BASH_REMATCH[1]}"
  [[ -f "$path" ]] || continue
  grep -Fxq "$path" "$SKIPPED" 2>/dev/null && continue
  [[ "$(vcodec_for "$path")" == av1 ]] || continue
  prepend+=("$path")
  clear_retries "$path"
done < "$LOG"

while IFS= read -r -d '' f; do
  grep -Fxq "$f" "$SKIPPED" 2>/dev/null && continue
  [[ "$(vcodec_for "$f")" == av1 ]] || continue
  prepend+=("$f")
done < <(find "/mnt/jellyfin/media/anime/Naruto Shippuden" -type f -name '*AV1*.mkv' -print0 2>/dev/null)

if [[ ${#prepend[@]} -gt 0 ]]; then
  mapfile -t prepend < <(printf '%s\n' "${prepend[@]}" | awk '!seen[$0]++')
  prepend_queue prepend
  log "queue-av1-for-qsv: prepended ${#prepend[@]} AV1 path(s) for QSV"
fi

systemctl --user start --no-block lg-tv-optimize.service 2>/dev/null || true
echo "Prepended ${#prepend[@]} AV1 file(s) for QSV transcode."
