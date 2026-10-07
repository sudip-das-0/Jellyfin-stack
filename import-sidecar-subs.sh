#!/usr/bin/env bash
# Wrapper — canonical hook lives in hooks/
exec "$(dirname "$(readlink -f "$0")")/hooks/import-sidecar-subs.sh"
