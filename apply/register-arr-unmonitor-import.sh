#!/usr/bin/env bash
# Purpose: Unmonitor Sonarr/Radarr titles after import completes.
# Runs: manual | register-stack-hooks.sh
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"
chmod +x "${HOOKS_DIR}/arr-unmonitor-on-import.sh"
exec /usr/bin/python3 "${LIB_DIR}/register_arr_notifications.py" unmonitor
