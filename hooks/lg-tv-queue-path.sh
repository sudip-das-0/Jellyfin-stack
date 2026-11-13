#!/usr/bin/env bash
# Queue a media file for LG TV optimize (host or Sonarr/Radarr container).
set -euo pipefail

[[ "${LG_TV_OPTIMIZE_ENABLED:-0}" == "1" ]] || exit 0

raw="${1:-${sonarr_episodefile_path:-${radarr_moviefile_path:-}}}"
[[ -n "$raw" ]] || exit 0

path="$raw"
if [[ "$raw" == /data/media/* ]]; then
  path="/mnt/jellyfin/${raw#/data/}"
fi

case "$path" in
  *.mkv|*.mp4|*.m2ts|*.ts|*.M2TS|*.TS) ;;
  *) exit 0 ;;
esac

if [[ ! -f "$raw" && ! -f "$path" ]]; then
  exit 0
fi

if [[ -d /data/logs ]]; then
  QUEUE=/data/logs/lg-tv/optimize.queue
  SKIPPED=/data/logs/lg-tv/optimize.skipped
  BASELINE=/data/logs/lg-tv/watcher.baseline
else
  QUEUE=/mnt/jellyfin/logs/lg-tv/optimize.queue
  SKIPPED=/mnt/jellyfin/logs/lg-tv/optimize.skipped
  BASELINE=/mnt/jellyfin/logs/lg-tv/watcher.baseline
fi
RETRIES="$(dirname "$QUEUE")/optimize.retries.tsv"

mkdir -p "$(dirname "$QUEUE")"
touch "$QUEUE" "$SKIPPED"
grep -Fxq "$path" "$SKIPPED" 2>/dev/null && exit 0
grep -Fxq "$path" "$QUEUE" 2>/dev/null && exit 0
echo "$path" >> "$QUEUE"
grep -Fxq "$path" "$BASELINE" 2>/dev/null || echo "$path" >> "$BASELINE"
if [[ "${LG_TV_QUEUE_NO_START:-0}" != 1 ]]; then
  systemctl --user start --no-block lg-tv-optimize.service 2>/dev/null || true
fi

# Bulk queue runs trim on each path; skip here (process-queue trims on drain).
if [[ "${LG_TV_QUEUE_NO_START:-0}" == 1 ]]; then
  exit 0
fi

if [[ -f "$RETRIES" ]]; then
  tmp=$(mktemp)
  data_alias=""
  [[ "$path" == /mnt/jellyfin/media/* ]] && data_alias="/data/${path#/mnt/jellyfin/}"
  awk -F '\t' -v p="$path" -v d="$data_alias" 'NF && $1 != p && (d == "" || $1 != d) { print }' "$RETRIES" > "$tmp"
  mv -f "$tmp" "$RETRIES"
fi
