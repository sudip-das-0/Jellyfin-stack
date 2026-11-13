#!/usr/bin/env python3
"""One English sidecar per video: {stem}.eng.srt (media-lang-policy.py)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "lib"))
from arr_common import JELLYFIN_MNT, MEDIA_ROOT, load_media_lang_policy  # noqa: E402

VIDEO_SUFFIXES = {".mkv", ".mp4"}


def default_media_root() -> Path:
    for candidate in (MEDIA_ROOT, Path("/data/media")):
        if candidate.is_dir():
            return candidate
    return MEDIA_ROOT


def resolve_video(path: Path) -> Path | None:
    if path.is_file():
        return path
    s = str(path)
    host_prefix = f"{JELLYFIN_MNT}/"
    if s.startswith("/data/media/"):
        alt = JELLYFIN_MNT / Path(s[len("/data/") :])
        if alt.is_file():
            return alt
    if s.startswith(str(MEDIA_ROOT) + "/"):
        alt = Path("/data") / Path(s[len(host_prefix) :])
        if alt.is_file():
            return alt
    return None


def load_policy():
    return load_media_lang_policy()


def iter_videos(root: Path):
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        if ".lgopt.tmp" in path.name or path.name.endswith(".lgstream.mkv"):
            continue
        yield path


def sidecars_for_video(video: Path) -> list[Path]:
    prefix = f"{video.stem}."
    return sorted(
        p
        for p in video.parent.iterdir()
        if p.is_file() and p.suffix.lower() == ".srt" and p.name.startswith(prefix)
    )


def normalize_video(video: Path, mlp, *, dry_run: bool) -> tuple[int, int, int]:
    """Returns (created_or_updated, removed, skipped)."""
    canonical = mlp.canonical_sidecar_path(video)
    related = sidecars_for_video(video)
    english = [p for p in related if mlp.is_english_sidecar_variant(video.stem, p)]
    removed = 0

    if canonical.is_file() and canonical not in english:
        english.insert(0, canonical)

    created = 0
    if not canonical.is_file() and english:
        english.sort(key=lambda p: mlp.english_sidecar_pick_score(p.name))
        source = english[0]
        if not dry_run:
            if source != canonical:
                source.replace(canonical)
        created = 1

    for path in related:
        if path == canonical:
            continue
        if not dry_run:
            path.unlink(missing_ok=True)
        removed += 1

    if not canonical.is_file() and not english:
        return 0, removed, 1
    return created, removed, 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--video", type=Path, action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = args.root or default_media_root()
    mlp = load_policy()

    if args.video:
        videos: list[Path] = []
        for raw in args.video:
            resolved = resolve_video(Path(raw))
            if resolved:
                videos.append(resolved)
    else:
        videos = list(iter_videos(root))

    created = removed = skipped = 0
    for video in videos:
        c, r, s = normalize_video(video, mlp, dry_run=args.dry_run)
        created += c
        removed += r
        skipped += s

    if root.is_dir():
        for subs_dir in root.rglob("Subs"):
            if not subs_dir.is_dir():
                continue
            for srt in subs_dir.glob("*.srt"):
                if not args.dry_run:
                    srt.unlink(missing_ok=True)
                removed += 1

    mode = "dry-run" if args.dry_run else "applied"
    print(
        f"normalize-subtitle-sidecars ({mode}): "
        f"videos={len(videos)} canonicalized={created} removed={removed} no_subs={skipped}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
