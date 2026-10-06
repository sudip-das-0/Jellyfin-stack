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
