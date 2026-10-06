#!/usr/bin/env bash
# LinuxServer Bazarr hardcodes a default OpenSubtitles API key in get_providers.py;
# inject the key from config.yaml after the container is running.
set -euo pipefail

CFG=/home/devdas/arr/config/bazarr/config/config.yaml
KEY=$(grep -A8 '^opensubtitlescom:' "$CFG" | grep 'api_key:' | awk '{print $2}')
[[ -n "$KEY" ]] || { echo "No opensubtitlescom.api_key in $CFG" >&2; exit 1; }

TARGET=/app/bazarr/bin/bazarr/app/get_providers.py
for _ in $(seq 1 30); do
  docker exec bazarr test -f "$TARGET" 2>/dev/null && break
  sleep 2
done

docker exec bazarr sed -i "s/'api_key': '[^']*'/'api_key': '${KEY}'/" "$TARGET"
echo "OpenSubtitles API key applied in Bazarr container."
