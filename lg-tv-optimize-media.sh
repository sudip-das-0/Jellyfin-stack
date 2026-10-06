#!/usr/bin/env bash
# Remux/transcode media for LG WebOS Jellyfin: H.264 + stereo AAC, no embedded subs.
# HEVC video encode order: Intel QSV → VAAPI → libx264 (CPU). Audio remux uses AAC (CPU).
# Usage: lg-tv-optimize-media.sh /mnt/jellyfin/media/series/Show/Season 1/file.mkv
set -euo pipefail

src="${1:-}"
if [[ -z "$src" || ! -f "$src" ]]; then
  exit 0
fi

case "$src" in
  *.mkv|*.mp4) ;;
  *) exit 0 ;;
esac

if [[ "$src" == *.lgstream.mkv ]]; then
  exit 0
fi

# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/stack-paths.sh
LOG=$LG_TV_LOG
mkdir -p "$(dirname "$LOG")"
log() { echo "$(date -Iseconds) $*" | tee -a "$LOG"; }

human_bytes() {
  local b=$1
  if [[ "$b" -ge 1073741824 ]]; then
    awk -v b="$b" 'BEGIN { printf "%.2f GiB", b/1073741824 }'
  elif [[ "$b" -ge 1048576 ]]; then
    awk -v b="$b" 'BEGIN { printf "%.1f MiB", b/1048576 }'
  else
    echo "${b} B"
  fi
}

TORRENTS_ROOT=/mnt/jellyfin/torrents

