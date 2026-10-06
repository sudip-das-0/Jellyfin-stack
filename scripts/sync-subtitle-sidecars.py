#!/usr/bin/env python3
"""Align {stem}.eng.srt to video audio (alass) and strip common release junk lines."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BIN = SCRIPT_DIR.parent / "bin"
ALASS = BIN / "alass"
VIDEO_SUFFIXES = {".mkv", ".mp4"}

_WATERMARK_BLOCK = re.compile(
    r"^\d+\s*\n\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}\s*\n"
    r"(?:Downloaded from|www\.YIFY|YIFY|YTS\.(?:MX|AG|LT)|RARBG).*?(?:\n\n|\Z)",
    re.M | re.I,
)
_TIME = re.compile(r"(\d+):(\d+):(\d+),(\d+)")


def default_media_root() -> Path:
    for candidate in (Path("/mnt/jellyfin/media"), Path("/data/media")):
        if candidate.is_dir():
            return candidate
    return Path("/mnt/jellyfin/media")


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{BIN}:{env.get('PATH', '')}"
    return env


def iter_videos(root: Path):
    for path in root.rglob("*"):
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        if ".lgopt.tmp" in path.name or path.name.endswith(".lgstream.mkv"):
            continue
        yield path


def sub_stats(text: str) -> tuple[float | None, float | None, int]:
    times = _TIME.findall(text)
    if not times:
        return None, None, 0

    def ts(parts: tuple[str, ...]) -> float:
        h, m, s, ms = map(int, parts)
        return h * 3600 + m * 60 + s + ms / 1000

    return ts(times[0]), ts(times[-1]), len(times)


def video_duration(video: Path) -> float | None:
    if not (BIN / "ffprobe").is_file():
        return None
    try:
        out = subprocess.check_output(
            [
                str(BIN / "ffprobe"),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(video),
            ],
            stderr=subprocess.DEVNULL,
            timeout=120,
            env=_env(),
        )
        return float(out.decode().strip())
    except (subprocess.SubprocessError, ValueError):
        return None


def strip_release_watermarks(text: str) -> str:
    prev = None
    cur = text
    while prev != cur:
        prev = cur
        cur = _WATERMARK_BLOCK.sub("", cur, count=8)
    return cur.lstrip("\ufeff")


def likely_needs_sync(video: Path, sub: Path, text: str) -> bool:
    name = f"{video.name} {sub.name}".upper()
    if "YTS" in name or "YIFY" in name:
        return True
    head = text[:800].lower()
    if "downloaded from" in head or "yts." in head:
        return True
    first, last, _ = sub_stats(text)
    if first is None:
        return True
    dur = video_duration(video)
    if dur is not None and last is not None:
        tail_gap = dur - last
        if tail_gap > 120 or tail_gap < -45:
            return True
    if first > 240:
        return True
    return False


def run_alass(video: Path, sub: Path, out: Path) -> None:
    if not ALASS.is_file():
        raise FileNotFoundError(f"alass not found at {ALASS}")
    subprocess.run(
        [str(ALASS), str(video), str(sub), str(out)],
        check=True,
        env=_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def sync_sidecar(video: Path, *, dry_run: bool, force: bool) -> str:
    """Returns: synced | cleaned | unchanged | skipped | error."""
    sub = video.with_suffix(".eng.srt")
    if not sub.is_file():
        return "skipped"
    try:
        original = sub.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "error"

    cleaned = strip_release_watermarks(original)
    need_alass = force or likely_needs_sync(video, sub, cleaned)

    synced = cleaned
    if need_alass:
        fd, tmp_name = tempfile.mkstemp(suffix=".srt", prefix="alass-")
        os.close(fd)
        tmp_out = Path(tmp_name)
        try:
            run_alass(video, sub, tmp_out)
            synced = strip_release_watermarks(
                tmp_out.read_text(encoding="utf-8", errors="replace")
            )
        except subprocess.CalledProcessError as exc:
            print(f"sync error {video}: {exc.stderr[-400:] if exc.stderr else exc}", file=sys.stderr)
            synced = cleaned
        finally:
            tmp_out.unlink(missing_ok=True)
    else:
        synced = cleaned

    if synced == original:
        return "unchanged"

    if dry_run:
        return "synced" if need_alass else "cleaned"

    backup = sub.with_suffix(".eng.srt.bak")
    if not backup.is_file():
        shutil.copy2(sub, backup)
    sub.write_text(synced, encoding="utf-8")
    return "synced" if need_alass else "cleaned"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--video", type=Path, action="append", default=[])
    parser.add_argument("--all", action="store_true", help="Run alass on every sidecar (slow)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = args.root or default_media_root()

    if args.video:
        videos = [Path(v) for v in args.video]
    else:
        videos = list(iter_videos(root))

    counts = {"synced": 0, "cleaned": 0, "unchanged": 0, "skipped": 0, "error": 0}
    for video in videos:
        result = sync_sidecar(video, dry_run=args.dry_run, force=args.all)
        counts[result] = counts.get(result, 0) + 1

    mode = "dry-run" if args.dry_run else "applied"
    print(
        f"sync-subtitle-sidecars ({mode}): videos={len(videos)} "
        f"synced={counts['synced']} cleaned={counts['cleaned']} "
        f"unchanged={counts['unchanged']} skipped={counts['skipped']} errors={counts['error']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
