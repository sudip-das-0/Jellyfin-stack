#!/usr/bin/env bash
# Completed imports stay unmonitored (Sonarr/Radarr built-in + import hooks).
set -euo pipefail
DIR=$(dirname "$(readlink -f "$0")")

"$DIR/register-arr-unmonitor-import.sh"
/usr/bin/python3 "$DIR/apply-no-seed-download-policy.py" 2>/dev/null || true

python3 <<'PY'
import sys
sys.path.insert(0, "/mnt/jellyfin/scripts/lib")
from arr_common import (
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    arr_json,
    read_api_key,
)

def patch_mm(base: str, key: str, label: str, **flags) -> None:
    cfg = arr_json(f"{base}/api/v3/config/mediamanagement", key)
    if not isinstance(cfg, dict):
        return
    for k, v in flags.items():
        cfg[k] = v
    arr_json(
        f"{base}/api/v3/config/mediamanagement/{cfg['id']}",
        key,
        method="PUT",
        body=cfg,
    )
    print(f"{label}: " + ", ".join(f"{k}={v!r}" for k, v in flags.items()))

son = read_api_key(SONARR_CONFIG)
rad = read_api_key(RADARR_CONFIG)
patch_mm(
    SONARR_URL,
    son,
    "sonarr",
    autoUnmonitorPreviouslyDownloadedEpisodes=True,
)
patch_mm(
    RADARR_URL,
    rad,
    "radarr",
    autoUnmonitorPreviouslyDownloadedMovies=True,
)
PY

if [[ "${UNMONITOR_BACKFILL:-0}" == "1" ]]; then
  /usr/bin/python3 "$DIR/../maintenance/unmonitor-on-import.py" --all-with-files
else
  echo "Optional backfill (Sonarr + Radarr): UNMONITOR_BACKFILL=1 $0"
fi

echo "Done. New imports unmonitor automatically; maintenance drains hook queues every 5m."