prune_empty_torrent_dir() {
  local dir="$1"
  while [[ "$dir" == "$TORRENTS_ROOT"/* ]]; do
    rmdir "$dir" 2>/dev/null || break
    dir=$(dirname "$dir")
  done
}

# Drop the download copy under torrents after media is ready (hardlink inode or copy fallback).
remove_torrent_originals() {
  local inode="$1"
  local media_file="$2"
  local size="$3"
  if [[ "${LG_TV_DELETE_TORRENT_ORIGINAL:-1}" == 0 ]]; then
    return 0
  fi
  if [[ ! -d "$TORRENTS_ROOT" ]]; then
    return 0
  fi

  local -a removed=()
  if [[ -n "$inode" ]]; then
    while IFS= read -r -d '' f; do
      rm -f "$f" && removed+=("$f")
    done < <(find "$TORRENTS_ROOT" -xdev -inum "$inode" -type f ! -path '*/incomplete/*' -print0 2>/dev/null)
  fi

  # Copied imports (no shared inode): same basename and byte size as pre-optimize media file.
  if [[ ${#removed[@]} -eq 0 && -n "$size" && "$size" -gt 0 ]]; then
    local base="${media_file##*/}"
    while IFS= read -r -d '' f; do
      [[ "$(stat -c '%s' "$f" 2>/dev/null || echo 0)" == "$size" ]] || continue
      rm -f "$f" && removed+=("$f")
    done < <(find "$TORRENTS_ROOT" -xdev -type f -name "$base" ! -path '*/incomplete/*' -print0 2>/dev/null)
  fi

  if [[ ${#removed[@]} -eq 0 ]]; then
    return 0
  fi
  for f in "${removed[@]}"; do
    log "removed torrent original: $f"
    prune_empty_torrent_dir "$(dirname "$f")"
  done
}

# Accept Sonarr (/data/media/...) or host (/mnt/jellyfin/media/...) paths.
if [[ "$src" == /data/media/* ]]; then
  src="/mnt/jellyfin/${src#/data/}"
fi
rel="${src#/mnt/jellyfin/media/}"
cin="/media/$rel"

if [[ -f /mnt/jellyfin/scripts/normalize-subtitle-sidecars.py ]]; then
  /usr/bin/python3 /mnt/jellyfin/scripts/normalize-subtitle-sidecars.py --video "$src" 2>/dev/null || true
fi
tmp_host="${src}.lgopt.tmp.mkv"
tmp_name="$(basename "$src").lgopt.tmp.mkv"
tmp_cin="/cache/lgopt/$tmp_name"

probe() {
  docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error "$1" \
    -select_streams v:0 -show_entries stream=codec_name,bit_rate -of csv=p=0 2>/dev/null
}
aprobe() {
  docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error "$1" \
    -select_streams a:0 -show_entries stream=codec_name,channels -of csv=p=0 2>/dev/null
}
subcount() {
  docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error "$1" \
    -show_entries stream=codec_type -select_streams s -of csv=p=0 2>/dev/null | wc -l
}
vheight() {
  docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error "$1" \
    -select_streams v:0 -show_entries stream=height -of csv=p=0 2>/dev/null
}
audio_map_args() {
  docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error -i "$1" \
    -select_streams a -show_entries stream=index:stream_tags=language \
    -of csv=p=0 2>/dev/null | python3 -c "
import importlib.util
import sys
spec = importlib.util.spec_from_file_location('mlp', '/mnt/jellyfin/scripts/media-lang-policy.py')
mlp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mlp)
rows = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    parts = line.split(',')
    idx = int(parts[0])
    lang = parts[1] if len(parts) > 1 else ''
    if not mlp.allowed(lang):
        continue
    rows.append((mlp.priority(lang), idx))
rows.sort()
print(' '.join(f'-map 0:{idx}' for _, idx in rows))
"
}
audio_count() {
  docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error "$1" \
    -show_entries stream=codec_type -select_streams a -of csv=p=0 2>/dev/null | wc -l
}

vinfo=$(probe "$cin") || vinfo=""
ainfo=$(aprobe "$cin") || ainfo=""
vcodec=${vinfo%%,*}
vbitrate=${vinfo##*,}
acodec=${ainfo%%,*}
achannels=${ainfo##*,}
subs=$(subcount "$cin" | tr -d ' ')
acount=$(audio_count "$cin" | tr -d ' ')
SOURCE_INODE=$(stat -c '%i' "$src" 2>/dev/null || true)
SOURCE_SIZE=$(stat -c '%s' "$src" 2>/dev/null || echo 0)
AUDIO_MAPS=$(audio_map_args "$cin")
if [[ -z "$AUDIO_MAPS" ]]; then
  # Remuxed files often drop language tags; still LG-playable (H.264 + stereo AAC).
  if [[ "$vcodec" == "h264" && "$acodec" == "aac" && "$achannels" == "2" && "$subs" == "0" ]]; then
    log "skip (already LG-friendly, untagged audio): $src"
    remove_torrent_originals "$SOURCE_INODE" "$src" "$SOURCE_SIZE"
    exit 0
  fi
  log "skip (no allowed audio tracks en/ja/ko/hi/bn): $src"
  exit 0
fi
max_h="${LG_TV_MAX_HEIGHT:-0}"
if [[ "$max_h" =~ ^[0-9]+$ && "$max_h" -gt 0 ]]; then
  vh=$(vheight "$cin" | tr -d ' ')
  if [[ -n "$vh" && "$vh" -gt "$max_h" ]]; then
    log "skip (height ${vh}p > max ${max_h}p): $src"
    exit 0
  fi
fi

# Preserve one English sidecar (.eng.srt) before remux strips embedded subs (-sn).
if [[ "$subs" != "0" ]]; then
  if [[ "${LG_TV_FORCE_SUB_REEXTRACT:-0}" == 1 ]]; then
    rm -f "${src%.*}.eng.srt"
  fi
  while read -r sub_idx suffix; do
    [[ -z "$sub_idx" || -z "$suffix" ]] && continue
    sidecar="${src%.*}.${suffix}.srt"
    [[ -f "$sidecar" ]] && continue
    ext_tmp="/cache/lgopt/${tmp_name}.${suffix}.srt"
    if docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -i "$cin" -map "0:${sub_idx}" -c:s srt "$ext_tmp" 2>/dev/null; then
      docker cp "jellyfin:${ext_tmp}" "$sidecar" 2>/dev/null && log "saved sidecar subs: $sidecar (stream $sub_idx)"
      docker exec jellyfin rm -f "$ext_tmp" 2>/dev/null || true
    fi
  done < <(
    docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error "$cin" \
      -select_streams s -show_entries stream=index:stream_tags=language:stream_tags=title \
      -of json 2>/dev/null | python3 -c "
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('mlp', '/mnt/jellyfin/scripts/media-lang-policy.py')
mlp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mlp)
data = json.load(sys.stdin)
for idx, suf in mlp.pick_sidecar_subtitles(data.get('streams') or []):
    print(idx, suf)
"
  )
fi

# Already TV-friendly: H.264, stereo AAC (single track), no embedded subtitles, reasonable bitrate.
if [[ "$vcodec" == "h264" && "$acodec" == "aac" && "$achannels" == "2" && "$subs" == "0" && "$acount" == "1" ]]; then
  if [[ -z "$vbitrate" || "$vbitrate" == "N/A" || "$vbitrate" -le 12000000 ]]; then
    log "skip (already LG-friendly): $src"
    remove_torrent_originals "$SOURCE_INODE" "$src" "$SOURCE_SIZE"
    exit 0
  fi
fi

bytes_before=$(stat -c%s "$src" 2>/dev/null || echo 0)
log "optimizing: $src (v=$vcodec a=$acodec ch=$achannels subs=$subs audio_tracks=$acount size=$(human_bytes "$bytes_before"))"

rm -f "$tmp_host"
docker exec jellyfin mkdir -p /cache/lgopt
docker exec jellyfin rm -f "$tmp_cin"

ENCODE_TIMEOUT="${LG_TV_ENCODE_TIMEOUT_SEC:-10800}"
Q="$(printf %q "$cin")"
O="$(printf %q "$tmp_cin")"
AUD_TAIL="$AUDIO_MAPS -c:a aac -ac 2 -b:a 192k -sn -map_metadata -1 -map_chapters -1 -max_muxing_queue_size 4096"

run_jellyfin_encode() {
  local tier=$1
  local body=$2
  local host_script remote="/cache/lgopt/enc-${tier}.sh"
  host_script=$(mktemp)
  {
    echo '#!/bin/bash'
    echo 'set -euo pipefail'
    echo "$body"
  } > "$host_script"
  chmod +x "$host_script"
  docker cp "$host_script" "jellyfin:${remote}" >/dev/null
  rm -f "$host_script"
  docker exec jellyfin rm -f "$tmp_cin" 2>/dev/null || true
  if timeout "$ENCODE_TIMEOUT" docker exec jellyfin bash "$remote"; then
    log "encode tier ok ($tier): $src"
    return 0
  fi
  local rc=$?
  docker exec jellyfin rm -f "$tmp_cin" 2>/dev/null || true
  if [[ "$rc" -eq 124 ]]; then
    log "encode tier timed out after ${ENCODE_TIMEOUT}s ($tier): $src"
  else
    log "encode tier failed ($tier): $src"
  fi
  return 1
}

hevc_to_h264_tiered() {
  local qsv_enc
  qsv_enc="-c:v h264_qsv -preset veryfast -async_depth 8 -bf 0 -g 240 -look_ahead 0 \
    -global_quality 24 -maxrate 10M -bufsize 20M"

  run_jellyfin_encode qsv-vaapi \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:/dev/dri/renderD128,driver=iHD \
      -init_hw_device qsv=qs@va -filter_hw_device qs \
      -hwaccel vaapi -hwaccel_output_format vaapi -extra_hw_frames 8 \
      -i $Q \
      -map 0:v:0 -vf 'scale_vaapi=format=nv12,hwmap=derive_device=qsv,format=qsv' \
      $qsv_enc \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode qsv-sw \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:/dev/dri/renderD128,driver=iHD \
      -init_hw_device qsv=qs@va -filter_hw_device qs \
      -i $Q \
      -map 0:v:0 -vf 'format=nv12,hwupload' \
      $qsv_enc \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode vaapi-hw \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:/dev/dri/renderD128,driver=iHD \
      -hwaccel vaapi -hwaccel_output_format vaapi \
      -i $Q \
      -map 0:v:0 -vf 'scale_vaapi=format=nv12' -c:v h264_vaapi -qp 24 -maxrate 10M -bufsize 20M \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode vaapi-sw \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:/dev/dri/renderD128,driver=iHD \
      -i $Q \
      -map 0:v:0 -vf 'format=nv12,hwupload' -c:v h264_vaapi -qp 24 -maxrate 10M -bufsize 20M \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode libx264 \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -i $Q \
      -map 0:v:0 -c:v libx264 -preset veryfast -crf 23 -maxrate 10M -bufsize 20M \
      $AUD_TAIL \
      $O" && return 0

  log "all encode tiers failed (qsv-vaapi, qsv-sw, vaapi, libx264): $src"
  return 1
}

if [[ "$vcodec" == "hevc" || "$vcodec" == "h265" ]]; then
  hevc_to_h264_tiered || exit 1
else
  run_jellyfin_encode h264-remux \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -i $Q \
      -map 0:v:0 -c:v copy \
      $AUDIO_MAPS -c:a aac -ac 2 -b:a 192k \
      -sn -map_metadata -1 -map_chapters -1 \
      -max_muxing_queue_size 4096 \
      $O" || exit 1
fi

if ! docker exec jellyfin test -s "$tmp_cin"; then
  log "optimize produced no output file: $src"
  docker exec jellyfin rm -f "$tmp_cin" 2>/dev/null || true
  exit 1
fi

docker cp "jellyfin:$tmp_cin" "$tmp_host"
docker exec jellyfin rm -f "$tmp_cin"
mv -f "$tmp_host" "$src"
bytes_after=$(stat -c%s "$src" 2>/dev/null || echo 0)
delta=$((bytes_after - bytes_before))
if [[ "$delta" -gt 0 ]]; then
  log "done: $src (size $(human_bytes "$bytes_before") -> $(human_bytes "$bytes_after"), +$(human_bytes "$delta") — transcode/remux often increases file size)"
elif [[ "$delta" -lt 0 ]]; then
  log "done: $src (size $(human_bytes "$bytes_before") -> $(human_bytes "$bytes_after"), -$(human_bytes "$((-delta))"))"
else
  log "done: $src (size unchanged at $(human_bytes "$bytes_after"))"
fi

remove_torrent_originals "$SOURCE_INODE" "$src" "$SOURCE_SIZE"
