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
python3 - <<PY || true
import sys
sys.path.insert(0, "${SCRIPTS_ROOT}/lib")
from arr_common import qbit_recheck_resume_errored
n = qbit_recheck_resume_errored()
print(f"recheck/resume {n} torrent(s)" if n else "no error/missingFiles torrents")
PY

for app in sonarr radarr; do
  config_var=${app^^}_CONFIG
  url_var=${app^^}_URL
  key=$(read_arr_api_key "${!config_var}")
  curl -sf -X POST -H "X-Api-Key: $key" -H "Content-Type: application/json" \
    -d '{"name":"RefreshMonitoredDownloads"}' "${!url_var}/api/v3/command" >/dev/null || true
done

if [[ "$skip_jf" == "0" && -x "${JF_SCRIPTS}/jellyfin-delete-guard.py" ]]; then
  python3 "${JF_SCRIPTS}/jellyfin-delete-guard.py" --sanitize-all --purge-ghosts --ghost-limit 50 || true
fi

log "done"
