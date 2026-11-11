#!/usr/bin/env bash
# Purpose: Sonarr/Radarr → Bazarr webhooks on import/upgrade.
# Runs: register-stack-hooks.sh
set -euo pipefail
exec /usr/bin/python3 "$(dirname "$(readlink -f "$0")")/register_bazarr_webhooks.py" "$@"
