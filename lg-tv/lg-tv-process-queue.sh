#!/usr/bin/env bash
# Drain LG TV optimize queue (one transcode at a time; re-queue on failure up to MAX_RETRIES).
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"
QUEUE=$LG_TV_QUEUE
LOCK=$LG_TV_LOCK
RETRIES=$LG_TV_RETRIES
DEAD=$LG_TV_DEAD
LOG=$LG_TV_LOG
SCRIPT="${LGTV_SCRIPTS}/lg-tv-optimize-media.sh"
PER_RUN="${LG_TV_OPTIMIZE_PER_RUN:-1}"
MAX_RETRIES="${LG_TV_OPTIMIZE_MAX_RETRIES:-4}"
# DRAIN=1: process until the queue file is empty (still one encode at a time).
DRAIN="${LG_TV_OPTIMIZE_DRAIN:-0}"
GAP_SEC="${LG_TV_OPTIMIZE_GAP_SEC:-0}"

exec 200>"$LOCK"
flock -n 200 || exit 0

mkdir -p "$(dirname "$QUEUE")" "$(dirname "$RETRIES")" "$(dirname "$LOG")"
touch "$QUEUE" "$RETRIES"
[[ -s "$QUEUE" ]] || exit 0

atomic_write() {
  local dest=$1
  local dir
  dir=$(dirname "$dest")
  local tmp
  tmp=$(mktemp "${dir}/.lg-tv-atomic.XXXXXX") || return 1
  cat > "$tmp"
  mv -f "$tmp" "$dest"
}

trim_stale_retries() {
  [[ -f "$RETRIES" ]] || return 0
  while IFS=$'\t' read -r rpath rcount; do
    [[ -n "$rpath" && -f "$rpath" ]] || continue
    printf '%s\t%s\n' "$rpath" "${rcount:-0}"
  done < "$RETRIES" | atomic_write "$RETRIES" || true
}
trim_stale_retries

log() { echo "$(date -Iseconds) $*" >> "$LOG"; }

normalize_path() {
  local p=$1
  if [[ "$p" == /data/media/* ]]; then
    echo "/mnt/jellyfin/${p#/data/}"
  else
    echo "$p"
  fi
}

retry_count() {
  local path=$1
  awk -F '\t' -v p="$path" '$1 == p { print $2; found=1; exit } END { if (!found) print 0 }' "$RETRIES"
}

set_retry_count() {
  local path=$1 count=$2
  awk -F '\t' -v p="$path" -v c="$count" '
    $1 == p { next }
    NF { print }
  ' "$RETRIES" | {
    if [[ "$count" -gt 0 ]]; then
      cat
      printf '%s\t%s\n' "$path" "$count"
    else
      cat
    fi
  } | atomic_write "$RETRIES" || true
}

clear_retry() {
  set_retry_count "$1" 0
}

queue_append() {
  local path=$1
  grep -Fxq "$path" "$QUEUE" 2>/dev/null || echo "$path" >> "$QUEUE"
}

# Put failed paths back at the front of the queue (deduped).
queue_requeue_front() {
  local path=$1
  local tmp
  tmp=$(mktemp "${LG_TV_DIR}/.requeue.XXXXXX") || return 1
  {
    echo "$path"
    grep -vxF "$path" "$QUEUE" 2>/dev/null || true
  } | awk '!seen[$0]++ && NF' > "$tmp"
  mv -f "$tmp" "$QUEUE"
}

on_failure() {
  local path=$1
  local n=$(( $(retry_count "$path") + 1 ))
  if [[ "$n" -ge "$MAX_RETRIES" ]]; then
    set_retry_count "$path" "$n"
    {
      echo "$(date -Iseconds) gave up after ${n} attempts: $path"
    } >> "$DEAD"
    log "gave up after ${n} failed attempt(s): $path (see $DEAD)"
    return 0
  fi
  set_retry_count "$path" "$n"
  queue_requeue_front "$path"
  log "re-queued at front (attempt ${n}/${MAX_RETRIES}): $path"
}

process_queue_batch() {
  mapfile -t lines < <(
    grep -v '^[[:space:]]*$' "$QUEUE" 2>/dev/null | awk '!seen[$0]++' || true
  )
  [[ ${#lines[@]} -eq 0 ]] && return 1

  local -a pending=()
  local attempted=0 path normalized

  for path in "${lines[@]}"; do
    [[ -n "$path" ]] || continue
    normalized=$(normalize_path "$path")
    if [[ "$attempted" -lt "$PER_RUN" ]]; then
      path=$normalized
      attempted=$((attempted + 1))
    else
      pending+=("$normalized")
    fi
  done

  [[ "$attempted" -eq 0 ]] && return 1

  # Persist tail before encode so restart mid-job does not lose the backlog.
  if [[ ${#pending[@]} -gt 0 ]]; then
    printf '%s\n' "${pending[@]}" | awk '!seen[$0]++' | atomic_write "$QUEUE" || return 1
  else
    : | atomic_write "$QUEUE" || return 1
  fi

  local prior_failures vcodec_hint=""
  prior_failures=$(retry_count "$path")
  if [[ "$prior_failures" -gt 0 ]]; then
    local backoff=$(( prior_failures < 20 ? prior_failures * 15 : 300 ))
    log "retry backoff ${backoff}s (prior failures=${prior_failures}): $path"
    sleep "$backoff"
  fi
  vcodec_hint=$(
    rel="${path#/mnt/jellyfin/media/}"
    cin="/media/${rel}"
    qin=$(printf %q "$cin")
    docker exec jellyfin bash -c "/usr/lib/jellyfin-ffmpeg/ffprobe -v error -select_streams v:0 \
      -show_entries stream=codec_name -of csv=p=0 ${qin} 2>/dev/null" | head -1 | tr -d '[:space:]'
  )
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

if [[ "$DRAIN" == 1 ]]; then
  while process_queue_batch; do
    if [[ "$GAP_SEC" =~ ^[0-9]+$ && "$GAP_SEC" -gt 0 ]]; then
      sleep "$GAP_SEC"
    fi
  done
else
  process_queue_batch || true
fi
