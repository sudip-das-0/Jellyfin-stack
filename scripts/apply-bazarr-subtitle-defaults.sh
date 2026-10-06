#!/usr/bin/env bash
# English subtitle profile for all Sonarr/Radarr items + backfill search for existing files.
# Online providers: free only (NapiProjekt, subf2m, Animetosho) — no OpenSubtitles VIP/paid APIs.
set -euo pipefail

BAZ_KEY=$(grep -A2 '^auth:' /home/devdas/arr/config/bazarr/config/config.yaml | grep apikey | awk '{print $2}')
SON_KEY=$(grep -oP '(?<=<ApiKey>)[^<]+' /home/devdas/arr/config/sonarr/config.xml)
RAD_KEY=$(grep -oP '(?<=<ApiKey>)[^<]+' /home/devdas/arr/config/radarr/config.xml)
BASE=http://127.0.0.1:6767/api
PROFILE=1

bazarr() {
  curl -sS -H "X-Api-Key: $BAZ_KEY" "$@"
}

echo "Restarting Bazarr to apply default profile settings..."
docker restart bazarr >/dev/null
sleep 8
until bazarr "$BASE/system/status" >/dev/null 2>&1; do sleep 2; done

echo "Syncing Sonarr/Radarr libraries in Bazarr..."
bazarr -X POST "$BASE/system/tasks?taskid=update_series" -o /dev/null -w "Sonarr sync: %{http_code}\n"
bazarr -X POST "$BASE/system/tasks?taskid=update_movies" -o /dev/null -w "Radarr sync: %{http_code}\n"
sleep 5

echo "Assigning English language profile to all series..."
mapfile -t SERIES_IDS < <(curl -sS -H "X-Api-Key: $SON_KEY" http://127.0.0.1:8989/api/v3/series | python3 -c "
import json,sys
for s in json.load(sys.stdin):
    print(s['id'])
")
for sid in "${SERIES_IDS[@]}"; do
  bazarr -X POST "${BASE}/series?seriesid=${sid}&profileid=${PROFILE}" -o /dev/null 2>/dev/null || true
done
echo "Assigned English profile to ${#SERIES_IDS[@]} series."

echo "Assigning English language profile to all movies..."
mapfile -t MOVIE_IDS < <(curl -sS -H "X-Api-Key: $RAD_KEY" http://127.0.0.1:7878/api/v3/movie | python3 -c "
import json,sys
for m in json.load(sys.stdin):
    print(m['id'])
")
for mid in "${MOVIE_IDS[@]}"; do
  bazarr -X POST "${BASE}/movies?radarrid=${mid}&profileid=${PROFILE}" -o /dev/null 2>/dev/null || true
done
echo "Assigned English profile to ${#MOVIE_IDS[@]} movies."

echo "Indexing existing subtitles on disk..."
bazarr -X POST "$BASE/system/tasks?taskid=series_full_scan_subtitles" -o /dev/null -w "Series index: %{http_code}\n"
bazarr -X POST "$BASE/system/tasks?taskid=movies_full_scan_subtitles" -o /dev/null -w "Movies index: %{http_code}\n"
sleep 3

echo "Searching for missing subtitles (existing library)..."
bazarr -X POST "$BASE/system/tasks?taskid=wanted_search_missing_subtitles_series" -o /dev/null -w "Series wanted search: %{http_code}\n"
bazarr -X POST "$BASE/system/tasks?taskid=wanted_search_missing_subtitles_movies" -o /dev/null -w "Movies wanted search: %{http_code}\n"

for sid in "${SERIES_IDS[@]}"; do
  bazarr -X PATCH "${BASE}/series?seriesid=${sid}&action=search-wanted" -o /dev/null 2>/dev/null || true
done

echo "Done. New Sonarr/Radarr imports will trigger Bazarr via webhook on download/upgrade."
