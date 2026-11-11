#!/usr/bin/env bash
# Jellyfin: Netflix-style next episode for series (TV clients), no playback/latency changes.
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"
exec /usr/bin/python3 "${APPLY_DIR}/apply-tv-series-playback.py"
