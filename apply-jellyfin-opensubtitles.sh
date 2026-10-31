#!/usr/bin/env bash
# Install/configure Jellyfin Open Subtitles plugin (same OpenSubtitles.com account as Bazarr).
set -euo pipefail

CFG=/home/devdas/arr/config/bazarr/config/config.yaml
JELLYFIN_DB=/mnt/jellyfin/jellyfin/config/data/jellyfin.db
PLUGIN_GUID=4b9ed42f518548b598036ff2989014c4
PLUGIN_VERSION=25.0.0.0

read_os_cred() {
  awk -v key="$1" '
    /^opensubtitlescom:/ { block=1; next }
    block && /^[a-z]/ { block=0 }
    block && $1 == (key ":") { print $2; exit }
  ' "$CFG"
}

USER=$(read_os_cred username)
PASS=$(read_os_cred password)
[[ -n "$USER" && -n "$PASS" ]] || {
  echo "Missing opensubtitlescom username/password in $CFG" >&2
  exit 1
}

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
  echo "Installing Open Subtitles plugin $PLUGIN_VERSION..."
  curl -sS -X POST "${auth[@]}" \
    "http://127.0.0.1:8096/Packages/Installed/Open%20Subtitles?Version=$PLUGIN_VERSION" -o /dev/null
  docker restart jellyfin >/dev/null
  wait_jellyfin
fi

echo "Configuring Open Subtitles credentials..."
body=$(CFG="$CFG" python3 <<'PY'
import json, os, re
from pathlib import Path
text = Path(os.environ["CFG"]).read_text(encoding="utf-8")
block = re.search(r"^opensubtitlescom:\n(.*?)(?=^\w|\Z)", text, re.M | re.S)
vals = {}
if block:
    for line in block.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            vals[k.strip()] = v.strip()
print(json.dumps({"Username": vals.get("username", ""), "Password": vals.get("password", ""), "CredentialsInvalid": False}))
PY
)
curl -sS -X POST "${auth[@]}" -H "Content-Type: application/json" \
  -d "$body" \
  "http://127.0.0.1:8096/Plugins/$PLUGIN_GUID/Configuration" -o /dev/null

python3 <<'PY'
from pathlib import Path
import re

libs = (
    "/mnt/jellyfin/jellyfin/config/root/default/Series/options.xml",
    "/mnt/jellyfin/jellyfin/config/root/default/Anime/options.xml",
    "/mnt/jellyfin/jellyfin/config/root/default/Movies/options.xml",
    "/mnt/jellyfin/jellyfin/config/root/default/Other/options.xml",
)
fetcher_block = """  <DisabledSubtitleFetchers />
  <SubtitleFetcherOrder>
    <string>Open Subtitles</string>
  </SubtitleFetcherOrder>"""

for rel in libs:
    p = Path(rel)
    text = p.read_text(encoding="utf-8")
    text = re.sub(
        r"<DisabledSubtitleFetchers\s*/>.*?<SubtitleFetcherOrder\s*/>",
        fetcher_block,
        text,
        count=1,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"<RequirePerfectSubtitleMatch>.*?</RequirePerfectSubtitleMatch>",
        "<RequirePerfectSubtitleMatch>false</RequirePerfectSubtitleMatch>",
        text,
        count=1,
        flags=re.DOTALL,
    )
    p.write_text(text, encoding="utf-8")
    print(f"Jellyfin library {p.parent.name}: Open Subtitles enabled, perfect match off")
PY

echo "Done. Restart Jellyfin if libraries were open during edit."
docker restart jellyfin >/dev/null 2>&1 || true
wait_jellyfin || true
