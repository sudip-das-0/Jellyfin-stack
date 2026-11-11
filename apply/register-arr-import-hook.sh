#!/usr/bin/env bash
# Purpose: Sonarr/Radarr import postprocess hook (subs + LG queue).
# Runs: manual | register-stack-hooks.sh
# Depends: stack-paths.sh, register_arr_notifications.py, hooks/
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"
chmod +x "${HOOKS_DIR}/arr-import-postprocess.sh" "${HOOKS_DIR}/import-sidecar-subs.sh" "${HOOKS_DIR}/lg-tv-queue-path.sh"
exec /usr/bin/python3 "${LIB_DIR}/register_arr_notifications.py" import-hook
