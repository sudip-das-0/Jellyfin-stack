#!/usr/bin/env bash
# Remux/transcode media for LG WebOS Jellyfin: H.264 + stereo AAC, no embedded subs.
# Video: Intel QSV/VAAPI decode+encode first; libx264 last. Target LG_TV_MAXRATE (default 15M).
# Storage: skip when <= LG_TV_MAX_GIB_PER_HOUR (default 5 GiB/h); shrink/transcode above that.
# H.264 above target GiB/h: HW transcode when LG_TV_H264_SHRINK=1; else remux copy only when under target.
# AV1: all HW tiers first; libx264 only when LG_TV_AV1_ALLOW_CPU=1 (after LG_TV_AV1_HW_ATTEMPTS queue failures).
# LG_TV_HW_DECODE=0 disables HW decode on H.264 remux. LG_TV_AV1_QSV=0 forces CPU AV1 transcode.
# Usage: lg-tv-optimize-media.sh /mnt/jellyfin/media/series/Show/Season 1/file.mkv
set -euo pipefail

src="${1:-}"
if [[ -z "$src" || ! -f "$src" ]]; then
  exit 0
fi

case "$src" in
  *.mkv|*.mp4|*.m2ts|*.ts|*.M2TS|*.TS) ;;
  *) exit 0 ;;
esac

needs_mkv_container=0
case "$src" in
  *.m2ts|*.ts|*.M2TS|*.TS) needs_mkv_container=1 ;;
esac

if [[ "$src" == *.lgstream.mkv ]]; then
  exit 0
fi

# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/stack-paths.sh
LG_TV_DRI_NODE="$LG_TV_QSV_DEVICE"
LG_TV_DRI_DRIVER="$LG_TV_VAAPI_DRIVER"
LOG=$LG_TV_LOG
SKIPPED=$LG_TV_SKIPPED
mkdir -p "$(dirname "$LOG")" "$(dirname "$SKIPPED")"
log() { echo "$(date -Iseconds) $*" | tee -a "$LOG"; }

mark_permanent_skip() {
  touch "$SKIPPED"
  grep -Fxq "$src" "$SKIPPED" 2>/dev/null || echo "$src" >> "$SKIPPED"
  /mnt/jellyfin/scripts/lg-tv-purge-queue-path.sh "$src" 2>/dev/null || true
}

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
duration_sec() {
  docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error "$1" \
    -show_entries format=duration -of csv=p=0 2>/dev/null | head -1 | tr -d '[:space:]'
}
# ffprobe bit_rate is often empty on remuxes; fall back to container size / duration.
effective_video_bps() {
  local vb=${1:-}
  if [[ "$vb" =~ ^[0-9]+$ && "$vb" -gt 0 ]]; then
    echo "$vb"
    return 0
  fi
  local dur
  dur=$(duration_sec "$cin")
  dur=${dur%%.*}
  if [[ -z "$dur" || "$dur" -le 0 || "$SOURCE_SIZE" -le 0 ]]; then
    echo "N/A"
    return 0
  fi
  echo $(( SOURCE_SIZE * 8 / dur ))
}

media_gib_per_hour() {
  local size=$1 dur=$2
  dur=${dur%%.*}
  if [[ -z "$dur" || "$dur" -le 0 || "$size" -le 0 ]]; then
    echo "N/A"
    return 0
  fi
  awk -v b="$size" -v d="$dur" -v max="${LG_TV_MAX_GIB_PER_HOUR:-5}" \
    'BEGIN { printf "%.2f", b * 3600 / d / 1073741824 }'
}

