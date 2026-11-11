#!/usr/bin/env bash
# Purpose: Rebuild Bazarr *arr mirror after FK/sync errors.
# Runs: manual
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

DIR=$(dirname "$(readlink -f "$0")")
BAZ_DB="$BAZARR_DB"
BAZ_KEY=$(read_bazarr_api_key)
BASE=http://127.0.0.1:6767/api

echo "Stopping Bazarr..."
docker stop bazarr >/dev/null

backup="${BAZ_DB}.bak.$(date +%Y%m%d-%H%M%S)"
cp -a "$BAZ_DB" "$backup"
echo "Backup: $backup"

echo "Purging series/movies cache tables..."
sqlite3 "$BAZ_DB" <<'SQL'
PRAGMA foreign_keys=OFF;
DELETE FROM table_episodes_subtitles;
DELETE FROM table_history;
DELETE FROM table_episodes;
DELETE FROM table_shows;
DELETE FROM table_movies_subtitles;
DELETE FROM table_history_movie;
DELETE FROM table_movies;
DELETE FROM table_blacklist;
DELETE FROM table_blacklist_movie;
PRAGMA foreign_keys=ON;
SQL

echo "Starting Bazarr..."
docker start bazarr >/dev/null
for _ in $(seq 1 45); do
  curl -sf -H "X-Api-Key: $BAZ_KEY" "$BASE/system/status" >/dev/null 2>&1 && break
  sleep 2
done

"$DIR/bazarr-apply-opensubtitles-apikey.sh" || true

baz() { curl -sS -H "X-Api-Key: $BAZ_KEY" "$@"; }

echo "Syncing from Sonarr and Radarr..."
baz -X POST "$BASE/system/tasks?taskid=update_series" -o /dev/null -w "update_series: %{http_code}\n"
baz -X POST "$BASE/system/tasks?taskid=update_movies" -o /dev/null -w "update_movies: %{http_code}\n"
sleep 15

"$DIR/apply-bazarr-subtitle-defaults.sh"

echo "Bazarr resync complete."
