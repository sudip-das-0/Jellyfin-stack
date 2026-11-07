#!/usr/bin/env bash
# Jellyfin UI delete → Sonarr/Radarr unmonitor (Webhook plugin + local listener).
set -euo pipefail

DIR=$(dirname "$(readlink -f "$0")")
JELLYFIN_DB=/mnt/jellyfin/jellyfin/config/data/jellyfin.db
PLUGIN_GUID=71552a5a5c5c4350a2aeebe451a30173
PLUGIN_VERSION=22.0.0.0
WEBHOOK_URL="http://127.0.0.1:9407/"
SERVICE_SRC="$DIR/systemd/user/jellyfin-delete-webhook.service"

chmod +x "$DIR/jellyfin-delete-webhook.py"

API_KEY=$(sqlite3 "$JELLYFIN_DB" "SELECT AccessToken FROM ApiKeys LIMIT 1;")
[[ -n "$API_KEY" ]] || {
  echo "No Jellyfin API key in $JELLYFIN_DB" >&2
  exit 1
}
auth=(-H "Authorization: MediaBrowser Token=\"$API_KEY\"")

wait_jellyfin() {
  for _ in $(seq 1 40); do
    curl -sf http://127.0.0.1:8096/System/Info/Public >/dev/null 2>&1 && return 0
    sleep 2
  done
  echo "Jellyfin not responding on :8096" >&2
  return 1
}

installed=$(curl -sS "${auth[@]}" http://127.0.0.1:8096/Plugins |
  python3 -c "import json,sys; print(any(p.get('Id')=='$PLUGIN_GUID' for p in json.load(sys.stdin)))")

if [[ "$installed" != True ]]; then
  echo "Installing Jellyfin Webhook plugin $PLUGIN_VERSION..."
  curl -sS -X POST "${auth[@]}" \
    "http://127.0.0.1:8096/Packages/Installed/Webhook?Version=$PLUGIN_VERSION" -o /dev/null
  docker restart jellyfin >/dev/null
  wait_jellyfin
fi

export API_KEY PLUGIN_GUID WEBHOOK_URL
python3 <<'PY'
import json, os, urllib.request

guid = os.environ["PLUGIN_GUID"]
url = os.environ["WEBHOOK_URL"]
api_key = os.environ["API_KEY"]
auth = {"Authorization": f'MediaBrowser Token="{api_key}"'}
cfg_url = f"http://127.0.0.1:8096/Plugins/{guid}/Configuration"
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

"$DIR/register-arr-delete-sync.sh"

echo "Done. Webhook also runs jellyfin-delete-guard (UserData sanitization + DB ghost purge on ItemDeleted)."
echo "PlaybackStart events clear bad UserData before you delete from the UI."
echo "When deleting items check «Delete from server» so files are removed from disk."
echo "Webhook listener: systemctl --user status jellyfin-delete-webhook.service"