over_storage_target() {
  local g=${1:-}
  if [[ -z "$g" || "$g" == "N/A" ]]; then
    if [[ "$EFF_VBPS" != "N/A" && "$EFF_VBPS" -gt "$LG_TV_TARGET_VBITRATE_BPS" ]]; then
      return 0
    fi
    return 1
  fi
  awk -v g="$g" -v max="${LG_TV_MAX_GIB_PER_HOUR:-5}" 'BEGIN { exit (g > max ? 0 : 1) }'
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
MEDIA_DUR=$(duration_sec "$cin")
MEDIA_GIB=$(media_gib_per_hour "$SOURCE_SIZE" "$MEDIA_DUR")
EFF_VBPS=$(effective_video_bps "$vbitrate")
AUDIO_MAPS=$(audio_map_args "$cin")
if [[ -z "$AUDIO_MAPS" ]]; then
  # Remuxed files often drop language tags; still LG-playable (H.264 + stereo AAC).
  if [[ "$needs_mkv_container" -eq 0 && "$vcodec" == "h264" && "$acodec" == "aac" && "$achannels" == "2" && "$subs" == "0" ]] \
    && ! over_storage_target "$MEDIA_GIB"; then
    log "skip (already LG-friendly, untagged audio, ${MEDIA_GIB} GiB/h): $src"
    mark_permanent_skip
    remove_torrent_originals "$SOURCE_INODE" "$src" "$SOURCE_SIZE"
    exit 0
  fi
  first_a_idx=$(docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error "$cin" \
    -select_streams a:0 -show_entries stream=index -of csv=p=0 2>/dev/null | head -1 | tr -d '[:space:]')
  if [[ -n "$first_a_idx" ]]; then
    AUDIO_MAPS="-map 0:${first_a_idx}"
    log "remux: no tagged en/ja/ko/hi/bn audio; using first audio track for $src"
  else
    log "skip (no audio streams): $src"
    exit 0
  fi
fi
max_h="${LG_TV_MAX_HEIGHT:-0}"
if [[ "$max_h" =~ ^[0-9]+$ && "$max_h" -gt 0 ]]; then
  vh=$(vheight "$cin" | tr -d ' ')
  if [[ -n "$vh" && "$vh" -gt "$max_h" ]]; then
    log "skip (height ${vh}p > max ${max_h}p): $src"
    mark_permanent_skip
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

# Already TV-friendly: H.264, stereo AAC, no embedded subtitles, at/below storage target (GiB/h).
if [[ "$needs_mkv_container" -eq 0 && "$vcodec" == "h264" && "$acodec" == "aac" && "$achannels" == "2" && "$subs" == "0" && "$acount" -ge 1 ]]; then
  if ! over_storage_target "$MEDIA_GIB"; then
    log "skip (already LG-friendly, ${MEDIA_GIB} GiB/h): $src"
    mark_permanent_skip
    remove_torrent_originals "$SOURCE_INODE" "$src" "$SOURCE_SIZE"
    exit 0
  fi
fi

bytes_before=$(stat -c%s "$src" 2>/dev/null || echo 0)
log "optimizing: $src (v=$vcodec a=$acodec ch=$achannels subs=$subs audio_tracks=$acount gib_h=${MEDIA_GIB} size=$(human_bytes "$bytes_before"))"

ENCODE_LOCK="${LG_TV_DIR}/encode.lock"
exec 201>"$ENCODE_LOCK"
if ! flock -w "${LG_TV_ENCODE_LOCK_WAIT_SEC:-86400}" 201; then
  log "encode lock busy (timeout): $src"
  exit 1
fi

# After exclusive lock: drop orphan jellyfin-ffmpeg jobs left under /cache/lgopt (daemon restart, etc.).
docker exec jellyfin bash -c 'for p in /proc/[0-9]*; do
  cmd=$(tr "\0" " " < "$p/cmdline" 2>/dev/null) || continue
  case "$cmd" in *ffmpeg*cache/lgopt*) kill "${p##*/}" 2>/dev/null ;; esac
done' 2>/dev/null || true

rm -f "$tmp_host"
docker exec jellyfin mkdir -p /cache/lgopt
docker exec jellyfin rm -f "$tmp_cin"

ENCODE_TIMEOUT="${LG_TV_ENCODE_TIMEOUT_SEC:-10800}"
Q="$(printf %q "$cin")"
O="$(printf %q "$tmp_cin")"
AUD_TAIL="$AUDIO_MAPS -c:a aac -ac 2 -b:a 192k -sn -map_metadata -1 -map_chapters -1 -max_muxing_queue_size 4096"

