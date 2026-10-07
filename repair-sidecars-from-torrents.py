#!/usr/bin/env python3
"""
Fix or create {stem}.eng.srt from matching files under /mnt/jellyfin/torrents.

Repairs missing sidecars and wrong duplicates (same hash as another episode in the season).
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import sidecar_release as sr  # noqa: E402

mlp = sr.mlp

VIDEO_SUFFIXES = {".mkv", ".mp4"}
TORRENT_ROOT = Path("/mnt/jellyfin/torrents")
MEDIA_ROOT = Path("/mnt/jellyfin/media")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iter_videos(root: Path):
    for path in root.rglob("*"):
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        if ".lgopt.tmp" in path.name or path.name.endswith(".lgstream.mkv"):
            continue
        yield path


def season_duplicate_hashes(season_dir: Path) -> set[str]:
    by_hash: dict[str, list[Path]] = {}
    for srt in season_dir.glob("*.eng.srt"):
        by_hash.setdefault(file_hash(srt), []).append(srt)
    bad: set[str] = set()
    for paths in by_hash.values():
        if len(paths) > 1:
            bad.add(file_hash(paths[0]))
    return bad


def needs_repair(video: Path, sidecar: Path, bad_hashes: set[str]) -> bool:
    if not sidecar.is_file():
        return True
    return file_hash(sidecar) in bad_hashes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=MEDIA_ROOT / "series")
    parser.add_argument("--torrent-root", type=Path, default=TORRENT_ROOT)
    parser.add_argument("--show", type=str, default="", help="Limit to one series folder name")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = args.root
    if not root.is_dir():
        print(f"root not found: {root}", file=sys.stderr)
        return 1

    repaired = skipped = no_source = 0
    for video in iter_videos(root):
        if args.show and args.show not in str(video):
            continue
        sidecar = mlp.canonical_sidecar_path(video)
        bad = season_duplicate_hashes(video.parent)
        if not needs_repair(video, sidecar, bad):
            skipped += 1
            continue
        source = sr.find_torrent_sidecar_for_video(video, args.torrent_root)
        if not source:
            no_source += 1
            continue
        if args.dry_run:
            print(f"DRY\t{video.relative_to(root)}\t<- {source}")
            repaired += 1
            continue
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, sidecar)
        print(f"OK\t{sidecar.relative_to(root.parent)} <- {source.name}")
        repaired += 1

    print(
        f"repair-sidecars-from-torrents: repaired={repaired} skipped={skipped} no_source={no_source}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
