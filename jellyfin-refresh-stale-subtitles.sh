#!/usr/bin/env bash
# Jellyfin sometimes keeps ghost external subs (*.srt) after sidecars were renamed to *.eng.srt.
# Refreshes Movies + Series + Anime libraries so MediaStreams match files on disk.
set -euo pipefail

JELLYFIN_DB=/mnt/jellyfin/jellyfin/config/data/jellyfin.db
KEY=$(sqlite3 "$JELLYFIN_DB" "SELECT AccessToken FROM ApiKeys LIMIT 1;")
[[ -n "$KEY" ]] || { echo "No Jellyfin API key" >&2; exit 1; }

auth=(-H "Authorization: MediaBrowser Token=\"$KEY\"")
before=$(sqlite3 "$JELLYFIN_DB" 'SELECT COUNT(*) FROM MediaStreamInfos WHERE IsExternal=1 AND Path GLOB "*.srt" AND Path NOT GLOB "*.eng.srt" AND Path NOT GLOB "*.en.srt";' 2>/dev/null || echo 0)

mapfile -t libs < <(curl -sS "${auth[@]}" http://127.0.0.1:8096/Library/VirtualFolders |
  python3 -c "import json,sys; print('\n'.join(l['ItemId'] for l in json.load(sys.stdin) if l.get('Name') in ('Movies','Series','Anime')))")

for id in "${libs[@]}"; do
  curl -sS -X POST "${auth[@]}" \
    "http://127.0.0.1:8096/Items/${id}/Refresh?Recursive=true&MetadataRefreshMode=Default&ImageRefreshMode=Default" \
    -o /dev/null -w "Refresh ${id}: %{http_code}\n"
done

echo "Stale non-.eng.srt subtitle streams before refresh: ${before}"
echo "Library refresh queued; ghost subs usually clear within a few minutes."