stop_lgopt_encoders() {
  # Kill stale encoders for this output only (not other queue jobs).
  local needle
  needle=$(printf %q "$tmp_name")
  docker exec jellyfin bash -c "for p in /proc/[0-9]*; do
    cmd=\$(tr '\0' ' ' < \"\$p/cmdline\" 2>/dev/null) || continue
    case \"\$cmd\" in *${needle}*) kill \"\${p##*/}\" 2>/dev/null ;; esac
  done" 2>/dev/null || true
  docker exec jellyfin rm -f "$tmp_cin" 2>/dev/null || true
}

run_jellyfin_encode() {
  local tier=$1
  local body=$2
  local host_script remote="/cache/lgopt/enc-${tier}.sh"
  stop_lgopt_encoders
  host_script=$(mktemp)
  {
    echo '#!/bin/bash'
    echo 'set -euo pipefail'
    echo "$body"
  } > "$host_script"
  chmod +x "$host_script"
  docker cp "$host_script" "jellyfin:${remote}" >/dev/null
  rm -f "$host_script"
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
    -global_quality 24 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE}"

  run_jellyfin_encode hevc-qsv \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -init_hw_device qsv=qs@va -filter_hw_device qs \
      -hwaccel qsv -hwaccel_output_format qsv -c:v hevc_qsv \
      -i $Q \
      -map 0:v:0 -vf 'scale_qsv=format=nv12' \
      $qsv_enc \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode qsv-vaapi \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -init_hw_device qsv=qs@va -filter_hw_device qs \
      -hwaccel vaapi -hwaccel_output_format vaapi -extra_hw_frames 8 \
      -i $Q \
      -map 0:v:0 -vf 'scale_vaapi=format=nv12,hwmap=derive_device=qsv,format=qsv' \
      $qsv_enc \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode qsv-sw \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -init_hw_device qsv=qs@va -filter_hw_device qs \
      -i $Q \
      -map 0:v:0 -vf 'format=nv12,hwupload' \
      $qsv_enc \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode vaapi-hw \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -hwaccel vaapi -hwaccel_output_format vaapi \
      -i $Q \
      -map 0:v:0 -vf 'scale_vaapi=format=nv12' -c:v h264_vaapi -qp 24 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE} \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode vaapi-sw \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -i $Q \
      -map 0:v:0 -vf 'format=nv12,hwupload' -c:v h264_vaapi -qp 24 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE} \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode libx264 \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -i $Q \
      -map 0:v:0 -c:v libx264 -preset veryfast -crf 23 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE} \
      $AUD_TAIL \
      $O" && return 0

  log "all encode tiers failed (qsv-vaapi, qsv-sw, vaapi, libx264): $src"
  return 1
}

h264_to_h264_tiered() {
  local qsv_enc
  qsv_enc="-c:v h264_qsv -preset veryfast -async_depth 8 -bf 0 -g 240 -look_ahead 0 \
    -global_quality 24 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE}"

  run_jellyfin_encode h264-shrink-qsv \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -init_hw_device qsv=qs@va -filter_hw_device qs \
      -hwaccel qsv -hwaccel_output_format qsv -c:v h264_qsv \
      -i $Q \
      -map 0:v:0 -vf 'scale_qsv=format=nv12' \
      $qsv_enc \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode h264-shrink-qsv-vaapi \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -init_hw_device qsv=qs@va -filter_hw_device qs \
      -hwaccel vaapi -hwaccel_output_format vaapi -extra_hw_frames 8 \
      -i $Q \
      -map 0:v:0 -vf 'scale_vaapi=format=nv12,hwmap=derive_device=qsv,format=qsv' \
      $qsv_enc \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode h264-shrink-vaapi-hw \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -hwaccel vaapi -hwaccel_output_format vaapi \
      -i $Q \
      -map 0:v:0 -vf 'scale_vaapi=format=nv12' -c:v h264_vaapi -qp 24 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE} \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode h264-shrink-libx264 \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -i $Q \
      -map 0:v:0 -c:v libx264 -preset veryfast -crf 23 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE} \
      $AUD_TAIL \
      $O" && return 0

  log "all H.264 shrink tiers failed (qsv, vaapi, libx264): $src"
  return 1
}

h264_needs_shrink() {
  if [[ "${LG_TV_H264_SHRINK:-1}" != 1 ]]; then
    return 1
  fi
  over_storage_target "$MEDIA_GIB"
}

