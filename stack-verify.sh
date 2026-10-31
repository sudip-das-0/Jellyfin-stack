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
ok=(
  p.get('max_ratio_enabled')
  and float(p.get('max_ratio',-1))==0
  and int(p.get('max_ratio_act',-1))==0
)
sys.exit(0 if ok else 1)
" &>/dev/null && ok "qBittorrent max_ratio=0 (pause; *arr removes after import)" || warn "qBittorrent seeding limits wrong (run apply-no-seed-download-policy.sh)"

echo "=== Bazarr webhooks ==="
curl -sf -H "X-Api-Key: $SON_KEY" http://127.0.0.1:8989/api/v3/notification | python3 -c "
import json,sys
for n in json.load(sys.stdin):
  if n.get('name')=='Bazarr subtitles (Sonarr)':
    u=next((f['value'] for f in n['fields'] if f['name']=='url'),'')
    print(u)
    break
" | grep -q 'http://bazarr:6767/api/webhooks/sonarr' && ok "Sonarr → Bazarr webhook" || warn "Sonarr Bazarr webhook URL wrong/missing"

echo "=== Bazarr ↔ Sonarr/Radarr (Docker DNS) ==="
docker exec bazarr getent hosts sonarr radarr &>/dev/null && ok "Bazarr resolves sonarr/radarr" || warn "Bazarr cannot resolve sonarr/radarr (split compose networks — recreate stack from /mnt/jellyfin/scripts)"
BAZ_KEY=$(grep -A2 '^auth:' /home/devdas/arr/config/bazarr/config/config.yaml | grep apikey | awk '{print $2}')
curl -sf -H "X-Api-Key: $BAZ_KEY" http://127.0.0.1:6767/api/system/status | python3 -c "
import json,sys
d=json.load(sys.stdin).get('data',{})
ok=d.get('sonarr_version') not in (None,'unknown','') and d.get('radarr_version') not in (None,'unknown','')
print('ok' if ok else 'bad')
sys.exit(0 if ok else 1)
" &>/dev/null && ok "Bazarr sees Sonarr/Radarr versions" || warn "Bazarr Sonarr/Radarr connection broken (run bazarr-resync-from-arr.sh after fixing network)"

echo "=== Jellyfin Open Subtitles plugin ==="
JF_KEY=$(sqlite3 /mnt/jellyfin/jellyfin/config/data/jellyfin.db "SELECT AccessToken FROM ApiKeys LIMIT 1;" 2>/dev/null || true)
if [[ -n "${JF_KEY:-}" ]]; then
  curl -sf -H "Authorization: MediaBrowser Token=\"$JF_KEY\"" http://127.0.0.1:8096/Plugins | python3 -c "
import json,sys
for p in json.load(sys.stdin):
  if p.get('Id')=='4b9ed42f518548b598036ff2989014c4' and p.get('Status')=='Active':
    raise SystemExit(0)
raise SystemExit(1)
" &>/dev/null && ok "Open Subtitles plugin active" || warn "Open Subtitles plugin missing (run apply-jellyfin-opensubtitles.sh)"
else
  warn "Cannot check Jellyfin plugins (no API key)"
fi

echo "=== Host scripts & systemd ==="
if [[ -L /home/devdas/arr/scripts ]]; then
  target=$(readlink -f /home/devdas/arr/scripts)
  [[ "$target" == "$DIR" ]] && ok "arr/scripts → $DIR" || warn "arr/scripts symlink → $target (expected $DIR)"
elif [[ -d /home/devdas/arr/scripts ]]; then
  warn "/home/devdas/arr/scripts is a directory (expected symlink to $DIR)"
else
  warn "missing /home/devdas/arr/scripts symlink (ln -sfn $DIR /home/devdas/arr/scripts)"
fi
need_exec "$DIR/lg-tv-process-queue.sh"
need_exec "$DIR/lg-tv-optimize-daemon.sh"
need_exec "$DIR/lg-tv-optimize-media.sh"
need_exec "$DIR/lg-tv-queue-av1-for-qsv.sh"
need_exec "$DIR/hooks/lg-tv-queue-path.sh"
if grep -q 'download-maintenance.lock' "$DIR/download-maintenance.sh" 2>/dev/null; then
  ok "download-maintenance uses flock"
else
  warn "download-maintenance missing flock (overlapping runs can wedge CPU)"
