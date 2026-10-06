#!/usr/bin/env bash
# Drain LG TV optimize queue (one transcode at a time; re-queue on failure up to MAX_RETRIES).
set -euo pipefail
# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/stack-paths.sh
QUEUE=$LG_TV_QUEUE
LOCK=$LG_TV_LOCK
RETRIES=$LG_TV_RETRIES
DEAD=$LG_TV_DEAD
LOG=$LG_TV_LOG
SCRIPT="${SCRIPTS_ROOT}/lg-tv-optimize-media.sh"
PER_RUN="${LG_TV_OPTIMIZE_PER_RUN:-1}"
MAX_RETRIES="${LG_TV_OPTIMIZE_MAX_RETRIES:-3}"
# DRAIN=1: process until the queue file is empty (still one encode at a time).
DRAIN="${LG_TV_OPTIMIZE_DRAIN:-0}"
GAP_SEC="${LG_TV_OPTIMIZE_GAP_SEC:-0}"

exec 200>"$LOCK"
flock -n 200 || exit 0

mkdir -p "$(dirname "$QUEUE")" "$(dirname "$RETRIES")" "$(dirname "$LOG")"
touch "$QUEUE" "$RETRIES"
[[ -s "$QUEUE" ]] || exit 0

trim_stale_retries() {
  [[ -f "$RETRIES" ]] || return 0
  local tmp
  tmp=$(mktemp)
  while IFS=$'\t' read -r rpath rcount; do
    [[ -n "$rpath" && -f "$rpath" ]] || continue
    printf '%s\t%s\n' "$rpath" "${rcount:-0}"
  done < "$RETRIES" > "$tmp"
  mv -f "$tmp" "$RETRIES"
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
  local tmp
  tmp=$(mktemp)
  awk -F '\t' -v p="$path" -v c="$count" '
    $1 == p { next }
    NF { print }
  ' "$RETRIES" > "$tmp"
  if [[ "$count" -gt 0 ]]; then
    printf '%s\t%s\n' "$path" "$count" >> "$tmp"
  fi
  mv -f "$tmp" "$RETRIES"
}

clear_retry() {
  set_retry_count "$1" 0
}

queue_append() {
  local path=$1
  grep -Fxq "$path" "$QUEUE" 2>/dev/null || echo "$path" >> "$QUEUE"
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
  queue_append "$path"
  log "re-queued (attempt ${n}/${MAX_RETRIES}): $path"
}

process_queue_batch() {
  mapfile -t lines < <(grep -v '^[[:space:]]*$' "$QUEUE" 2>/dev/null || true)
  [[ ${#lines[@]} -eq 0 ]] && return 1
  : > "$QUEUE"
  local attempted=0 path

  for path in "${lines[@]}"; do
    [[ -n "$path" ]] || continue
    path=$(normalize_path "$path")
    if [[ "$attempted" -lt "$PER_RUN" ]]; then
      if "$SCRIPT" "$path"; then
        clear_retry "$path"
        attempted=$((attempted + 1))
      else
        on_failure "$path"
        attempted=$((attempted + 1))
      fi
    else
      queue_append "$path"
    fi
  done
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
