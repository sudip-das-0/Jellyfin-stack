#!/usr/bin/env bash
# Purpose: Subtitle/audio language policy (Bazarr + Jellyfin + *arr formats).
# Runs: register-stack-hooks.sh
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

/usr/bin/python3 "${APPLY_DIR}/apply-tv-direct-play-formats.py"
/usr/bin/python3 "${APPLY_DIR}/apply-english-audio-preference.py"

sqlite3 "$JELLYFIN_DB" <<'SQL'
UPDATE Users
SET AudioLanguagePreference = 'eng',
    SubtitleLanguagePreference = 'eng',
    SubtitleMode = 1,
    PlayDefaultAudioTrack = 1,
    RememberSubtitleSelections = 1,
    RememberAudioSelections = 1,
    EnableUserPreferenceAccess = 1;
SQL

BAZ_KEY=$(read_bazarr_api_key)
curl -sS -X POST -H "X-Api-Key: $BAZ_KEY" "${BAZARR_URL}/api/system/tasks?taskid=update_series" -o /dev/null
curl -sS -X POST -H "X-Api-Key: $BAZ_KEY" "${BAZARR_URL}/api/system/tasks?taskid=update_movies" -o /dev/null
docker restart bazarr >/dev/null 2>&1 || true
sleep 10
"${APPLY_DIR}/bazarr-apply-opensubtitles-apikey.sh" || true
docker restart jellyfin >/dev/null 2>&1 || true
echo "Media language policy applied."
