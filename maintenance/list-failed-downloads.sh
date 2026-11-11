#!/usr/bin/env bash
# List failed / stuck downloads across Sonarr, Radarr, qBittorrent, and LG optimize dead-letter.
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

SONARR_KEY="$(read_arr_api_key "$SONARR_CONFIG")"
RADARR_KEY="$(read_arr_api_key "$RADARR_CONFIG")"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

section() { echo; echo "=== $* ==="; }

section "Sonarr — Activity → Queue (failed / warning)"
curl -sf -H "X-Api-Key: ${SONARR_KEY}" \
  "http://127.0.0.1:8989/api/v3/queue?pageSize=250&includeUnknownSeriesItems=true" -o "$TMP" || {
  echo "Sonarr unavailable"
}
python3 - "$TMP" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
bad = []
for r in data.get("records") or []:
    st = r.get("status") or ""
    tr = r.get("trackedDownloadStatus") or ""
    err = (r.get("errorMessage") or "").strip()
    if st == "failed" or tr in ("importFailed", "downloadFailed"):
        bad.append(r)
    elif st == "warning" and err:
        bad.append(r)
if not bad:
    print("  (none)")
else:
    for r in bad:
        ep = r.get("episode") or {}
        label = f"S{ep.get('seasonNumber', '?')}E{ep.get('episodeNumber', '?')}"
        series = (r.get("series") or {}).get("title") or r.get("title") or "?"
        print(f"  {series} {label} | {st}/{tr} | {err[:100]}")
        print(f"    release: {(r.get('title') or '')[:90]}")
PY

section "Sonarr — History → download failed (eventType=4)"
curl -sf -H "X-Api-Key: ${SONARR_KEY}" \
  "http://127.0.0.1:8989/api/v3/history?pageSize=100&eventType=4" -o "$TMP" || true
python3 - "$TMP" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    print("  (unavailable)")
    raise SystemExit
recs = data.get("records") or []
if not recs:
    print("  (none in last 100)")
else:
    for r in recs:
        print(f"  epId={r.get('episodeId')} | {(r.get('sourceTitle') or '')[:100]}")
print(f"  total in history: {data.get('totalRecords', '?')}")
PY

section "Sonarr — Blocklist"
curl -sf -H "X-Api-Key: ${SONARR_KEY}" \
  "http://127.0.0.1:8989/api/v3/blocklist?pageSize=50" -o "$TMP" || true
python3 - "$TMP" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
recs = data.get("records") or []
if not recs:
    print("  (empty)")
else:
    for r in recs:
        print(f"  {(r.get('sourceTitle') or r.get('title') or '?')[:100]}")
print(f"  total: {data.get('totalRecords', len(recs))}")
PY

section "Orphan videos in /mnt/jellyfin/torrents (not in library)"
python3 - <<'PY' 2>/dev/null || echo "  (reconcile script unavailable)"
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("r", "/mnt/jellyfin/scripts/maintenance/reconcile-torrents-library.py")
spec2 = importlib.util.spec_from_file_location("h", "/mnt/jellyfin/scripts/lib/orphan_import_helpers.py")
# lightweight count
TOR = Path("/mnt/jellyfin/torrents")
VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".wmv", ".ts"}
n = sum(1 for p in TOR.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO and "incomplete" not in p.parts)
print(f"  video files under torrents (ex incomplete): {n}")
print("  fix: runs every 5m via download-maintenance → reconcile-torrents-library.py")
PY

section "Sonarr — Wanted → Missing (count only; full list in UI)"
curl -sf -H "X-Api-Key: ${SONARR_KEY}" \
  "http://127.0.0.1:8989/api/v3/wanted/missing?pageSize=1" -o "$TMP" || true
python3 - "$TMP" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
print(f"  missing episodes (monitored): {d.get('totalRecords', '?')}")
print("  UI: Sonarr → Wanted → Missing")
PY

section "Radarr — Queue (failed / warning)"
curl -sf -H "X-Api-Key: ${RADARR_KEY}" \
  "http://127.0.0.1:7878/api/v3/queue?pageSize=100" -o "$TMP" || echo "Radarr unavailable"
python3 - "$TMP" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
bad = []
for r in data.get("records") or []:
    st = r.get("status") or ""
    tr = r.get("trackedDownloadStatus") or ""
    err = (r.get("errorMessage") or "").strip()
    if st == "failed" or tr in ("importFailed", "downloadFailed") or (st == "warning" and err):
        bad.append(r)
if not bad:
    print("  (none)")
else:
    for r in bad:
        print(f"  {(r.get('title') or '?')[:90]} | {st}/{tr} | {err[:80]}")
PY

section "Radarr — Blocklist"
curl -sf -H "X-Api-Key: ${RADARR_KEY}" \
  "http://127.0.0.1:7878/api/v3/blocklist?pageSize=50" -o "$TMP" || true
python3 - "$TMP" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
recs = data.get("records") or []
print(f"  total: {data.get('totalRecords', len(recs))}")
for r in recs[:20]:
    print(f"  {(r.get('sourceTitle') or '?')[:100]}")
PY

section "qBittorrent — error / missing / stalled"
curl -sf -u "${QBIT_USER}:${QBIT_PASS}" "http://127.0.0.1:8080/api/v2/torrents/info" -o "$TMP" || echo "qBittorrent unavailable"
python3 - "$TMP" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
bad_states = {"missingFiles", "error", "stalledDL"}
bad = [t for t in data if t.get("state") in bad_states]
if not bad:
    print("  (none)")
else:
    for t in bad:
        print(f"  [{t.get('state')}] {(t.get('name') or '')[:100]}")
PY

DEAD="/mnt/jellyfin/logs/lg-tv/optimize.deadletter.log"
if [[ -f "$DEAD" ]]; then
  section "LG TV optimize — dead-letter (encode failed 3×)"
  tail -n 20 "$DEAD" | sed 's/^/  /'
else
  section "LG TV optimize — dead-letter"
  echo "  (no dead-letter file)"
fi

echo
echo "Tip: periodic snapshots also go to /mnt/jellyfin/logs/downloads/download-\$(date +%F).log"
