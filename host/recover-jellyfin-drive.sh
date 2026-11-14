#!/usr/bin/env bash
# Manual / guard: ensure SSD mount, restart stack, refresh *arr + qBit.
# RECOVER_SKIP_JELLYFIN=1 — skip jellyfin container + DB guard
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=jellyfin-drive-lib.sh
source "${SCRIPT_DIR}/jellyfin-drive-lib.sh"

log() { echo "$(date -Iseconds) [recover-jellyfin-drive] $*"; }

if ! jellyfin_prepare_volume; then
  log "ERROR: volume not ready (run: sudo ${HOST_DIR}/install-jellyfin-mount-sudo.sh)"
  exit 1
fi

skip_jf=$([[ "${RECOVER_SKIP_JELLYFIN:-0}" == "1" ]] && echo 1 || echo 0)
log "restarting stack under ${JELLYFIN_MNT}"
jellyfin_restart_stack "$skip_jf"

log "resuming errored qBit jobs (if any)"
python3 <<PY
import sys, urllib.parse
sys.path.insert(0, "${SCRIPTS_ROOT}/lib")
from arr_common import qbit_json
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

if [[ "$skip_jf" == "0" && -x "${JF_SCRIPTS}/jellyfin-delete-guard.py" ]]; then
  python3 "${JF_SCRIPTS}/jellyfin-delete-guard.py" --sanitize-all --purge-ghosts --ghost-limit 50 || true
fi

log "done"