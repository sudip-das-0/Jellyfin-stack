#!/usr/bin/env bash
# Dedupe queue/torrents first, then stalled-torrent recovery (single torrent per item only).
set -euo pipefail
/usr/bin/python3 /home/devdas/arr/scripts/prune-stall-logs.py
/usr/bin/python3 /home/devdas/arr/scripts/queue-dedupe.py
/usr/bin/python3 /home/devdas/arr/scripts/torrent-stall-recovery.py
/usr/bin/python3 /home/devdas/arr/scripts/cleanup-qbit-missing.py
/usr/bin/python3 /home/devdas/arr/scripts/cleanup-qbit-completed.py
/usr/bin/python3 /home/devdas/arr/scripts/cleanup-partial-series.py
# LG TV queue: continuous lg-tv-optimize.service (no gap between jobs).
if ! /usr/bin/python3 /home/devdas/arr/scripts/series-season-completeness.py; then
  /usr/bin/python3 /home/devdas/arr/scripts/sonarr-search-incomplete-seasons.py || true
  echo "[maintenance] WARN: incomplete monitored TV season(s) — see /mnt/jellyfin/logs/stall-recovery/series-completeness.log" >&2
fi
exit 0
