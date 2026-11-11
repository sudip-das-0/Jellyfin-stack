#!/usr/bin/env bash
# Purpose: Subtitle/audio language policy (Bazarr + Jellyfin + *arr formats).
# Runs: register-stack-hooks.sh
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

POLICY="${LIB_DIR}/media-lang-policy.py"
/usr/bin/python3 "${APPLY_DIR}/apply-tv-direct-play-formats.py"

SON=$(read_arr_api_key "$SONARR_CONFIG")
RAD=$(read_arr_api_key "$RADARR_CONFIG")
export BAZARR_DB BAZARR_CONFIG

python3 <<PY
import importlib.util
import json, sqlite3

spec = importlib.util.spec_from_file_location("mlp", "$POLICY")
mlp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mlp)

import os
BAZARR_DB = os.environ["BAZARR_DB"]
items = [
    {
        "language": c,
        "forced": False,
        "hi": False,
        "audio_only": False,
        "itemType": "language",
        "id": i + 1,
        "audio_exclude": "False",
        "audio_only_include": "False",
    }
    for i, c in enumerate(mlp.BAZARR_SUBTITLE_LANGS)
]
conn = sqlite3.connect(BAZARR_DB)
conn.execute(
    "UPDATE table_languages_profiles SET name=?, items=? WHERE profileId=1",
    ("English subtitles", json.dumps(items)),
)
conn.commit()
conn.close()
print("Bazarr subtitle profile:", list(mlp.BAZARR_SUBTITLE_LANGS))
print("Audio allow-list (grabs/remux):", list(mlp.JELLYFIN_AUDIO_LANGS))
PY

python3 <<PY
import json, urllib.request
for port, key in (("8989", "$SON"), ("7878", "$RAD")):
    base = f"http://127.0.0.1:{port}"
    req = urllib.request.Request(f"{base}/api/v3/releaseprofile", headers={"X-Api-Key": key})
    profiles = json.load(urllib.request.urlopen(req))
    body = {
        "enabled": True,
        "required": [],
        "ignored": ["VOSTFR", "VFQ", "VFO", "SUBFRENCH"],
        "indexerId": 0,
        "tags": [],
    }
    if profiles:
        body = {**profiles[0], **body}
        url = f"{base}/api/v3/releaseprofile/{profiles[0]['id']}"
        method = "PUT"
    else:
        url = f"{base}/api/v3/releaseprofile"
        method = "POST"
    urllib.request.urlopen(
        urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            method=method,
            headers={"X-Api-Key": key, "Content-Type": "application/json"},
        )
    )
    print(f"{base}: release profile updated")
PY

/usr/bin/python3 <<PY
from pathlib import Path
import importlib.util
import re

spec = importlib.util.spec_from_file_location("mlp", "$POLICY")
mlp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mlp)

subtitle_langs = list(mlp.JELLYFIN_SUBTITLE_LANGS)
block = "\n".join(f"    <string>{c}</string>" for c in subtitle_langs)
for rel in (
    "/mnt/jellyfin/jellyfin/config/root/default/Series/options.xml",
    "/mnt/jellyfin/jellyfin/config/root/default/Anime/options.xml",
    "/mnt/jellyfin/jellyfin/config/root/default/Movies/options.xml",
    "/mnt/jellyfin/jellyfin/config/root/default/Other/options.xml",
):
    p = Path(rel)
    text = p.read_text(encoding="utf-8")
    new = re.sub(
        r"<SubtitleDownloadLanguages>.*?</SubtitleDownloadLanguages>",
        f"<SubtitleDownloadLanguages>\n{block}\n  </SubtitleDownloadLanguages>",
        text,
        count=1,
        flags=re.DOTALL,
    )
    new = re.sub(
        r"<AllowEmbeddedSubtitles>.*?</AllowEmbeddedSubtitles>",
        f"<AllowEmbeddedSubtitles>{mlp.JELLYFIN_ALLOW_EMBEDDED_SUBTITLES}</AllowEmbeddedSubtitles>",
        new,
        count=1,
        flags=re.DOTALL,
    )
    new = re.sub(
        r"<RequirePerfectSubtitleMatch>.*?</RequirePerfectSubtitleMatch>",
        "<RequirePerfectSubtitleMatch>false</RequirePerfectSubtitleMatch>",
        new,
        count=1,
        flags=re.DOTALL,
    )
    p.write_text(new, encoding="utf-8")
    print(
        f"Jellyfin {p.parent.name}: subtitle langs {subtitle_langs}, "
        f"embedded={mlp.JELLYFIN_ALLOW_EMBEDDED_SUBTITLES}"
    )
PY

sqlite3 "$JELLYFIN_DB" <<'SQL'
UPDATE Users
SET AudioLanguagePreference = 'eng',
    SubtitleLanguagePreference = 'eng',
    SubtitleMode = 1,
    PlayDefaultAudioTrack = 1,
    RememberSubtitleSelections = 1,
    RememberAudioSelections = 1,
    EnableUserPreferenceAccess = 1;
SQL

python3 <<'PY'
from pathlib import Path
import os
import re

cfg = Path(os.environ["BAZARR_CONFIG"])
text = cfg.read_text(encoding="utf-8")
replacements = {
    r"^  serie_default_profile: .*$": "  serie_default_profile: 1",
    r"^  movie_default_profile: .*$": "  movie_default_profile: 1",
    r"^  single_language: .*$": "  single_language: true",
    r"^  use_embedded_subs: .*$": "  use_embedded_subs: false",
}
for pattern, repl in replacements.items():
    text = re.sub(pattern, repl, text, count=1, flags=re.M)
cfg.write_text(text, encoding="utf-8")
print("Bazarr defaults: profileId=1 (English subtitles), single_language=true, use_embedded_subs=false")
PY

BAZ_KEY=$(read_bazarr_api_key)
curl -sS -X POST -H "X-Api-Key: $BAZ_KEY" "http://127.0.0.1:6767/api/system/tasks?taskid=update_series" -o /dev/null
curl -sS -X POST -H "X-Api-Key: $BAZ_KEY" "http://127.0.0.1:6767/api/system/tasks?taskid=update_movies" -o /dev/null
docker restart bazarr >/dev/null 2>&1 || true
sleep 10
/mnt/jellyfin/scripts/apply/bazarr-apply-opensubtitles-apikey.sh || true
docker restart jellyfin >/dev/null 2>&1 || true
echo "Media language policy applied."
