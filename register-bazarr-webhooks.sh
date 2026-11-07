#!/usr/bin/env bash
# Purpose: Sonarr/Radarr → Bazarr webhooks on import/upgrade.
# Runs: register-stack-hooks.sh
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/stack-paths.sh"

SONARR_KEY=$(read_arr_api_key "$SONARR_CONFIG")
RADARR_KEY=$(read_arr_api_key "$RADARR_CONFIG")
BAZARR_KEY=$(read_bazarr_api_key)

upsert_webhook() {
  local base=$1 key=$2 name=$3 url=$4 bazarr_key=$5
  python3 <<PY
import json, urllib.request
key = "$key"
base = "$base"
name = "$name"
url = "$url"
bazarr_key = "$bazarr_key"
req = urllib.request.Request(f"{base}/api/v3/notification", headers={"X-Api-Key": key})
items = json.load(urllib.request.urlopen(req))
existing = next((n for n in items if n.get("name") == name), None)
body = {
    "name": name,
    "implementation": "Webhook",
    "configContract": "WebhookSettings",
    "onGrab": False,
    "onDownload": True,
    "onUpgrade": True,
    "onImportComplete": False,
    "onRename": False,
    "onSeriesAdd": False,
    "onSeriesDelete": False,
    "onEpisodeFileDelete": False,
    "onHealthIssue": False,
    "onApplicationUpdate": False,
    "onMovieDelete": False,
    "onMovieFileDelete": False,
    "includeHealthWarnings": False,
    "tags": [],
    "fields": [
        {"name": "url", "value": url},
        {"name": "method", "value": 1},
        {"name": "username", "value": ""},
        {"name": "password", "value": ""},
        {
            "name": "headers",
            "value": [{"key": "X-Api-Key", "value": bazarr_key}],
        },
    ],
}
if existing:
    body = {**existing, **body}
    nid = existing["id"]
    urllib.request.urlopen(
        urllib.request.Request(
            f"{base}/api/v3/notification/{nid}",
            data=json.dumps(body).encode(),
            method="PUT",
            headers={"X-Api-Key": key, "Content-Type": "application/json"},
        )
    )
    print(f"Updated {name} id={nid}")
else:
    urllib.request.urlopen(
        urllib.request.Request(
            f"{base}/api/v3/notification",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"X-Api-Key": key, "Content-Type": "application/json"},
        )
    )
    print(f"Created {name}")
PY
}

upsert_webhook "http://127.0.0.1:8989" "$SONARR_KEY" "Bazarr subtitles (Sonarr)" \
  "http://bazarr:6767/api/webhooks/sonarr" "$BAZARR_KEY"
upsert_webhook "http://127.0.0.1:7878" "$RADARR_KEY" "Bazarr subtitles (Radarr)" \
  "http://bazarr:6767/api/webhooks/radarr" "$BAZARR_KEY"
