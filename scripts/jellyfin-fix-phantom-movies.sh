#!/usr/bin/env bash
exec "$(dirname "$(readlink -f "$0")")/jellyfin-db-trim.sh"
