#!/usr/bin/env python3
"""Report missing or suspicious English sidecar subtitles under /mnt/jellyfin/media."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SYNC = SCRIPT_DIR / "sync-subtitle-sidecars.py"
VIDEO_SUFFIXES = {".mkv", ".mp4"}


def load_sync():
    spec = importlib.util.spec_from_file_location("sync_subs", SYNC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args()
    sync = load_sync()
    root = args.root or sync.default_media_root()

    missing: list[str] = []
    flagged: list[tuple[str, str]] = []
    dup_content: list[tuple[str, str]] = []
    ok = 0

    season_srts: dict[Path, list[Path]] = defaultdict(list)

    for video in sync.iter_videos(root):
        rel = str(video.relative_to(root))
        sub = video.with_suffix(".eng.srt")
        if not sub.is_file():
            missing.append(rel)
            continue
        season_srts[sub.parent].append(sub)
        try:
            text = sub.read_text(encoding="utf-8", errors="replace")
        except OSError:
            flagged.append((rel, "unreadable"))
            continue
        if sync.likely_needs_sync(video, sub, text):
            first, last, n = sync.sub_stats(text)
            dur = sync.video_duration(video)
            bits = []
            if first is not None and first > 240:
                bits.append(f"late_start={first:.0f}s")
            if dur and last is not None:
                bits.append(f"tail_gap={dur - last:.0f}s")
            if "YTS" in video.name.upper() or "downloaded from" in text[:500].lower():
                bits.append("yts/release junk")
            flagged.append((rel, "; ".join(bits) or "heuristic"))
        else:
            ok += 1

    for season_dir, srts in season_srts.items():
        by_hash: dict[str, list[Path]] = defaultdict(list)
        for srt in srts:
            digest = hashlib.sha256(srt.read_bytes()).hexdigest()
            by_hash[digest].append(srt)
        for paths in by_hash.values():
            if len(paths) < 2:
                continue
            names = ", ".join(p.name for p in sorted(paths)[:4])
            if len(paths) > 4:
                names += f", +{len(paths) - 4} more"
            try:
                rel_season = season_dir.relative_to(root)
            except ValueError:
                rel_season = season_dir
            dup_content.append((str(rel_season), names))

    total = ok + len(missing) + len(flagged)
    print(
        f"audit-subtitle-sidecars: total_videos={total} ok={ok} "
        f"missing={len(missing)} flagged={len(flagged)} dup_season={len(dup_content)}"
    )
    for rel in missing:
        print(f"MISSING\t{rel}")
    for rel, why in flagged:
        print(f"FLAG\t{rel}\t{why}")
    for rel, names in dup_content:
        print(f"DUP\t{rel}\t{names}")
    return 1 if missing or dup_content else 0


if __name__ == "__main__":
    sys.exit(main())
