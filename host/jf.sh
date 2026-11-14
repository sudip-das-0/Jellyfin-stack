#!/usr/bin/env bash
# Jellyfin + *arr stack shortcut (default: docker compose up -d).
set -euo pipefail
cli="$(dirname "$(readlink -f "$0")")/jellyfin-cli.sh"
[[ $# -eq 0 ]] && set -- start
exec "$cli" "$@"
