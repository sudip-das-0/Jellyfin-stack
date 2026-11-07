#!/usr/bin/env bash
# Purpose: Legacy name — stop downloaders if jellyfin SSD is missing.
# Depends: jellyfin-drive-guard.sh
exec "$(dirname "$(readlink -f "$0")")/jellyfin-drive-guard.sh"
