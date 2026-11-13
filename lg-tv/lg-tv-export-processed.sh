#!/usr/bin/env bash
# Safe LG optimize: library -> scratch on $MEDIA_ROOT -> ~/Downloads/processed/ (library untouched).
#
# Usage:
#   lg-tv-export-processed.sh /mnt/jellyfin/media/movies/Foo/bar.mkv [more paths…]
#
# Overrides for one-off REMUX/big titles: LG_TV_SKIP_REMUX=0 LG_TV_MAX_GIB_PER_HOUR=5 …
set -euo pipefail
# shellcheck source=../lib/stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

if [[ $# -eq 0 ]]; then
  echo "Usage: $(basename "$0") /path/under/${MEDIA_ROOT}/video.mkv …" >&2
  exit 1
fi

OPT_SCRIPT="${LGTV_SCRIPTS}/lg-tv-optimize-media.sh"
SCRATCH_ROOT="${MEDIA_ROOT}/.lg-opt-export"
DOWNLOADS_ROOT="${LG_TV_DOWNLOADS_ROOT:-${HOME}/Downloads}"
PROCESSED_DIR="${LG_TV_PROCESSED_DIR:-${DOWNLOADS_ROOT}/processed}"
LOG="${DOWNLOADS_ROOT}/lg-tv-export-processed.log"
LOCK="${SCRATCH_ROOT}/.export-processed.lock"

log() { echo "$(date -Iseconds) $*" | tee -a "$LOG"; }

purge_scratch_skip_entries() {
  [[ -f "${LG_TV_SKIPPED}" ]] || return 0
  sed -i "\|^${SCRATCH_ROOT}/|d" "${LG_TV_SKIPPED}" 2>/dev/null || true
}

file_size() {
  stat -L -c '%s' "${1:?}" 2>/dev/null || echo 0
}

scratch_label() {
  local library="$1"
  echo "$library" | tr -c '[:alnum:]' '_' | cut -c1-56
}

process_one() {
  local library="$1"
  local base workdir work dest lib_sz
  base="$(basename "$library")"
  dest="${PROCESSED_DIR}/${base}"
  workdir="${SCRATCH_ROOT}/$(scratch_label "$library")"
  work="${workdir}/${base}"

  case "$library" in
    "${MEDIA_ROOT}"/*) ;;
    *)
      log "ERROR: not under MEDIA_ROOT: $library"
      return 1
      ;;
  esac
  if [[ ! -f "$library" ]]; then
    log "missing: $library"
    return 1
  fi

  lib_sz="$(file_size "$library")"
  if [[ -f "$dest" && "${LG_TV_FORCE_PROCESSED:-0}" != 1 ]]; then
    local proc_sz
    proc_sz="$(file_size "$dest")"
    if [[ "$proc_sz" -gt 0 && "$proc_sz" -lt "$lib_sz" ]]; then
      log "keep existing processed (smaller): $base"
      return 0
    fi
  fi

  mkdir -p "$PROCESSED_DIR" "$workdir"
  rm -f "$work" "${work}.lgopt.tmp.mkv"
  log "copy -> scratch (GiB/h=${LG_TV_MAX_GIB_PER_HOUR}) $base"
  rsync -a --info=progress2 "$library" "$work"
  if [[ "$(file_size "$work")" != "$lib_sz" ]]; then
    log "ERROR: scratch size mismatch"
    return 1
  fi

  log "optimize $base"
  export LG_TV_DELETE_TORRENT_ORIGINAL=0
  if ! "$OPT_SCRIPT" "$work"; then
    log "optimize failed: $base"
    rm -rf "$workdir"
    return 1
  fi
  purge_scratch_skip_entries

  if [[ ! -s "$work" ]]; then
    log "ERROR: empty output: $base"
    rm -rf "$workdir"
    return 1
  fi

  log "move -> processed/ ($(du -h "$work" | cut -f1))"
  mv -f "$work" "$dest"
  shopt -s nullglob
  for s in "${workdir}/"*.srt; do
    cp -a "$s" "${PROCESSED_DIR}/$(basename "$s")"
  done
  shopt -u nullglob
  rm -rf "$workdir"
  log "done processed/$base"
}

mkdir -p "$PROCESSED_DIR" "$SCRATCH_ROOT"
: >>"$LOG"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "Another export run is active ($LOCK)" >&2
  exit 1
fi

lg_tv_ensure_jellyfin || {
  log "ERROR: jellyfin container not running"
  exit 1
}

log "[batch] export -> ${PROCESSED_DIR} (MAX_GIB_PER_HOUR=${LG_TV_MAX_GIB_PER_HOUR})"
for library in "$@"; do
  process_one "$library" || exit 1
done
log "[batch] finished"
