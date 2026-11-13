#!/usr/bin/env bash
# Install LG encodes from ~/Downloads/processed/ into the library (backup -> *.lgopt-backup).
#
# Matches by basename under $MEDIA_ROOT. Skips files with no unique library match.
#
# Usage: lg-tv-promote-processed.sh [processed_dir]
set -euo pipefail
# shellcheck source=../lib/stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

PROCESSED="${1:-${LG_TV_PROCESSED_DIR:-${HOME}/Downloads/processed}}"

log() { echo "$(date -Iseconds) $*"; }

find_library_path() {
  local base="$1"
  local -a hits
  mapfile -d '' hits < <(find "$MEDIA_ROOT" -type f -name "$base" -print0 2>/dev/null)
  if [[ "${#hits[@]}" -eq 1 ]]; then
    printf '%s' "${hits[0]}"
    return 0
  fi
  if [[ "${#hits[@]}" -eq 0 ]]; then
    log "skip (no library match): $base"
  else
    log "skip (ambiguous ${#hits[@]} matches): $base"
  fi
  return 1
}

promote_one() {
  local src="$1" lib backup base
  base="$(basename "$src")"
  lib="$(find_library_path "$base")" || return 0
  backup="${lib}.lgopt-backup"
  if [[ -f "$backup" ]]; then
    log "rotate backup: ${backup} -> ${backup}.prior"
    mv -f "$backup" "${backup}.prior"
  fi
  log "backup library -> ${backup##*/}"
  mv -f "$lib" "$backup"
  log "install -> ${lib}"
  rsync -a --info=progress2 "$src" "$lib"
  rm -f "$src"
  log "done: $base ($(du -h "$lib" | cut -f1))"
}

shopt -s nullglob
files=("${PROCESSED}"/*.{mkv,mp4,MKV,MP4})
shopt -u nullglob
if [[ "${#files[@]}" -eq 0 ]]; then
  log "nothing to promote in ${PROCESSED}"
  exit 0
fi

for src in "${files[@]}"; do
  [[ -f "$src" ]] || continue
  promote_one "$src"
done
log "promote finished"
