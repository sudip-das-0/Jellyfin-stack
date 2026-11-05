#!/usr/bin/env python3
"""Storage targets for LG TV optimize (GiB/hour vs file size + duration)."""
from __future__ import annotations

import json
import os
import subprocess
import sys

MEDIA_ROOT = "/mnt/jellyfin/media"
DEFAULT_MAX_GIB_PER_HOUR = 3.5


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
    if len(sys.argv) < 2:
        print("usage: lg_tv_storage.py <host-media-path> [max_gib_per_hour]", file=sys.stderr)
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
