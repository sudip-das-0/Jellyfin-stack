#!/usr/bin/env bash
# Append download activity to dated logs on /mnt/jellyfin (outside container bind mounts).
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

LOG_DIR="${LOG_ROOT}/downloads"
SONARR_KEY="$(read_arr_api_key "$SONARR_CONFIG")"
RADARR_KEY="$(read_arr_api_key "$RADARR_CONFIG")"
DATE="$(TZ=Asia/Kolkata date +%F)"
TS="$(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M:%S %Z')"
OUT="${LOG_DIR}/download-${DATE}.log"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

mkdir -p "$LOG_DIR"

{
  echo "=== ${TS} ==="

  if curl -sf -u "${QBIT_USER}:${QBIT_PASS}" "http://127.0.0.1:8080/api/v2/torrents/info" -o "$TMP"; then
    python3 - "$TMP" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
active = [t for t in data if t.get("state") not in ("pausedUP", "pausedDL", "missingFiles")]
if not active:
    print("qBittorrent: no active torrents")
else:
    for t in sorted(active, key=lambda x: -x.get("progress", 0)):
        pct = round(100 * t.get("progress", 0), 1)
        up = t.get("upspeed", 0) // 1024
        dl = t.get("dlspeed", 0) // 1024
        seeds = t.get("num_seeds", "?")
        print(
            f"  [{t.get('state')}] {pct}% seeds={seeds} "
            f"↓{dl}KiB/s ↑{up}KiB/s | {t.get('name', '')[:120]}"
        )
PY
  else
    echo "qBittorrent: unavailable"
  fi

  if curl -sf -H "X-Api-Key: ${SONARR_KEY}" "http://127.0.0.1:8989/api/v3/queue?pageSize=20" -o "$TMP"; then
    python3 - "$TMP" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
recs = data.get("records") or []
if not recs:
    print("Sonarr queue: empty")
else:
    for r in recs:
        title = r.get("series", {}).get("title") or r.get("title", "?")
        ep = r.get("episode", {})
        ep_label = f"S{ep.get('seasonNumber', '?')}E{ep.get('episodeNumber', '?')}" if ep else ""
        print(f"  Sonarr: {title} {ep_label} status={r.get('status')} {r.get('title', '')[:80]}")
PY
  else
    echo "Sonarr queue: unavailable"
  fi

  if curl -sf -H "X-Api-Key: ${RADARR_KEY}" "http://127.0.0.1:7878/api/v3/queue?pageSize=10" -o "$TMP"; then
    python3 - "$TMP" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
recs = data.get("records") or []
if not recs:
    print("Radarr queue: empty")
else:
    for r in recs:
        print(f"  Radarr: {r.get('title', '?')} status={r.get('status')}")
PY
  fi

  echo
} >> "$OUT"
