#!/usr/bin/env bash
# Sonarr/Radarr: unmonitor titles when import completes (no re-grab / upgrade churn).
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/stack-paths.sh"
chmod +x "${HOOKS_DIR}/arr-unmonitor-on-import.sh"

SONARR_KEY=$(grep -oP '(?<=<ApiKey>)[^<]+' /home/devdas/arr/config/sonarr/config.xml)
RADARR_KEY=$(grep -oP '(?<=<ApiKey>)[^<]+' /home/devdas/arr/config/radarr/config.xml)
SCRIPT=/data/scripts/arr-unmonitor-on-import.sh

upsert() {
  local base=$1 key=$2 name=$3 template_name=$4 on_import=$5
  python3 <<PY
import json, urllib.request
key = "$key"
base = "$base"
name = "$name"
script = "$SCRIPT"
template_name = "$template_name"
on_import = "$on_import" == "1"
req = urllib.request.Request(f"{base}/api/v3/notification", headers={"X-Api-Key": key})
items = json.load(urllib.request.urlopen(req))
template = next((n for n in items if n.get("name") == template_name), None)
if not template:
    raise SystemExit(f"Missing template connection {template_name!r}")
existing = next((n for n in items if n.get("name") == name), None)
body = {k: v for k, v in template.items() if k != "id"}
body.update({
    "name": name,
    "onGrab": False,
    "onDownload": False,
    "onUpgrade": False,
    "onImportComplete": on_import,
    "onRename": False,
    "onSeriesAdd": False,
    "onSeriesDelete": False,
    "onEpisodeFileDelete": False,
    "onHealthIssue": False,
    "onApplicationUpdate": False,
    "onMovieDelete": False,
    "onMovieFileDelete": False,
    "fields": [
        {"name": "path", "value": script, "order": 0, "type": "filePath"},
        {"name": "arguments", "value": "", "order": 1, "type": "textbox"},
    ],
})
if existing:
    url = f"{base}/api/v3/notification/{existing['id']}"
    http_method = "PUT"
else:
    url = f"{base}/api/v3/notification"
    http_method = "POST"
urllib.request.urlopen(
    urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method=http_method,
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
    )
)
print(f"{'Updated' if existing else 'Created'} {name}")
PY
}

upsert "http://127.0.0.1:8989" "$SONARR_KEY" "Arr unmonitor on import (Sonarr)" \
  "Arr import postprocess (Sonarr)" 1
upsert "http://127.0.0.1:7878" "$RADARR_KEY" "Arr unmonitor on import (Radarr)" \
  "Arr import postprocess (Radarr)" 1
