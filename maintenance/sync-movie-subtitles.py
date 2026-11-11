#!/usr/bin/env python3
"""Align sidecar English .srt subtitles to video audio (ffsubsync fallback).

LAPSE (Jellyfin plugin) is the default autosync. This script is a CLI fallback.
Writes synced text to `{stem}.srt` and keeps `{stem}.srt.unsynced` as backup.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from arr_common import LOG_ROOT, MEDIA_ROOT, SCRIPTS_ROOT, TZ, echo_script_log, jellyfin_volume_ready

MEDIA_MOVIES = MEDIA_ROOT / "movies"
LOG_DIR = LOG_ROOT / "subs-sync"
STATE_PATH = LOG_DIR / "state.json"
VENV_FFS = SCRIPTS_ROOT / ".venv-subsync/bin/ffs"
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi"}
LIBRARY_DIRS = ("movies", "series", "anime")
FOREIGN_SRT = re.compile(
    r"\.(jpn|ja|kor|ko|hin|hi|fre|fra|fr|deu|ger|de|spa|es|ita|it|por|pt|rus|ru|chi|zh)\.srt$",
    re.I,
)


def log(msg: str) -> None:
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    line = f"{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')} {msg}"
    echo_script_log(LOG_DIR / f"sync-{day}.log", line)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"movies": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"movies": {}}


def save_state(state: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def pick_english_srt(folder: Path, stem: str) -> Path | None:
    preferred = [
        folder / f"{stem}.srt",
        folder / f"{stem}.eng.srt",
        folder / f"{stem}.en.srt",
    ]
    for p in preferred:
        if p.is_file() and not FOREIGN_SRT.search(p.name):
            return p
    for p in sorted(folder.glob("*.srt")):
        if FOREIGN_SRT.search(p.name):
            continue
        if stem in p.stem:
            return p
    return None


def parse_ffsubsync_log(text: str) -> dict:
    info: dict[str, object] = {}
    m = re.search(r"offset seconds:\s*([-\d.]+)", text)
    if m:
        info["offset_seconds"] = float(m.group(1))
    if "leaving subtitles unmodified" in text:
        info["skipped"] = True
    if "writing output to" in text:
        info["wrote"] = True
    return info


def sync_one(
    video: Path,
    *,
    ffs: Path,
    max_offset: float,
    split_penalty: bool,
    dry_run: bool,
    force: bool,
) -> str:
    folder = video.parent
    stem = video.stem
    key = str(video.resolve())
    state = load_state()
    prev = (state.get("movies") or {}).get(key) or {}
    if prev.get("status") == "ok" and not force:
        return f"skip (already synced): {video.name}"

    src = pick_english_srt(folder, stem)
    if not src:
        return f"skip (no English srt): {video.name}"

    out_tmp = folder / f"{stem}.synced.srt"
    backup = folder / f"{stem}.srt.unsynced"
    primary = folder / f"{stem}.srt"

    if dry_run:
        return f"dry-run: {video.name} ({src.name})"

    if not ffs.is_file():
        raise RuntimeError(f"ffsubsync missing at {ffs}")

    args = [
        str(ffs),
        str(video),
        "-i",
        str(src),
        "-o",
        str(out_tmp),
        "--max-offset-seconds",
        str(int(max_offset)),
        "--quality-max-offset-seconds",
        str(int(max_offset)),
        "--ffmpeg-path",
        "/usr/bin",
    ]
    if split_penalty:
        args.append("--split-penalty")

    proc = subprocess.run(args, capture_output=True, text=True)
    log_text = (proc.stdout or "") + (proc.stderr or "")
    meta = parse_ffsubsync_log(log_text)

    if proc.returncode != 0 or meta.get("skipped") or not out_tmp.is_file():
        meta["status"] = "failed"
        meta["at"] = datetime.now(timezone.utc).isoformat()
        state.setdefault("movies", {})[key] = meta
        save_state(state)
        tail = " | ".join(log_text.strip().splitlines()[-2:])
        return f"FAIL {video.name}: {tail[:200]}"

    if primary.is_file() and not backup.is_file():
        shutil.copy2(primary, backup)
    elif not backup.is_file():
        shutil.copy2(src, backup)

    shutil.copy2(out_tmp, primary)
    out_tmp.unlink(missing_ok=True)
    eng = folder / f"{stem}.eng.srt"
    if eng.exists():
        eng.unlink(missing_ok=True)
    try:
        eng.symlink_to(primary.name)
    except OSError:
        shutil.copy2(primary, eng)

    meta["status"] = "ok"
    meta["at"] = datetime.now(timezone.utc).isoformat()
    meta["source"] = src.name
    state.setdefault("movies", {})[key] = meta
    save_state(state)
    off = meta.get("offset_seconds", "?")
    return f"OK {video.name} (offset {off}s)"


def iter_videos(root: Path) -> list[Path]:
    out: list[Path] = []
    if not root.is_dir():
        return out
    if root.is_file() and root.suffix.lower() in VIDEO_EXT:
        return [root]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".", ".."}]
        folder = Path(dirpath)
        for name in filenames:
            p = folder / name
            if p.suffix.lower() in VIDEO_EXT and p.is_file():
                out.append(p)
    out.sort()
    return out


def iter_library_videos() -> list[Path]:
    out: list[Path] = []
    for name in LIBRARY_DIRS:
        out.extend(iter_videos(MEDIA_ROOT / name))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--movie", help='Movie folder name, e.g. "The Wolf of Wall Street (2013)"')
    parser.add_argument("--path", help="Video file or folder (movies/series/anime)")
    parser.add_argument("--all", action="store_true", help="All videos under media/movies, series, anime")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--max-offset",
        type=float,
        default=float(os.environ.get("SUBSYNC_MAX_OFFSET_SEC", "180")),
    )
    parser.add_argument(
        "--split-penalty",
        action="store_true",
        help="Piecewise sync (can break Jellyfin with overlapping cues; off by default)",
    )
    args = parser.parse_args()

    if not jellyfin_volume_ready():
        log("skipped — jellyfin SSD not ready")
        return 0

    ffs = Path(os.environ.get("FFS_BIN", VENV_FFS))
    if args.path:
        target = Path(args.path)
        if not target.exists():
            print(f"Not found: {target}", file=sys.stderr)
            return 1
        targets = iter_videos(target)
    elif args.movie:
        folder = MEDIA_MOVIES / args.movie
        if not folder.is_dir():
            print(f"Not found: {folder}", file=sys.stderr)
            return 1
        targets = iter_videos(folder)
    elif args.all:
        targets = iter_library_videos()
        if args.limit > 0:
            targets = targets[: args.limit]
    else:
        parser.error("use --path, --movie, or --all")

    ok = fail = skip = 0
    for video in targets:
        msg = sync_one(
            video,
            ffs=ffs,
            max_offset=args.max_offset,
            split_penalty=args.split_penalty,
            dry_run=args.dry_run,
            force=args.force,
        )
        log(msg)
        if msg.startswith("OK"):
            ok += 1
        elif msg.startswith("FAIL"):
            fail += 1
        else:
            skip += 1

    log(f"summary: ok={ok} fail={fail} skip={skip}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
