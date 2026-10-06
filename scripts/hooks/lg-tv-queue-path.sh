#!/usr/bin/env bash
# Queue a media file for LG TV optimize (host or Sonarr/Radarr container).
set -euo pipefail

raw="${1:-${sonarr_episodefile_path:-${radarr_moviefile_path:-}}}"
[[ -n "$raw" ]] || exit 0

path="$raw"
if [[ "$raw" == /data/media/* ]]; then
  path="/mnt/jellyfin/${raw#/data/}"
fi

case "$path" in
  *.mkv|*.mp4) ;;
  *) exit 0 ;;
esac

if [[ ! -f "$raw" && ! -f "$path" ]]; then
  exit 0
fi

if [[ -d /data/logs ]]; then
  QUEUE=/data/logs/lg-tv/optimize.queue
else
  QUEUE=/mnt/jellyfin/logs/lg-tv/optimize.queue
fi
RETRIES="$(dirname "$QUEUE")/optimize.retries.tsv"

mkdir -p "$(dirname "$QUEUE")"
touch "$QUEUE"
grep -Fxq "$path" "$QUEUE" 2>/dev/null && exit 0
echo "$path" >> "$QUEUE"
if [[ "${LG_TV_QUEUE_NO_START:-0}" != 1 ]]; then
  systemctl --user start --no-block lg-tv-optimize.service 2>/dev/null || true
fi

if [[ -f "$RETRIES" ]]; then
  tmp=$(mktemp)
  data_alias=""
  [[ "$path" == /mnt/jellyfin/media/* ]] && data_alias="/data/${path#/mnt/jellyfin/}"
  awk -F '\t' -v p="$path" -v d="$data_alias" 'NF && $1 != p && (d == "" || $1 != d) { print }' "$RETRIES" > "$tmp"
  mv -f "$tmp" "$RETRIES"
fi
