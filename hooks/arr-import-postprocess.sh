#!/usr/bin/env bash
# Sonarr/Radarr: local subs from release folder, then queue LG TV optimize (single hook).
set -euo pipefail

event="${sonarr_eventtype:-${radarr_eventtype:-}}"
if [[ "$event" == "Test" ]]; then
  exit 0
fi

dir="$(dirname "${BASH_SOURCE[0]}")"
"$dir/import-sidecar-subs.sh"
# Subtitle sidecar normalization runs on the host in lg-tv-optimize-media.sh (*arr images have no Python).
"$dir/lg-tv-queue-path.sh"
if [[ -n "${sonarr_episodefile_path:-}" ]]; then
  /mnt/jellyfin/scripts/jellyfin/jellyfin-queue-episode-image-refresh.sh "${sonarr_episodefile_path}" 2>/dev/null || true
elif [[ -n "${sonarr_episodefile_sourcepath:-}" ]]; then
  /mnt/jellyfin/scripts/jellyfin/jellyfin-queue-episode-image-refresh.sh "${sonarr_episodefile_sourcepath}" 2>/dev/null || true
fi
