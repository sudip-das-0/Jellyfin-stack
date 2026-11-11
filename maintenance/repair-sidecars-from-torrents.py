#!/usr/bin/env python3
"""
Fix or create {stem}.eng.srt from matching files under /mnt/jellyfin/torrents.

Repairs missing sidecars, wrong duplicates (same hash as another episode in the season),
and sidecars whose dialogue no longer matches the torrent release file (e.g. episode 1
text copied to every episode).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "lib"))

from arr_common import MEDIA_ROOT, TORRENTS_ROOT  # noqa: E402
import sidecar_release as sr  # noqa: E402

mlp = sr.mlp

VIDEO_SUFFIXES = {".mkv", ".mp4"}
TORRENT_ROOT = TORRENTS_ROOT
MEDIA_ROOTS = (
    MEDIA_ROOT / "series",
    MEDIA_ROOT / "anime",
    MEDIA_ROOT / "movies",
    MEDIA_ROOT / "other",
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dialogue_lines(text: str) -> list[str]:
    lines: list[str] = []
    for ln in text.splitlines():
        if "-->" in ln or ln.strip().isdigit():
            continue
        stripped = ln.strip()
        if stripped:
            lines.append(stripped)
    return lines


def dialogue_match_ratio(a: list[str], b: list[str]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    hits = sum(1 for i in range(n) if a[i] == b[i])
    return hits / n


def read_sub_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")


def sidecar_alass_backup_path(sidecar: Path) -> Path:
    """Legacy alass backup (video.eng.srt -> video.eng.eng.srt.bak)."""
    name = sidecar.name
    if name.endswith(".eng.srt"):
        base = name[: -len(".eng.srt")]
        return sidecar.parent / f"{base}.eng.eng.srt.bak"
    return sidecar.with_suffix(f"{sidecar.suffix}.bak")


def restore_from_alass_backup(
    video: Path,
    sidecar: Path,
    *,
    dry_run: bool,
) -> bool:
    backup = sidecar_alass_backup_path(sidecar)
    if not backup.is_file():
        return False
    if (
        sidecar_shares_dialogue_with_sibling(sidecar, video.parent)
        and file_hash(sidecar) != file_hash(backup)
    ):
        if dry_run:
            print(
                f"DRY\t{sidecar.relative_to(MEDIA_ROOT)}\t<- backup {backup.name} (dup cluster)"
            )
            return True
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, sidecar)
        print(f"OK\t{sidecar.relative_to(MEDIA_ROOT)} <- backup {backup.name} (dup cluster)")
        return True
    first = season_first_episode_sidecar(video.parent)
    side_lines = dialogue_lines(read_sub_text(sidecar))
    bak_lines = dialogue_lines(read_sub_text(backup))
    if (
        dialogue_match_ratio(side_lines, bak_lines) >= 0.98
        and file_hash(sidecar) == file_hash(backup)
    ):
        return False
    if (
        dialogue_match_ratio(side_lines, bak_lines) >= 0.98
        and not sidecar_shares_dialogue_with_sibling(sidecar, video.parent)
    ):
        return False
    if first and first.is_file() and first != sidecar:
        first_lines = dialogue_lines(read_sub_text(first))
        ep = sr.episode_code_label(video.stem)
        first_ep = sr.episode_code_label(first.name)
        if ep and first_ep and ep != first_ep:
            if dialogue_match_ratio(bak_lines, first_lines) >= 0.95:
                return False
            if dialogue_match_ratio(side_lines, first_lines) < 0.95:
                return False
    if dry_run:
        print(f"DRY\t{sidecar.relative_to(MEDIA_ROOT)}\t<- backup {backup.name}")
        return True
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, sidecar)
    print(f"OK\t{sidecar.relative_to(MEDIA_ROOT)} <- backup {backup.name}")
    return True


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


def sidecar_shares_dialogue_with_sibling(sidecar: Path, season_dir: Path) -> bool:
    """True when another episode in the same season has >=95% identical subtitle text."""
    if not sidecar.is_file():
        return False
    mine = dialogue_lines(read_sub_text(sidecar))
    if len(mine) < 50:
        return False
    my_ep = sr.episode_code_label(sidecar.name)
    for other in season_dir.glob("*.eng.srt"):
        if other == sidecar:
            continue
        other_ep = sr.episode_code_label(other.name)
        if my_ep and other_ep and my_ep == other_ep:
            continue
        theirs = dialogue_lines(read_sub_text(other))
        if dialogue_match_ratio(mine, theirs) >= 0.95:
            return True
    return False


def season_first_episode_sidecar(season_dir: Path) -> Path | None:
    candidates: list[tuple[str, Path]] = []
    for srt in season_dir.glob("*.eng.srt"):
        m = re.search(r"(?i)s(\d+)e(\d+)", srt.name)
        if m:
            candidates.append((f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}", srt))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def needs_repair(
    video: Path,
    sidecar: Path,
    bad_hashes: set[str],
    torrent_source: Path | None,
    *,
    force: bool,
) -> bool:
    if not sidecar.is_file():
        return True
    if file_hash(sidecar) in bad_hashes:
        return True
    if force and torrent_source is not None:
        return True

    side_lines = dialogue_lines(read_sub_text(sidecar))
    if torrent_source is not None and torrent_source.is_file():
        tor_lines = dialogue_lines(read_sub_text(torrent_source))
        if dialogue_match_ratio(side_lines, tor_lines) < 0.85:
            return True

    first = season_first_episode_sidecar(video.parent)
    if first and first != sidecar and first.is_file():
        ep = sr.episode_code_label(video.stem)
        first_ep = sr.episode_code_label(first.name)
        if ep and first_ep and ep != first_ep:
            first_lines = dialogue_lines(read_sub_text(first))
            if (
                len(first_lines) >= 50
                and dialogue_match_ratio(side_lines, first_lines) >= 0.95
            ):
                return True
    if sidecar_shares_dialogue_with_sibling(sidecar, video.parent):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        default=[],
        help="Media root (repeatable). Default: series, anime, movies, other",
    )
    parser.add_argument("--torrent-root", type=Path, default=TORRENT_ROOT)
    parser.add_argument("--show", type=str, default="", help="Limit to one series folder name")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-copy from torrent whenever a matching release .srt exists",
    )
    args = parser.parse_args()

    roots = args.root or list(MEDIA_ROOTS)
    repaired = skipped = no_source = restored = 0
    for root in roots:
        if not root.is_dir():
            print(f"root not found: {root}", file=sys.stderr)
            continue
        for video in iter_videos(root):
            if args.show and args.show not in str(video):
                continue
            sidecar = mlp.canonical_sidecar_path(video)
            bad = season_duplicate_hashes(video.parent)
            source = sr.find_torrent_sidecar_for_video(video, args.torrent_root)
            if not needs_repair(video, sidecar, bad, source, force=args.force):
                skipped += 1
                continue
            if source:
                if args.dry_run:
                    print(f"DRY\t{video.relative_to(root)}\t<- {source}")
                    repaired += 1
                    continue
                sidecar.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, sidecar)
                print(f"OK\t{sidecar.relative_to(MEDIA_ROOT)} <- {source.name}")
                repaired += 1
                continue
            if restore_from_alass_backup(video, sidecar, dry_run=args.dry_run):
                restored += 1 if not args.dry_run else 0
                repaired += 1
                continue
            no_source += 1

    print(
        f"repair-sidecars-from-torrents: repaired={repaired} restored_from_backup={restored} "
        f"skipped={skipped} no_source={no_source}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
