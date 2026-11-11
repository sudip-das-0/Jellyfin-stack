#!/usr/bin/env bash
# Register live Sonarr/Radarr/Jellyfin connections after a stack reset.
set -euo pipefail
DIR=$(dirname "$(readlink -f "$0")")
# shellcheck source=lib/stack-paths.sh
source "$DIR/lib/stack-paths.sh"
APPLY="$DIR/apply"
LGTV="$DIR/lg-tv"

chmod +x \
  "${HOOKS_DIR}/arr-import-postprocess.sh" \
  "${HOOKS_DIR}/import-sidecar-subs.sh" \
  "${HOOKS_DIR}/lg-tv-queue-path.sh" \
  "${HOOKS_DIR}/arr-on-media-deleted.sh" \
  "${HOOKS_DIR}/arr-unmonitor-on-import.sh"

/usr/bin/python3 "$APPLY/apply-no-seed-download-policy.py"
/usr/bin/python3 "${LIB_DIR}/register_arr_notifications.py" import-hook
/usr/bin/python3 "${LIB_DIR}/register_arr_notifications.py" delete-sync
/usr/bin/python3 "${LIB_DIR}/register_arr_notifications.py" unmonitor
"$APPLY/apply-jellyfin-ui-delete-sync.sh" || true
/usr/bin/python3 "${LIB_DIR}/register_bazarr_webhooks.py"
"$APPLY/bazarr-apply-opensubtitles-apikey.sh" || true
"$APPLY/apply-jellyfin-opensubtitles.sh" || true
"$APPLY/apply-media-language-policy.sh" --settings-only
"$APPLY/apply-tv-series-playback.sh"
/usr/bin/python3 "$APPLY/apply-lapse-plugin.py" || true
if [[ -f "$DIR/temp/apply-blocked-movies.py" ]]; then
  /usr/bin/python3 "$DIR/temp/apply-blocked-movies.py" || true
fi
"$LGTV/lg-tv-normalize-queue.sh" 2>/dev/null || true
"$DIR/stack-verify.sh" || true
