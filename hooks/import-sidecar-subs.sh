#!/usr/bin/env bash
# Copy episode-matched English subs from the release folder to {video}.eng.srt
# (Sonarr/Radarr containers have no Python — logic lives here; see sidecar_release.py on the host.)
set -euo pipefail

dest="${sonarr_episodefile_path:-${radarr_moviefile_path:-}}"
[[ -n "$dest" ]] || exit 0

stem="${dest%.*}"
sidecar="${stem}.eng.srt"
[[ -f "$sidecar" ]] && exit 0

shopt -s nullglob nocaseglob

extract_episode_code() {
  local name=$1
  if [[ "$name" =~ [^0-9][Ss]([0-9]{1,2})[Ee]([0-9]{1,2})[^0-9] ]]; then
    :
  elif [[ "$name" =~ ^[Ss]([0-9]{1,2})[Ee]([0-9]{1,2}) ]]; then
    :
  elif [[ "$name" =~ [Ss]([0-9]{1,2})[Ee]([0-9]{1,2}) ]]; then
    :
  else
    return 1
  fi
  printf 'S%02dE%02d' $((10#${BASH_REMATCH[1]})) $((10#${BASH_REMATCH[2]}))
}

video_ep=$(extract_episode_code "$(basename "$stem")" || true)
video_base=$(basename "$stem")

is_foreign_name() {
  local l=${1,,}
  [[ "$l" =~ \.(jpn|ja|kor|ko|hin|hi|ben|bn|fre|fra|fr|deu|ger|de|spa|es|ita|it|por|pt|rus|ru|chi|zh|zho)\.srt$ ]]
}

is_english_candidate() {
  local l=${1,,}
  is_foreign_name "$1" && return 1
  [[ "$l" == *eng* || "$l" == *.en.* || "$l" == *english* || "$l" == *.srt ]]
}

score_candidate() {
  local name=$1
  local l=${name,,}
  local score=30
  [[ "$l" == *.eng.srt ]] && score=0
  [[ "$l" == *.en.srt ]] && score=10
  [[ "$l" == *.english.srt ]] && score=12
  [[ "$l" == *.srt && "$score" == 30 ]] && score=20
  [[ "$l" == *forced* || "$l" == *sdh* ]] && score=$((score + 25))
  if [[ -n "$video_ep" ]]; then
    local srt_ep
    srt_ep=$(extract_episode_code "$name" || true)
    [[ "$srt_ep" != "$video_ep" ]] && return 1
  else
    local srt_stem=${name%.srt}
    [[ "$video_base" == "$srt_stem" || "$video_base" == "$srt_stem"* ]] || return 1
  fi
  echo "$score"
}

pick_from_dir() {
  local dir=$1
  [[ -d "$dir" ]] || return 1
  local best_score=9999 best_file=
  local f score
  for f in "$dir"/*.srt "$dir"/*/*.srt; do
    [[ -f "$f" ]] || continue
    is_english_candidate "$(basename "$f")" || continue
    score=$(score_candidate "$(basename "$f")" || true)
    [[ -z "$score" ]] && continue
    if (( score < best_score )); then
      best_score=$score
      best_file=$f
    fi
  done
  [[ -n "$best_file" ]] || return 1
  cp -f "$best_file" "$sidecar"
}

for folder in \
  "${sonarr_episodefile_sourcefolder:-}" \
  "${sonarr_sourcefolder:-}" \
  "${radarr_moviefile_sourcefolder:-}" \
  "${radarr_sourcefolder:-}"; do
  [[ -n "$folder" ]] || continue
  pick_from_dir "$folder" && exit 0
  pick_from_dir "$folder/Subs" && exit 0
done

exit 0
