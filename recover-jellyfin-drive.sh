#!/usr/bin/env bash
# Remount the jellyfin SSD (if unplugged/re-enumerated) and restart containers that bind /mnt/jellyfin.
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/stack-paths.sh"

log() { echo "$(date -Iseconds) [recover-jellyfin-drive] $*"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if ! mountpoint -q "$JELLYFIN_MNT"; then
  log "mounting UUID=$JELLYFIN_UUID at $JELLYFIN_MNT"
  sudo mount "UUID=$JELLYFIN_UUID" "$JELLYFIN_MNT"
elif ! test -r "$TORRENTS_ROOT" || ! docker exec qbittorrent test -r /data/torrents 2>/dev/null; then
  log "remounting $JELLYFIN_MNT (stale or unreadable bind target)"
  sudo umount -l "$JELLYFIN_MNT" 2>/dev/null || true
  sudo mount "UUID=$JELLYFIN_UUID" "$JELLYFIN_MNT"
fi

if ! mountpoint -q "$JELLYFIN_MNT"; then
  log "ERROR: $JELLYFIN_MNT is not a mount point"
  exit 1
fi

"$SCRIPT_DIR/ensure-jellyfin-media-mount.sh" || exit 1

restart_targets=(qbittorrent sonarr radarr prowlarr bazarr jellyseerr)
if [[ "${RECOVER_SKIP_JELLYFIN:-0}" != "1" ]]; then
  restart_targets+=(jellyfin)
fi
log "restarting Docker stack (bind mounts under $JELLYFIN_MNT): ${restart_targets[*]}"
docker restart "${restart_targets[@]}"

log "resuming errored qBit jobs (if any)"
python3 <<PY
import sys
from pathlib import Path
sys.path.insert(0, "${SCRIPTS_ROOT}")
from arr_common import qbit_json
import urllib.parse

info = qbit_json("/api/v2/torrents/info")
if not isinstance(info, list):
    raise SystemExit("qBit API unexpected response")
bad = [t["hash"] for t in info if t.get("state") in ("error", "missingFiles")]
if not bad:
    print("no error/missingFiles torrents")
else:
    body = urllib.parse.urlencode({"hashes": "|".join(bad)}).encode()
    for path in ("/api/v2/torrents/recheck", "/api/v2/torrents/resume"):
        try:
            qbit_json(path, method="POST", data=body)
        except Exception as e:
            print(path, e)
    print(f"recheck/resume {len(bad)} torrent(s)")
PY

SONARR_KEY=$(read_arr_api_key "$SONARR_CONFIG")
RADARR_KEY=$(read_arr_api_key "$RADARR_CONFIG")
for app in sonarr radarr; do
  key=$([ "$app" = sonarr ] && echo "$SONARR_KEY" || echo "$RADARR_KEY")
  port=$([ "$app" = sonarr ] && echo 8989 || echo 7878)
  curl -sf -X POST -H "X-Api-Key: $key" -H "Content-Type: application/json" \
    -d '{"name":"RefreshMonitoredDownloads"}' "http://127.0.0.1:${port}/api/v3/command" >/dev/null || true
done

if [[ "${RECOVER_SKIP_JELLYFIN:-0}" != "1" ]] && [[ -x "${SCRIPTS_ROOT}/jellyfin-delete-guard.py" ]]; then
  python3 "${SCRIPTS_ROOT}/jellyfin-delete-guard.py" --sanitize-all --purge-ghosts --ghost-limit 50 || true
fi

log "done — run download-maintenance.sh if queue rows still show missing files"
