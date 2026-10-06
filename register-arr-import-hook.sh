#!/usr/bin/env bash
# One custom script per *arr (subs + LG queue). Removes legacy duplicate connections.
set -euo pipefail

# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/stack-paths.sh
chmod +x "${HOOKS_DIR}/arr-import-postprocess.sh"
chmod +x "${HOOKS_DIR}/import-sidecar-subs.sh"
chmod +x "${HOOKS_DIR}/lg-tv-queue-path.sh"

SONARR_KEY=$(grep -oP '(?<=<ApiKey>)[^<]+' /home/devdas/arr/config/sonarr/config.xml)
RADARR_KEY=$(grep -oP '(?<=<ApiKey>)[^<]+' /home/devdas/arr/config/radarr/config.xml)
SCRIPT=/data/scripts/arr-import-postprocess.sh

manage() {
  local base=$1 key=$2 svc_name=$3 kind=$4
  python3 <<PY
import json, urllib.request
key = "$key"
base = "$base"
script = "$SCRIPT"
svc_name = "$svc_name"
kind = "$kind"
remove = (
    ["LG TV optimize queue (Sonarr)", "Import sidecar subs (Sonarr)"]
    if kind == "sonarr"
    else ["LG TV optimize queue (Radarr)", "Import sidecar subs (Radarr)"]
)

req = urllib.request.Request(f"{base}/api/v3/notification", headers={"X-Api-Key": key})
items = json.load(urllib.request.urlopen(req))
for n in list(items):
    if n.get("name") in remove:
        urllib.request.urlopen(
            urllib.request.Request(
                f"{base}/api/v3/notification/{n['id']}",
                method="DELETE",
                headers={"X-Api-Key": key},
            )
        )
        print(f"Removed legacy connection: {n['name']}")

items = json.load(urllib.request.urlopen(req))
existing = next((n for n in items if n.get("name") == svc_name), None)
body = {
    "name": svc_name,
    "implementation": "CustomScript",
    "configContract": "CustomScriptSettings",
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
    "fields": [{"name": "path", "value": script}, {"name": "arguments", "value": ""}],
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
    print(f"Updated {svc_name} id={nid}")
else:
    urllib.request.urlopen(
        urllib.request.Request(
            f"{base}/api/v3/notification",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"X-Api-Key": key, "Content-Type": "application/json"},
        )
    )
    print(f"Created {svc_name}")
PY
}

manage "http://127.0.0.1:8989" "$SONARR_KEY" "Arr import postprocess (Sonarr)" sonarr
manage "http://127.0.0.1:7878" "$RADARR_KEY" "Arr import postprocess (Radarr)" radarr
