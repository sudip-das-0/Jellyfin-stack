#!/usr/bin/env python3
"""Storage targets for LG TV optimize (GiB/hour vs file size + duration)."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

MEDIA_ROOT = "/mnt/jellyfin/media"
DEFAULT_MAX_GIB_PER_HOUR = 5.0
SKIP_SUFFIX = (".lgstream.mkv", ".lgopt.tmp.mkv")
VIDEO_SUFFIXES = (".mkv", ".mp4", ".m2ts", ".ts")


def _load_media_lang_policy():
    policy = Path(__file__).resolve().parent / "media-lang-policy.py"
    spec = importlib.util.spec_from_file_location("mlp", policy)
    mlp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mlp)
    return mlp


def library_videos(media_root: str = MEDIA_ROOT) -> list[str]:
    out = []
    for root, _, files in os.walk(media_root):
        for name in files:
            low = name.lower()
            if not low.endswith(VIDEO_SUFFIXES):
                continue
            if any(name.endswith(s) for s in SKIP_SUFFIX):
                continue
            out.append(os.path.join(root, name))
    return out


def probe_streams(path: str, timeout: int = 90, media_root: str = MEDIA_ROOT):
    """Return (path, streams|None, duration_sec|None). streams is None on probe failure."""
    prefix = media_root.rstrip("/") + "/"
    if not path.startswith(prefix):
        return path, None, None
    rel = path[len(prefix) :]
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
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return path, None, None
    if r.returncode != 0:
        return path, None, None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return path, None, None
    dur = None
    try:
        dur = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = None
    if dur is not None and dur <= 0:
        dur = None
    return path, data.get("streams", []), dur


def gib_from_size(path: str, duration_sec: float | None) -> float | None:
    if duration_sec is None:
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    return gib_per_hour(size, duration_sec)


def audio_maps(streams, mlp) -> str:
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


def is_lg_friendly(
    streams,
    path: str,
    duration_sec: float | None,
    mlp,
    max_gib_per_hour: float,
) -> bool:
    """Match lg-tv-optimize-media.sh skip rules (already TV-ready + storage target)."""
    if not streams:
        return False
    gib = gib_from_size(path, duration_sec)
    if gib is not None and gib > max_gib_per_hour:
        return False
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    if not v or not audios:
        return False
    vcodec = v.get("codec_name", "")
    a0 = audios[0]
    acodec = a0.get("codec_name", "")
    ach = a0.get("channels", 0)
    scount = len(subs)
    acount = len(audios)
    maps = audio_maps(streams, mlp)

    if not maps:
        return vcodec == "h264" and acodec == "aac" and ach == 2 and scount == 0

    return (
        vcodec == "h264"
        and acodec == "aac"
        and ach == 2
        and scount == 0
        and acount == 1
    )


def needs_mkv_container(path: str) -> bool:
    low = path.lower()
    return low.endswith(".m2ts") or low.endswith(".ts")


def needs_optimize(
    path: str,
    streams,
    duration_sec: float | None,
    mlp,
    max_gib_per_hour: float,
) -> bool:
    if needs_mkv_container(path):
        return True
    gib = gib_from_size(path, duration_sec)
    if gib is not None and gib > max_gib_per_hour:
        return True
    return not is_lg_friendly(streams, path, duration_sec, mlp, max_gib_per_hour)


def scan_unoptimized(media_root: str = MEDIA_ROOT) -> tuple[list[str], str]:
    """Return (paths_needing_optimize, stats_line)."""
    mlp = _load_media_lang_policy()
    max_gib = float(os.environ.get("LG_TV_MAX_GIB_PER_HOUR", str(DEFAULT_MAX_GIB_PER_HOUR)))
    paths = library_videos(media_root)
    need: list[str] = []
    friendly = 0
    probe_fail = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(probe_streams, p, 90, media_root): p for p in paths}
        for fut in as_completed(futs):
            path, streams, duration_sec = fut.result()
            if streams is None:
                probe_fail += 1
                print(f"probe_fail: {path}", file=sys.stderr)
                continue
            if needs_optimize(path, streams, duration_sec, mlp, max_gib):
                need.append(path)
            else:
                friendly += 1
    need.sort()
    stats = (
        f"library_videos={len(paths)} lg_friendly={friendly} "
        f"needs_optimize={len(need)} probe_fail={probe_fail}"
    )
    return need, stats


def probe_duration_sec(host_path: str, timeout: int = 90) -> float | None:
    if not host_path.startswith(MEDIA_ROOT + "/"):
        return None
    rel = host_path[len(MEDIA_ROOT) + 1 :]
    cin = f"/media/{rel}"
    cmd = [
        "docker",
        "exec",
        "jellyfin",
        "/usr/lib/jellyfin-ffmpeg/ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        cin,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout or "{}")
        dur = (data.get("format") or {}).get("duration")
        if dur is None:
            return None
        val = float(dur)
        return val if val > 0 else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def gib_per_hour(size_bytes: int, duration_sec: float) -> float:
    return size_bytes * 3600.0 / duration_sec / (1024**3)


def file_gib_per_hour(host_path: str) -> float | None:
    try:
        size = os.path.getsize(host_path)
    except OSError:
        return None
    dur = probe_duration_sec(host_path)
    if dur is None:
        return None
    return gib_per_hour(size, dur)


def over_gib_target(
    host_path: str, max_gib_per_hour: float = DEFAULT_MAX_GIB_PER_HOUR
) -> bool:
    g = file_gib_per_hour(host_path)
    if g is None:
        return False
    return g > max_gib_per_hour


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] in {"--scan-unoptimized", "--scan"}:
        media_root = sys.argv[2] if len(sys.argv) > 2 else MEDIA_ROOT
        need, stats = scan_unoptimized(media_root)
        for p in need:
            print(p)
        print(stats, file=sys.stderr)
        return 0
    if len(sys.argv) < 2:
        print(
            "usage: lg_tv_storage.py <host-media-path> [max_gib_per_hour]"
            " | --scan-unoptimized [media_root]",
            file=sys.stderr,
        )
        return 2
    path = sys.argv[1]
    max_g = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MAX_GIB_PER_HOUR
    g = file_gib_per_hour(path)
    if g is None:
        print("unknown")
        return 1
    print(f"{g:.3f}")
    return 0 if g <= max_g else 3


if __name__ == "__main__":
    raise SystemExit(main())
