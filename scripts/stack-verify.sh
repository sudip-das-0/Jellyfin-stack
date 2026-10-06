#!/usr/bin/env bash
# Verify script ↔ container ↔ systemd wiring for the media stack.
set -euo pipefail

DIR=$(dirname "$(readlink -f "$0")")
ERR=0
warn() { echo "WARN: $*" >&2; ERR=1; }
ok() { echo "OK: $*"; }

need_file() {
  [[ -f "$1" ]] || { warn "missing file: $1"; return; }
  ok "file $1"
}

need_exec() {
  [[ -x "$1" ]] || { warn "not executable: $1"; return; }
  ok "executable $1"
}

echo "=== Docker containers ==="
for c in jellyfin sonarr radarr bazarr qbittorrent prowlarr jellyseerr; do
  if docker ps --format '{{.Names}}' | grep -qx "$c"; then
    ok "container running: $c"
  else
    warn "container not running: $c"
  fi
done

echo "=== Sonarr/Radarr import scripts (in container) ==="
for c in sonarr radarr; do
  docker exec "$c" test -x /data/scripts/arr-import-postprocess.sh && ok "$c arr-import-postprocess.sh" || warn "$c missing import hook script"
done
ok "subtitle normalize runs on host (lg-tv-optimize-media.sh; *arr containers have no Python)"

echo "=== *arr API notifications ==="
SON_KEY=$(grep -oP '(?<=<ApiKey>)[^<]+' /home/devdas/arr/config/sonarr/config.xml)
RAD_KEY=$(grep -oP '(?<=<ApiKey>)[^<]+' /home/devdas/arr/config/radarr/config.xml)
check_hook() {
  local name=$1 url=$2 key=$3
  path=$(curl -sf -H "X-Api-Key: $key" "$url/api/v3/notification" | python3 -c "
import json,sys
name=sys.argv[1]
for n in json.load(sys.stdin):
  if n.get('name')==name and n.get('implementation')=='CustomScript':
    for f in n.get('fields',[]):
      if f.get('name')=='path':
        print(f.get('value',''))
        raise SystemExit(0)
raise SystemExit(1)
" "$name" 2>/dev/null) || { warn "$name not registered at $url"; return; }
  [[ "$path" == "/data/scripts/arr-import-postprocess.sh" ]] && ok "$name → $path" || warn "$name path unexpected: $path"
}
check_hook "Arr import postprocess (Sonarr)" "http://127.0.0.1:8989" "$SON_KEY"
check_hook "Arr import postprocess (Radarr)" "http://127.0.0.1:7878" "$RAD_KEY"

echo "=== Download policy (no seed) ==="
check_no_seed() {
  local url=$1 key=$2 label=$3
  curl -sf -H "X-Api-Key: $key" "$url/api/v3/downloadclient" | python3 -c "
import json,sys
d=json.load(sys.stdin)[0]
ok=d.get('removeCompletedDownloads') is True
print('ok' if ok else 'bad')
sys.exit(0 if ok else 1)
" &>/dev/null && ok "$label removeCompletedDownloads" || warn "$label still seeds (removeCompletedDownloads false)"
}
check_no_seed "http://127.0.0.1:8989" "$SON_KEY" "Sonarr"
check_no_seed "http://127.0.0.1:7878" "$RAD_KEY" "Radarr"
curl -sf -u Dimpu:adminadmin http://127.0.0.1:8080/api/v2/app/preferences | python3 -c "
import json,sys
p=json.load(sys.stdin)
ok=p.get('max_ratio_enabled') and float(p.get('max_ratio',-1))==0
sys.exit(0 if ok else 1)
" &>/dev/null && ok "qBittorrent max_ratio=0" || warn "qBittorrent seeding limits not set (run apply-no-seed-download-policy.sh)"

echo "=== Bazarr webhooks ==="
curl -sf -H "X-Api-Key: $SON_KEY" http://127.0.0.1:8989/api/v3/notification | python3 -c "
import json,sys
for n in json.load(sys.stdin):
  if n.get('name')=='Bazarr subtitles (Sonarr)':
    u=next((f['value'] for f in n['fields'] if f['name']=='url'),'')
    print(u)
    break
" | grep -q 'http://bazarr:6767/api/webhooks/sonarr' && ok "Sonarr → Bazarr webhook" || warn "Sonarr Bazarr webhook URL wrong/missing"

echo "=== Host scripts & systemd ==="
need_exec "$DIR/lg-tv-process-queue.sh"
need_exec "$DIR/lg-tv-optimize-daemon.sh"
need_exec "$DIR/lg-tv-optimize-media.sh"
need_exec /mnt/jellyfin/scripts/lg-tv-queue-path.sh
for unit in media-stack.service lg-tv-optimize.service lg-tv-new-media-watch.service torrent-stall-recovery.timer download-activity-log.timer; do
  systemctl --user is-enabled "$unit" &>/dev/null && ok "enabled $unit" || warn "not enabled: $unit"
done
systemctl --user is-active lg-tv-optimize.service &>/dev/null && ok "lg-tv-optimize.service running" || warn "lg-tv-optimize.service not active"
if systemctl --user is-enabled lg-tv-optimize.timer &>/dev/null; then
  warn "lg-tv-optimize.timer should be disabled (continuous worker replaces it)"
fi

echo "=== Jellyfin transcode device ==="
if timeout 15 docker exec jellyfin test -e /dev/dri/renderD128 2>/dev/null; then
  ok "Jellyfin renderD128"
else
  warn "Jellyfin renderD128 check failed (busy or no device)"
fi
if grep -q '<HardwareAccelerationType>qsv</HardwareAccelerationType>' /mnt/jellyfin/jellyfin/config/config/encoding.xml 2>/dev/null; then
  ok "encoding.xml QSV"
else
  warn "encoding.xml not set to QSV"
fi

echo "=== LG TV queue paths ==="
"$DIR/lg-tv-normalize-queue.sh" 2>/dev/null || true
# shellcheck source=stack-paths.sh
source /home/devdas/arr/scripts/stack-paths.sh
if [[ -f "$LG_TV_QUEUE" ]]; then
  if grep -q '^/data/media/' "$LG_TV_QUEUE" 2>/dev/null; then
    warn "queue still has /data/media paths (re-run lg-tv-normalize-queue.sh)"
  else
    ok "queue paths use /mnt/jellyfin"
  fi
fi

echo "=== Summary ==="
if [[ "$ERR" -eq 0 ]]; then
  echo "All checks passed."
  exit 0
fi
echo "Some checks failed. Try: $DIR/register-stack-hooks.sh"
exit 1
