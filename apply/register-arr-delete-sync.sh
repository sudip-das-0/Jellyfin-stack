#!/usr/bin/env bash
# Purpose: Sonarr/Radarr file-delete → sync-manual-deletes hook.
# Runs: manual | register-stack-hooks.sh
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"
chmod +x "${HOOKS_DIR}/arr-on-media-deleted.sh"
exec /usr/bin/python3 "${LIB_DIR}/register_arr_notifications.py" delete-sync
