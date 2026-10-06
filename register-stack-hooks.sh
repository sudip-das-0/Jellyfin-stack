#!/usr/bin/env bash
# Register all Sonarr/Radarr connections (import postprocess + Bazarr webhooks + OS API key).
# See /mnt/jellyfin/scripts/README.md
set -euo pipefail
DIR=$(dirname "$(readlink -f "$0")")

"$DIR/apply-no-seed-download-policy.sh"
"$DIR/register-arr-import-hook.sh"
"$DIR/register-bazarr-webhooks.sh"
"$DIR/bazarr-apply-opensubtitles-apikey.sh" || true
"$DIR/apply-media-language-policy.sh" --settings-only
"$DIR/apply-tv-series-playback.sh"
"$DIR/lg-tv-normalize-queue.sh" 2>/dev/null || true
"$DIR/stack-verify.sh" || true