av1_to_h264_tiered() {
  local qsv_enc
  qsv_enc="-c:v h264_qsv -preset veryfast -async_depth 8 -bf 0 -g 240 -look_ahead 0 \
    -global_quality 24 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE}"

  run_jellyfin_encode av1-qsv \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -init_hw_device qsv=qs@va -filter_hw_device qs \
      -hwaccel qsv -hwaccel_output_format qsv -c:v av1_qsv \
      -i $Q \
      -map 0:v:0 -vf 'scale_qsv=format=nv12' \
      $qsv_enc \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode av1-qsv-vaapi \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -init_hw_device qsv=qs@va -filter_hw_device qs \
      -hwaccel vaapi -hwaccel_output_format vaapi \
      -i $Q \
      -map 0:v:0 -vf 'scale_vaapi=format=nv12,hwmap=derive_device=qsv,format=qsv' \
      $qsv_enc \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode av1-dav1d-qsv-enc \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -init_hw_device qsv=qs@va -filter_hw_device qs \
      -i $Q \
      -map 0:v:0 -vf 'format=nv12,hwupload=extra_hw_frames=64' \
      $qsv_enc \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode av1-vaapi-hw \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -hwaccel vaapi -hwaccel_output_format vaapi \
      -i $Q \
      -map 0:v:0 -vf 'scale_vaapi=format=nv12' -c:v h264_vaapi -qp 24 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE} \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode av1-vaapi-sw \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -i $Q \
      -map 0:v:0 -vf 'format=nv12,hwupload' -c:v h264_vaapi -qp 24 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE} \
      $AUD_TAIL \
      $O" && return 0

  if [[ "${LG_TV_AV1_ALLOW_CPU:-0}" == 1 ]]; then
    log "AV1 HW tiers exhausted; trying libx264 (CPU) after prior queue failures: $src"
    run_jellyfin_encode libx264 \
      "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
        -i $Q \
        -map 0:v:0 -c:v libx264 -preset veryfast -crf 23 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE} \
        $AUD_TAIL \
        $O" && return 0
  fi

  log "all AV1 HW encode tiers failed (will re-queue; libx264 after ${LG_TV_AV1_HW_ATTEMPTS:-3} failures): $src"
  return 1
}

vp9_to_h264_tiered() {
  local qsv_enc
  qsv_enc="-c:v h264_qsv -preset veryfast -async_depth 8 -bf 0 -g 240 -look_ahead 0 \
    -global_quality 24 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE}"

  run_jellyfin_encode vp9-qsv \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -init_hw_device qsv=qs@va -filter_hw_device qs \
      -hwaccel qsv -hwaccel_output_format qsv -c:v vp9_qsv \
      -i $Q \
      -map 0:v:0 -vf 'scale_qsv=format=nv12' \
      $qsv_enc \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode vp9-qsv-vaapi \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -init_hw_device qsv=qs@va -filter_hw_device qs \
      -hwaccel vaapi -hwaccel_output_format vaapi \
      -i $Q \
      -map 0:v:0 -vf 'scale_vaapi=format=nv12,hwmap=derive_device=qsv,format=qsv' \
      $qsv_enc \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode vp9-vaapi-hw \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -hwaccel vaapi -hwaccel_output_format vaapi \
      -i $Q \
      -map 0:v:0 -vf 'scale_vaapi=format=nv12' -c:v h264_vaapi -qp 24 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE} \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode libx264 \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -i $Q \
      -map 0:v:0 -c:v libx264 -preset veryfast -crf 23 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE} \
      $AUD_TAIL \
      $O" && return 0

  log "all VP9 encode tiers failed: $src"
  return 1
}

generic_to_h264_tiered() {
  local qsv_enc
  qsv_enc="-c:v h264_qsv -preset veryfast -async_depth 8 -bf 0 -g 240 -look_ahead 0 \
    -global_quality 24 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE}"

  run_jellyfin_encode generic-vaapi-hw \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -init_hw_device qsv=qs@va -filter_hw_device qs \
      -hwaccel vaapi -hwaccel_output_format vaapi \
      -i $Q \
      -map 0:v:0 -vf 'scale_vaapi=format=nv12,hwmap=derive_device=qsv,format=qsv' \
      $qsv_enc \
      $AUD_TAIL \
      $O" && return 0

  run_jellyfin_encode generic-vaapi-enc \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
      -hwaccel vaapi -hwaccel_output_format vaapi \
      -i $Q \
      -map 0:v:0 -vf 'scale_vaapi=format=nv12' -c:v h264_vaapi -qp 24 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE} \
      $AUD_TAIL \
      $O" && return 0

  if libx264_from_source; then
    return 0
  fi
  log "all generic encode tiers failed: $src"
  return 1
}