fi
need_exec "$DIR/hooks/import-sidecar-subs.sh"
REPO_UNIT="$DIR/systemd/user/lg-tv-optimize.service"
USER_UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/lg-tv-optimize.service"
if [[ -f "$REPO_UNIT" && -f "$USER_UNIT" ]]; then
  for var in LG_TV_AV1_HW_ATTEMPTS LG_TV_OPTIMIZE_MAX_RETRIES LG_TV_HW_DECODE LG_TV_AV1_QSV; do
    repo_val=$(grep -E "^Environment=${var}=" "$REPO_UNIT" | head -1 | cut -d= -f3- || true)
    user_val=$(grep -E "^Environment=${var}=" "$USER_UNIT" | head -1 | cut -d= -f3- || true)
    if [[ -n "$repo_val" && "$repo_val" == "$user_val" ]]; then
      ok "lg-tv-optimize.service $var=$user_val"
    elif [[ -z "$user_val" ]]; then
      warn "lg-tv-optimize.service missing $var (run apply-qsv-transcode-policy.sh or copy repo unit)"
    else
      warn "lg-tv-optimize.service drift: $var repo=$repo_val user=$user_val"
    fi
  done
fi
for unit in media-stack.service lg-tv-optimize.service lg-tv-new-media-watch.service torrent-stall-recovery.timer download-activity-log.timer qbit-network-profile.timer; do
  systemctl --user is-enabled "$unit" &>/dev/null && ok "enabled $unit" || warn "not enabled: $unit"
done
systemctl --user is-active lg-tv-optimize.service &>/dev/null && ok "lg-tv-optimize.service running" || warn "lg-tv-optimize.service not active"
if systemctl --user is-enabled lg-tv-optimize.timer &>/dev/null; then
  warn "lg-tv-optimize.timer should be disabled (continuous worker replaces it)"
fi
for unit in queue-missing-downloads.timer torrent-library-dedupe.timer; do
  systemctl --user is-enabled "$unit" &>/dev/null && ok "enabled $unit" || warn "not enabled: $unit"
done

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
ENC_XML=/mnt/jellyfin/jellyfin/config/config/encoding.xml
if [[ -f "$ENC_XML" ]]; then
  for c in h264 hevc av1 vp9; do
    if grep -q "<string>$c</string>" "$ENC_XML" 2>/dev/null; then
      ok "encoding.xml HW decode: $c"
    else
      warn "encoding.xml missing HW decode codec: $c"
    fi
  done
  if grep -q '<PreferSystemNativeHwDecoder>true</PreferSystemNativeHwDecoder>' "$ENC_XML" 2>/dev/null; then
    ok "encoding.xml PreferSystemNativeHwDecoder"
  else
    warn "encoding.xml PreferSystemNativeHwDecoder not true"
  fi
  if grep -q '<EnableHardwareEncoding>true</EnableHardwareEncoding>' "$ENC_XML" 2>/dev/null; then
    ok "encoding.xml hardware encoding enabled"
  else
    warn "encoding.xml hardware encoding disabled"
  fi
fi

echo "=== LG optimize HW decode tiers ==="
OPT_SCRIPT="$DIR/lg-tv-optimize-media.sh"
if [[ -f "$OPT_SCRIPT" ]]; then
  for needle in hevc-qsv av1-qsv vp9-qsv h264-remux-hw hwaccel; do
    if grep -q "$needle" "$OPT_SCRIPT" 2>/dev/null; then
      ok "optimize script includes $needle"
    else
      warn "optimize script missing $needle"
    fi
  done
  first_libx=$(grep -n 'run_jellyfin_encode libx264' "$OPT_SCRIPT" | head -1 | cut -d: -f1 || echo 0)
  first_hw=$(grep -n 'hwaccel' "$OPT_SCRIPT" | head -1 | cut -d: -f1 || echo 99999)
  if [[ "$first_hw" -lt "$first_libx" && "$first_libx" -gt 0 ]]; then
    ok "optimize script lists HW decode before libx264 fallback"
  else
    warn "optimize script HW/libx264 tier order unexpected"
  fi
  if timeout 20 docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -decoders 2>/dev/null \
    | grep -qE 'av1_qsv|hevc_qsv|h264_qsv|vp9_qsv'; then
    ok "Jellyfin ffmpeg QSV decoders present"
  else
    warn "Jellyfin ffmpeg QSV decoders not found"
  fi
fi

echo "=== LG TV queue paths ==="
"$DIR/lg-tv-normalize-queue.sh" 2>/dev/null || true
# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/stack-paths.sh
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
