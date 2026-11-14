#!/usr/bin/env bash
# Sonarr/Radarr: local subs from release folder, then queue LG TV optimize (single hook).
set -euo pipefail
# shellcheck source=hook-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/hook-lib.sh"

event="${sonarr_eventtype:-${radarr_eventtype:-}}"
[[ "$event" == "Test" ]] && exit 0

dir="$(dirname "${BASH_SOURCE[0]}")"
"$dir/import-sidecar-subs.sh"
# Subtitle sidecar normalization runs on the host in lg-tv-optimize-media.sh (*arr images have no Python).
"$dir/lg-tv-queue-path.sh"

# Episode image refresh: this hook runs inside the *arr container, so only enqueue the path;
# download-maintenance.sh drains the queue on the host (jellyfin-process-episode-image-refresh.sh).
ep_path="${sonarr_episodefile_path:-${sonarr_episodefile_sourcepath:-}}"
if [[ -n "$ep_path" ]] && hook_is_video_path "$ep_path"; then
  queue_append_unique "$(hook_log_root)/stall-recovery/jellyfin-episode-image-refresh.queue" "$ep_path" || true
fi