libx264_from_source() {
  run_jellyfin_encode libx264 \
    "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
      -i $Q \
      -map 0:v:0 -c:v libx264 -preset veryfast -crf 23 -maxrate ${LG_TV_MAXRATE} -bufsize ${LG_TV_BUFSIZE} \
      $AUD_TAIL \
      $O"
}

if [[ "$vcodec" == "hevc" || "$vcodec" == "h265" ]]; then
  hevc_to_h264_tiered || exit 1
elif [[ "$vcodec" == "av1" ]]; then
  if [[ "${LG_TV_AV1_QSV:-1}" == 1 ]]; then
    av1_to_h264_tiered || exit 1
  else
    libx264_from_source || exit 1
  fi
elif [[ "$vcodec" == "h264" ]]; then
  if h264_needs_shrink; then
    log "h264 HW shrink (${MEDIA_GIB} GiB/h > ${LG_TV_MAX_GIB_PER_HOUR:-5}, maxrate ${LG_TV_MAXRATE:-15M}, size=$(human_bytes "$SOURCE_SIZE")): $src"
    h264_to_h264_tiered || exit 1
  else
  h264_remux_ok=0
  if [[ "${LG_TV_HW_DECODE:-1}" == 1 ]]; then
    if run_jellyfin_encode h264-remux-hw \
      "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
        -init_hw_device vaapi=va:${LG_TV_DRI_NODE},driver=${LG_TV_DRI_DRIVER} \
        -hwaccel qsv -hwaccel_output_format qsv -c:v h264_qsv \
        -i $Q \
        -map 0:v:0 -c:v copy \
        $AUDIO_MAPS -c:a aac -ac 2 -b:a 192k \
        -sn -map_chapters -1 \
        -max_muxing_queue_size 4096 \
        $O"; then
      h264_remux_ok=1
    fi
  fi
  if [[ "$h264_remux_ok" -eq 0 ]]; then
    stop_lgopt_encoders
    run_jellyfin_encode h264-remux \
      "/usr/lib/jellyfin-ffmpeg/ffmpeg -y -hide_banner -loglevel error \
        -i $Q \
        -map 0:v:0 -c:v copy \
        $AUDIO_MAPS -c:a aac -ac 2 -b:a 192k \
        -sn -map_chapters -1 \
        -max_muxing_queue_size 4096 \
        $O" || exit 1
  fi
  fi
elif [[ "$vcodec" == "vp9" ]]; then
  vp9_to_h264_tiered || exit 1
else
  generic_to_h264_tiered || exit 1
fi

if ! docker exec jellyfin test -s "$tmp_cin"; then
  log "optimize produced no output file: $src"
  docker exec jellyfin rm -f "$tmp_cin" 2>/dev/null || true
  exit 1
fi

docker cp "jellyfin:$tmp_cin" "$tmp_host"
docker exec jellyfin rm -f "$tmp_cin"
final_path="$src"
case "$src" in
  *.m2ts|*.ts|*.M2TS|*.TS)
    final_path="${src%.*}.mkv"
    ;;
esac
mv -f "$tmp_host" "$final_path"
if [[ "$final_path" != "$src" ]]; then
  log "replaced container: $src -> $final_path"
  rm -f "$src"
  /mnt/jellyfin/scripts/lg-tv-purge-queue-path.sh "$src" 2>/dev/null || true
  src="$final_path"
  rel="${src#/mnt/jellyfin/media/}"
fi
bytes_after=$(stat -c%s "$src" 2>/dev/null || echo 0)
delta=$((bytes_after - bytes_before))
if [[ "$delta" -gt 0 ]]; then
  log "done: $src (size $(human_bytes "$bytes_before") -> $(human_bytes "$bytes_after"), +$(human_bytes "$delta") — transcode/remux often increases file size)"
elif [[ "$delta" -lt 0 ]]; then
  log "done: $src (size $(human_bytes "$bytes_before") -> $(human_bytes "$bytes_after"), -$(human_bytes "$((-delta))"))"
else
  log "done: $src (size unchanged at $(human_bytes "$bytes_after"))"
fi

/mnt/jellyfin/scripts/lg-tv-purge-queue-path.sh "$src" 2>/dev/null || true
/mnt/jellyfin/scripts/jellyfin-queue-episode-image-refresh.sh "$src" 2>/dev/null || true
remove_torrent_originals "$SOURCE_INODE" "$src" "$SOURCE_SIZE"
