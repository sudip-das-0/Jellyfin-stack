#!/usr/bin/env bash
# Drain LG TV optimize queue (one transcode at a time; re-queue on failure up to MAX_RETRIES).
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"
lg_tv_optimize_enabled || exit 0
lg_tv_ensure_jellyfin || exit 1
QUEUE=$LG_TV_QUEUE
LOCK=$LG_TV_LOCK
RETRIES=$LG_TV_RETRIES
DEAD=$LG_TV_DEAD
SCRIPT="${LGTV_SCRIPTS}/lg-tv-optimize-media.sh"
PER_RUN="${LG_TV_OPTIMIZE_PER_RUN:-1}"
MAX_RETRIES="${LG_TV_OPTIMIZE_MAX_RETRIES:-4}"
# DRAIN=1: process until the queue file is empty (still one encode at a time).
DRAIN="${LG_TV_OPTIMIZE_DRAIN:-0}"
GAP_SEC="${LG_TV_OPTIMIZE_GAP_SEC:-0}"

exec 200>"$LOCK"
flock -n 200 || exit 0

mkdir -p "$(dirname "$QUEUE")" "$(dirname "$RETRIES")" "$(dirname "$LG_TV_LOG")"
touch "$QUEUE" "$RETRIES"
[[ -s "$QUEUE" ]] || exit 0

log() { lg_tv_log "$@"; }

# Retry file is "path<TAB>count"; hooks also edit it, so every change goes through queue_filter.
_keep_existing_retries() {
  local rpath rcount
  while IFS=$'\t' read -r rpath rcount; do
    [[ -n "$rpath" && -f "$rpath" ]] && printf '%s\t%s\n' "$rpath" "${rcount:-0}"
  done
  return 0
}
queue_filter "$RETRIES" _keep_existing_retries || true

retry_count() {
  awk -F '\t' -v p="$1" '$1 == p { print $2; found = 1; exit } END { if (!found) print 0 }' "$RETRIES"
}

set_retry_count() {
  local path=$1 count=$2
  _drop_then_add() {
    awk -F '\t' -v p="$path" '$1 != p && NF'
    if [[ "$count" -gt 0 ]]; then
      printf '%s\t%s\n' "$path" "$count"
    fi
  }
  queue_filter "$RETRIES" _drop_then_add || true
}

clear_retry() { set_retry_count "$1" 0; }

on_failure() {
  local path=$1 n
  n=$(( $(retry_count "$path") + 1 ))
  set_retry_count "$path" "$n"
  if [[ "$n" -ge "$MAX_RETRIES" ]]; then
    echo "$(date -Iseconds) gave up after ${n} attempts: $path" >>"$DEAD"
    log "gave up after ${n} failed attempt(s): $path (see $DEAD)"
    return 0
  fi
  queue_push_front "$QUEUE" "$path"
  log "re-queued at front (attempt ${n}/${MAX_RETRIES}): $path"
}

probe_vcodec() {
  local path=$1 cin
  cin="/media/${path#"${MEDIA_ROOT}"/}"
  docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error -select_streams v:0 \
    -show_entries stream=codec_name -of csv=p=0 "$cin" 2>/dev/null | head -1 | tr -d '[:space:]' || true
}

# Encode the head of the queue. Returns 1 when the queue is empty.
process_one() {
  local path prior_failures vcodec_hint

  # Aliases (/data/media/...) collapse to host paths before the head is taken.
  queue_filter "$QUEUE" lg_tv_normalize_stream || true
  path=$(queue_pop_front "$QUEUE")
  [[ -n "$path" ]] || return 1

  prior_failures=$(retry_count "$path")
  if [[ "$prior_failures" -gt 0 ]]; then
    local backoff=$(( prior_failures < 20 ? prior_failures * 15 : 300 ))
    log "retry backoff ${backoff}s (prior failures=${prior_failures}): $path"
    sleep "$backoff"
  fi

  vcodec_hint=$(probe_vcodec "$path")
  export LG_TV_PROBE_VCODEC="$vcodec_hint"
  if [[ "$vcodec_hint" == "av1" && "$prior_failures" -ge "${LG_TV_AV1_HW_ATTEMPTS:-3}" \
    && "${LG_TV_ALLOW_CPU_FALLBACK:-0}" == 1 ]]; then
    export LG_TV_AV1_ALLOW_CPU=1
    log "AV1 libx264 fallback enabled (prior failures=${prior_failures}): $path"
  else
    export LG_TV_AV1_ALLOW_CPU=0
  fi

  if "$SCRIPT" "$path"; then
    clear_retry "$path"
  else
    on_failure "$path"
  fi
  return 0
}

# Up to PER_RUN encodes, one at a time. Returns 1 when nothing was processed.
process_queue_batch() {
  local processed=0
  while [[ "$processed" -lt "$PER_RUN" ]] && process_one; do
    processed=$((processed + 1))
  done
  [[ "$processed" -gt 0 ]]
}

if [[ "$DRAIN" == 1 ]]; then
  while process_queue_batch; do
    if [[ "$GAP_SEC" =~ ^[0-9]+$ && "$GAP_SEC" -gt 0 ]]; then
      sleep "$GAP_SEC"
    fi
  done
else
  process_queue_batch || true
fi
