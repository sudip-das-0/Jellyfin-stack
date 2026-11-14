#!/usr/bin/env bash
# Sonarr/Radarr: library file deleted — queue path for host sync (no Python in *arr images).
set -euo pipefail
# shellcheck source=hook-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/hook-lib.sh"

event="${sonarr_eventtype:-${radarr_eventtype:-}}"
[[ "$event" == "Test" ]] && exit 0

raw="${sonarr_episodefile_path:-${radarr_moviefile_path:-}}"
[[ -n "$raw" ]] || exit 0

queue_append_unique "$(hook_log_root)/stall-recovery/delete-sync.queue" "$raw" || true
