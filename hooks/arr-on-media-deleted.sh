#!/usr/bin/env bash
# Sonarr/Radarr: library file deleted — queue path for host sync (no Python in *arr images).
set -euo pipefail

event="${sonarr_eventtype:-${radarr_eventtype:-}}"
if [[ "$event" == "Test" ]]; then
  exit 0
fi

raw="${sonarr_episodefile_path:-${radarr_moviefile_path:-}}"
[[ -n "$raw" ]] || exit 0

if [[ -d /data/logs ]]; then
  QUEUE=/data/logs/stall-recovery/delete-sync.queue
else
  QUEUE=/mnt/jellyfin/logs/stall-recovery/delete-sync.queue
fi
mkdir -p "$(dirname "$QUEUE")"
grep -Fxq "$raw" "$QUEUE" 2>/dev/null || echo "$raw" >> "$QUEUE"
