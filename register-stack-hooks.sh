#!/usr/bin/env bash
# Register all Sonarr/Radarr connections (import postprocess + Bazarr webhooks + OS API key).
set -euo pipefail
DIR=$(dirname "$(readlink -f "$0")")
APPLY="$DIR/apply"
LGTV="$DIR/lg-tv"

/usr/bin/python3 "$APPLY/apply-no-seed-download-policy.py"
"$APPLY/register-arr-import-hook.sh"
"$APPLY/register-arr-delete-sync.sh"
"$APPLY/register-arr-unmonitor-import.sh"
"$APPLY/apply-jellyfin-ui-delete-sync.sh" || true
"$APPLY/register-bazarr-webhooks.sh"
"$APPLY/bazarr-apply-opensubtitles-apikey.sh" || true
"$APPLY/apply-jellyfin-opensubtitles.sh" || true
"$APPLY/apply-media-language-policy.sh" --settings-only
"$APPLY/apply-tv-series-playback.sh"
"$LGTV/lg-tv-normalize-queue.sh" 2>/dev/null || true
"$DIR/stack-verify.sh" || true
