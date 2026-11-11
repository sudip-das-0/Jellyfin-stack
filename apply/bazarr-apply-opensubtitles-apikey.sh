#!/usr/bin/env bash
# Purpose: Inject OpenSubtitles API key into Bazarr container after start.
# Runs: register-stack-hooks.sh
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

CFG="$BAZARR_CONFIG"
KEY=$(grep -A8 '^opensubtitlescom:' "$CFG" | grep 'api_key:' | awk '{print $2}')
[[ -n "$KEY" ]] || { echo "No opensubtitlescom.api_key in $CFG" >&2; exit 1; }

TARGET=/app/bazarr/bin/bazarr/app/get_providers.py
for _ in $(seq 1 30); do
  docker exec bazarr test -f "$TARGET" 2>/dev/null && break
  sleep 2
done

docker exec bazarr sed -i "s/'api_key': '[^']*'/'api_key': '${KEY}'/" "$TARGET"
echo "OpenSubtitles API key applied in Bazarr container."
