#!/usr/bin/env bash
# Sonarr/Radarr: after import, stop monitoring so completed items are not re-grabbed.
set -euo pipefail

event="${sonarr_eventtype:-${radarr_eventtype:-}}"
if [[ "$event" == "Test" ]]; then
  exit 0
fi

case "$event" in
  DownloadImported|ImportComplete|MovieImport|ManualImportComplete) ;;
  *) exit 0 ;;
esac

if [[ -d /data/logs ]]; then
  SON_Q=/data/logs/stall-recovery/unmonitor-sonarr.queue
  RAD_Q=/data/logs/stall-recovery/unmonitor-radarr.queue
else
  SON_Q=/mnt/jellyfin/logs/stall-recovery/unmonitor-sonarr.queue
  RAD_Q=/mnt/jellyfin/logs/stall-recovery/unmonitor-radarr.queue
fi
mkdir -p "$(dirname "$SON_Q")"

if [[ -n "${sonarr_episodefile_episodeids:-}" ]]; then
  grep -Fxq "${sonarr_episodefile_episodeids}" "$SON_Q" 2>/dev/null \
    || echo "${sonarr_episodefile_episodeids}" >> "$SON_Q"
fi

if [[ -n "${sonarr_episodefile_episodeid:-}" ]]; then
  grep -Fxq "${sonarr_episodefile_episodeid}" "$SON_Q" 2>/dev/null \
    || echo "${sonarr_episodefile_episodeid}" >> "$SON_Q"
fi

if [[ -n "${radarr_movie_id:-}" ]]; then
  grep -Fxq "${radarr_movie_id}" "$RAD_Q" 2>/dev/null \
    || echo "${radarr_movie_id}" >> "$RAD_Q"
fi
