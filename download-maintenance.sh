#!/usr/bin/env bash
# Dedupe queue/torrents first, then stalled-torrent recovery (single torrent per item only).
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/stack-paths.sh"
/usr/bin/python3 "${SCRIPTS_ROOT}/prune-stall-logs.py"
/usr/bin/python3 "${SCRIPTS_ROOT}/queue-dedupe.py"
/usr/bin/python3 "${SCRIPTS_ROOT}/torrent-stall-recovery.py"
/usr/bin/python3 "${SCRIPTS_ROOT}/cleanup-qbit-missing.py"
/usr/bin/python3 "${SCRIPTS_ROOT}/cleanup-qbit-completed.py"
/usr/bin/python3 "${SCRIPTS_ROOT}/cleanup-partial-series.py"
# LG TV queue: continuous lg-tv-optimize.service (no gap between jobs).
if ! /usr/bin/python3 "${SCRIPTS_ROOT}/series-season-completeness.py"; then
  /usr/bin/python3 "${SCRIPTS_ROOT}/sonarr-search-incomplete-seasons.py" || true
  echo "[maintenance] WARN: incomplete monitored TV season(s) — see /mnt/jellyfin/logs/stall-recovery/series-completeness.log" >&2
fi
exit 0
