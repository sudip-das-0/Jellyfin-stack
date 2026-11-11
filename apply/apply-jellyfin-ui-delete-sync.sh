#!/usr/bin/env bash
# Purpose: Jellyfin UI delete → *arr unmonitor (Webhook plugin + delete guard listener).
# Runs: manual | register-stack-hooks.sh
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

DIR="$SCRIPTS_ROOT"
PLUGIN_GUID=71552a5a5c5c4350a2aeebe451a30173
PLUGIN_VERSION=22.0.0.0
WEBHOOK_URL="http://127.0.0.1:9407/"
SERVICE_SRC="$DIR/systemd/user/jellyfin-delete-webhook.service"

chmod +x "$DIR/jellyfin/jellyfin-delete-webhook.py"

API_KEY=$(sqlite3 "$JELLYFIN_DB" "SELECT AccessToken FROM ApiKeys LIMIT 1;")
[[ -n "$API_KEY" ]] || {
  echo "No Jellyfin API key in $JELLYFIN_DB" >&2
  exit 1
}
auth=(-H "Authorization: MediaBrowser Token=\"$API_KEY\"")

wait_jellyfin() {
  for _ in $(seq 1 40); do
    curl -sf "${JELLYFIN_URL}/System/Info/Public" >/dev/null 2>&1 && return 0
    sleep 2
  done
  echo "Jellyfin not responding at ${JELLYFIN_URL}" >&2
  return 1
}

installed=$(curl -sS "${auth[@]}" "${JELLYFIN_URL}/Plugins" |
  python3 -c "import json,sys; print(any(p.get('Id')=='$PLUGIN_GUID' for p in json.load(sys.stdin)))")

if [[ "$installed" != True ]]; then
  echo "Installing Jellyfin Webhook plugin $PLUGIN_VERSION..."
  curl -sS -X POST "${auth[@]}" \
    "${JELLYFIN_URL}/Packages/Installed/Webhook?Version=$PLUGIN_VERSION" -o /dev/null
  docker restart jellyfin >/dev/null
  wait_jellyfin
fi

export API_KEY PLUGIN_GUID WEBHOOK_URL JELLYFIN_URL
python3 <<'PY'
import json, os, urllib.request

guid = os.environ["PLUGIN_GUID"]
url = os.environ["WEBHOOK_URL"]
api_key = os.environ["API_KEY"]
jellyfin = os.environ["JELLYFIN_URL"].rstrip("/")
auth = {"Authorization": f'MediaBrowser Token="{api_key}"'}
cfg_url = f"{jellyfin}/Plugins/{guid}/Configuration"
req = urllib.request.Request(cfg_url, headers=auth)
config = json.load(urllib.request.urlopen(req))
name = "Arr delete sync"
generic = {
    "Name": name,
    "WebhookUri": url,
    "Enabled": True,
    "SendAllProperties": True,
    "Template": "",
    "NotificationTypes": ["ItemDeleted", "PlaybackStart"],
    "EnableMovies": True,
    "EnableEpisodes": True,
    "EnableSeasons": True,
    "EnableSeries": True,
    "EnableVideos": True,
    "UserFilter": [],
}
opts = config.get("GenericOptions") or []
config["GenericOptions"] = [g for g in opts if (g.get("Name") or "") != name] + [generic]
body = json.dumps(config).encode()
urllib.request.urlopen(
    urllib.request.Request(
        cfg_url,
        data=body,
        method="POST",
        headers={**auth, "Content-Type": "application/json"},
    )
)
print(f"Configured Generic webhook → {url}")
PY

mkdir -p "${HOME}/.config/systemd/user"
install -m 644 "$SERVICE_SRC" "${HOME}/.config/systemd/user/jellyfin-delete-webhook.service"
systemctl --user daemon-reload
systemctl --user enable --now jellyfin-delete-webhook.service

/usr/bin/python3 "$DIR/lib/register_arr_notifications.py" delete-sync

echo "Done. Webhook also runs jellyfin-delete-guard (duplicate UserData keys + DB ghost purge on ItemDeleted)."
echo "PlaybackStart only drops TMDB/IMDb UserData copies; item-GUID continue-watching rows are kept."
echo "When deleting items check «Delete from server» so files are removed from disk."
echo "Webhook listener: systemctl --user status jellyfin-delete-webhook.service"
