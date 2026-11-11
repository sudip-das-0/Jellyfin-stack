#!/usr/bin/env bash
# Queue media under /mnt/jellyfin/media that still need LG TV optimize (remux/transcode).
set -euo pipefail
# shellcheck source=stack-paths.sh
source /mnt/jellyfin/scripts/lib/stack-paths.sh

MEDIA_ROOT=/mnt/jellyfin/media
QUEUE_HELPER="${HOOKS_DIR}/lg-tv-queue-path.sh"
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && {
  echo "Usage: lg-tv-queue-unoptimized.sh [--dry-run]"
  exit 0
}

export LG_TV_QUEUE_NO_START=1

paths_file=$(mktemp)
count_file=$(mktemp)
/usr/bin/python3 - "$MEDIA_ROOT" <<'PY' >"$paths_file" 2>"$count_file"
import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

media_root = sys.argv[1]
skip_suffix = (".lgstream.mkv", ".lgopt.tmp.mkv")

spec = importlib.util.spec_from_file_location(
    "mlp", "/mnt/jellyfin/scripts/lib/media-lang-policy.py"
)
mlp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mlp)


def videos():
    out = []
    for root, _, files in os.walk(media_root):
        for name in files:
            low = name.lower()
            if not (
                low.endswith(".mkv")
                or low.endswith(".mp4")
                or low.endswith(".m2ts")
                or low.endswith(".ts")
            ):
                continue
            if any(name.endswith(s) for s in skip_suffix):
                continue
            out.append(os.path.join(root, name))
    return out


MAX_GIB_PER_HOUR = float(os.environ.get("LG_TV_MAX_GIB_PER_HOUR", "5"))


def ffprobe(path: str):
    rel = path[len("/mnt/jellyfin/media/") :]
    cin = f"/media/{rel}"
    cmd = [
        "docker",
        "exec",
        "jellyfin",
        "/usr/lib/jellyfin-ffmpeg/ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        cin,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        return path, None, None
    data = json.loads(r.stdout)
    dur = None
    try:
        dur = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = None
    if dur is not None and dur <= 0:
        dur = None
    return path, data.get("streams", []), dur


def file_gib_per_hour(path: str, duration_sec: float | None) -> float | None:
    if duration_sec is None:
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    return size * 3600.0 / duration_sec / (1024**3)


def audio_maps(streams):
    rows = []
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        lang = (s.get("tags") or {}).get("language", "")
        if not mlp.allowed(lang):
            continue
        rows.append((mlp.priority(lang), s.get("index", 0)))
    rows.sort()
    return " ".join(f"-map 0:{idx}" for _, idx in rows)


def is_lg_friendly(streams, path: str, duration_sec: float | None) -> bool:
    """Match lg-tv-optimize-media.sh skip rules (already TV-ready + storage target)."""
    if not streams:
        return False
    gib = file_gib_per_hour(path, duration_sec)
    if gib is not None and gib > MAX_GIB_PER_HOUR:
        return False
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    if not v or not audios:
        return False
    vcodec = v.get("codec_name", "")
    vbr = v.get("bit_rate")
    try:
        vbr_i = int(vbr) if vbr else None
    except ValueError:
        vbr_i = None
    a0 = audios[0]
    acodec = a0.get("codec_name", "")
    ach = a0.get("channels", 0)
    scount = len(subs)
    acount = len(audios)
    maps = audio_maps(streams)

    if not maps:
        return (
            vcodec == "h264"
            and acodec == "aac"
            and ach == 2
            and scount == 0
        )

    if (
        vcodec == "h264"
        and acodec == "aac"
        and ach == 2
        and scount == 0
        and acount == 1
    ):
        return True
    return False


def needs_mkv_container(path: str) -> bool:
    low = path.lower()
    return low.endswith(".m2ts") or low.endswith(".ts")


def needs_optimize(path: str, streams, duration_sec: float | None) -> bool:
    if needs_mkv_container(path):
        return True
    gib = file_gib_per_hour(path, duration_sec)
    if gib is not None and gib > MAX_GIB_PER_HOUR:
        return True
    return not is_lg_friendly(streams, path, duration_sec)


paths = videos()
need = []
friendly = 0
probe_fail = 0
with ThreadPoolExecutor(max_workers=6) as ex:
    futs = {ex.submit(ffprobe, p): p for p in paths}
    for fut in as_completed(futs):
        path, streams, duration_sec = fut.result()
        if streams is None:
            probe_fail += 1
            print(f"probe_fail: {path}", file=sys.stderr)
            continue
        if needs_optimize(path, streams, duration_sec):
            need.append(path)
        else:
            friendly += 1

need.sort()
for p in need:
    print(p)
print(
    f"library_videos={len(paths)} lg_friendly={friendly} needs_optimize={len(need)} probe_fail={probe_fail}",
    file=sys.stderr,
)
PY

stats=$(tr -d '\n' < "$count_file" 2>/dev/null || true)
count=$(printf '%s' "$stats" | sed -n 's/.*needs_optimize=\([0-9][0-9]*\).*/\1/p')
count=${count:-0}
rm -f "$count_file"

added=0
skipped=0
if [[ "$DRY" -eq 0 ]]; then
  systemctl --user stop lg-tv-optimize.service lg-tv-new-media-watch.service 2>/dev/null || true
  sleep 1
fi
merge_tmp=$(mktemp)
touch "$LG_TV_SKIPPED" 2>/dev/null || true
while IFS= read -r path; do
  [[ -n "$path" && -f "$path" ]] || continue
  if grep -Fxq "$path" "$LG_TV_SKIPPED" 2>/dev/null; then
    if /usr/bin/python3 /mnt/jellyfin/scripts/lib/lg_tv_storage.py "$path" "${LG_TV_MAX_GIB_PER_HOUR:-5}" >/dev/null 2>&1; then
      skipped=$((skipped + 1))
      continue
    fi
    grep -vxF "$path" "$LG_TV_SKIPPED" > "${LG_TV_SKIPPED}.tmp" 2>/dev/null \
      && mv "${LG_TV_SKIPPED}.tmp" "$LG_TV_SKIPPED" || true
  fi
  if [[ "$DRY" -eq 1 ]]; then
    echo "would queue: $path"
    added=$((added + 1))
    continue
  fi
  if grep -Fxq "$path" "$merge_tmp" 2>/dev/null; then
    skipped=$((skipped + 1))
    continue
  fi
  echo "$path" >>"$merge_tmp"
  added=$((added + 1))
done < "$paths_file"
rm -f "$paths_file"

if [[ "$DRY" -eq 0 ]]; then
  sort -u "$merge_tmp" | awk '!seen[$0]++' >"${merge_tmp}.out"
  mv -f "${merge_tmp}.out" "$LG_TV_QUEUE"
  chmod 600 "$LG_TV_QUEUE"
fi
rm -f "$merge_tmp"

"$LGTV_SCRIPTS/lg-tv-normalize-queue.sh" 2>/dev/null || true

echo "${stats:-needs_optimize=${count}} newly_queued=${added} already_in_queue=${skipped} dry_run=${DRY}"
if [[ "$DRY" -eq 0 ]]; then
  systemctl --user start --no-block lg-tv-optimize.service lg-tv-new-media-watch.service 2>/dev/null || true
fi
