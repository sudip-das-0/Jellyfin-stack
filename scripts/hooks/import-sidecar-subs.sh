#!/usr/bin/env bash
# Copy English subs from release folder only (.eng.srt / *english* / *.en.*).
set -euo pipefail

dest="${sonarr_episodefile_path:-${radarr_moviefile_path:-}}"
[[ -n "$dest" ]] || exit 0

stem="${dest%.*}"
shopt -s nullglob

is_english_name() {
  local base=$1
  local l=${base,,}
  [[ "$l" == *eng* || "$l" == *.en.* || "$l" == *english* ]]
}

copy_from_dir() {
  local dir=$1
  [[ -d "$dir" ]] || return 1
  local copied=0 f
  [[ -f "${stem}.eng.srt" ]] && return 0
  for f in "$dir"/*.srt "$dir"/*/*.srt; do
    [[ -f "$f" ]] || continue
    is_english_name "$(basename "$f")" || continue
    cp -f "$f" "${stem}.eng.srt"
    copied=1
    break
  done
  [[ "$copied" -eq 1 ]]
}

for folder in \
  "${sonarr_episodefile_sourcefolder:-}" \
  "${sonarr_sourcefolder:-}" \
  "${radarr_moviefile_sourcefolder:-}" \
  "${radarr_sourcefolder:-}"; do
  copy_from_dir "$folder" && exit 0
  copy_from_dir "$folder/Subs" && exit 0
done

exit 0
