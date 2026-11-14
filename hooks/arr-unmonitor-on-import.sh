#!/usr/bin/env bash
# Sonarr/Radarr: after import, stop monitoring so completed items are not re-grabbed.
set -euo pipefail
# shellcheck source=hook-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/hook-lib.sh"

event="${sonarr_eventtype:-${radarr_eventtype:-}}"
[[ "$event" == "Test" ]] && exit 0

case "$event" in
  DownloadImported|ImportComplete|MovieImport|ManualImportComplete) ;;
  *) exit 0 ;;
esac

STALL_DIR="$(hook_log_root)/stall-recovery"

# The plural var may carry a comma list; the singular one the same id: queue each distinct value.
for id in "${sonarr_episodefile_episodeids:-}" "${sonarr_episodefile_episodeid:-}"; do
  [[ -n "$id" ]] && { queue_append_unique "${STALL_DIR}/unmonitor-sonarr.queue" "$id" || true; }
done

if [[ -n "${radarr_movie_id:-}" ]]; then
  queue_append_unique "${STALL_DIR}/unmonitor-radarr.queue" "${radarr_movie_id}" || true
fi
