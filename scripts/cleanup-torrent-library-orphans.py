#!/usr/bin/env python3
"""Remove torrent-folder copies when the title is already in /mnt/jellyfin/media.

Run via: ``cleanup-partial-series.py --torrent-library [--dry-run]`` (not on the maintenance timer).

Safe rules:
- Never touch /mnt/jellyfin/torrents/incomplete
- Skip paths tied to in-progress qBittorrent jobs
- Delete torrent-side hardlinks when the same inode exists under media/
- Delete orphan (nlink=1) downloads matched via Sonarr/Radarr scene names or SxxExx + library paths
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arr_common import (
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    STALL_LOG_DIR,
    append_recovery_log,
    arr_json,
    qbit_json,
    read_api_key,
    rotate_log_file,
)

TORRENTS = Path("/mnt/jellyfin/torrents")
MEDIA = Path("/mnt/jellyfin/media")
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".wmv", ".ts"}
LOG_FILE = STALL_LOG_DIR / "torrent-library-cleanup.log"

EP_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})")
NORM_RE = re.compile(r"[^a-z0-9]+")


def log(msg: str) -> None:
    STALL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    rotate_log_file(LOG_FILE)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg)


def host_torrent_path(raw: str) -> str:
    if raw.startswith("/data/torrents"):
        return "/mnt/jellyfin/torrents" + raw[len("/data/torrents") :]
    if raw.startswith("/mnt/jellyfin/torrents"):
        return raw
    return raw


def norm(s: str) -> str:
    return NORM_RE.sub("", (s or "").lower())


def map_docker_media(path: str) -> Path:
    if path.startswith("/data/media/"):
        return Path("/mnt/jellyfin/media") / path[len("/data/media/") :]
    if path.startswith("/mnt/jellyfin/media/"):
        return Path(path)
    return Path(path)


def qbit_protected_roots() -> set[Path]:
    protected: set[Path] = set()
    try:
        torrents = qbit_json("/api/v2/torrents/info")
    except urllib.error.URLError as e:
        log(f"warning: qBittorrent unavailable, skipping in-progress protection: {e}")
        return protected
    if not isinstance(torrents, list):
        return protected
    incomplete_states = {
        "downloading",
        "metaDL",
        "stalledDL",
        "queuedDL",
        "checkingDL",
        "pausedDL",
        "allocating",
    }
    for t in torrents:
        progress = float(t.get("progress") or 0)
        state = t.get("state") or ""
        # Keep files for jobs still grabbing data (including queued/not started).
        if progress >= 0.999 and state not in incomplete_states:
            continue
        content = t.get("content_path") or ""
        if not content:
            save = t.get("save_path") or "/data/torrents"
            content = os.path.join(save, t.get("name") or "")
        p = Path(host_torrent_path(content))
        protected.add(p)
    return protected


def is_protected(path: Path, protected: set[Path]) -> bool:
    for root in protected:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            if root == path:
                return True
    return False


def media_paths_by_inode() -> dict[int, list[Path]]:
    by_inode: dict[int, list[Path]] = {}
    for root, _, files in os.walk(MEDIA):
        for name in files:
            if Path(name).suffix.lower() not in VIDEO_EXT:
                continue
            p = Path(root) / name
            try:
                ino = p.stat().st_ino
            except OSError:
                continue
            by_inode.setdefault(ino, []).append(p)
    return by_inode


def load_arr_matchers() -> set[str]:
    """Scene/path tokens for titles with files on disk in media/."""
    scene_tokens: set[str] = set()

    try:
        movies = arr_json(f"{RADARR_URL}/api/v3/movie", read_api_key(RADARR_CONFIG))
        for m in movies or []:
            mf = m.get("movieFile") or {}
            for key in ("sceneName", "originalFilePath", "relativePath"):
                val = mf.get(key) or ""
                if val:
                    scene_tokens.add(norm(val))
                    scene_tokens.add(norm(Path(val).name))
                    scene_tokens.add(norm(Path(val).stem))
            mf_path = mf.get("path") or ""
            if mf_path and map_docker_media(mf_path).is_file():
                scene_tokens.add(norm(m.get("title") or ""))
    except (urllib.error.URLError, OSError) as e:
        log(f"warning: Radarr match data partial: {e}")

    try:
        sonarr_key = read_api_key(SONARR_CONFIG)
        series_list = arr_json(f"{SONARR_URL}/api/v3/series", sonarr_key)
        for series in series_list or []:
            sid = series.get("id")
            if sid is None:
                continue
            scene_tokens.add(norm(series.get("title") or ""))
            try:
                files = arr_json(
                    f"{SONARR_URL}/api/v3/episodefile?seriesId={int(sid)}",
                    sonarr_key,
                )
            except urllib.error.HTTPError:
                continue
            if not isinstance(files, list):
                continue
            for ef in files:
                path = ef.get("path") or ""
                if path and not map_docker_media(path).is_file():
                    continue
                for key in ("sceneName", "relativePath"):
                    val = ef.get(key) or ""
                    if val:
                        scene_tokens.add(norm(val))
                        scene_tokens.add(norm(Path(val).name))
                        scene_tokens.add(norm(Path(val).stem))
    except (urllib.error.URLError, OSError) as e:
        log(f"warning: Sonarr match data partial: {e}")

    scene_tokens.discard("")
    return scene_tokens


def path_matches_arr(path: Path, scene_tokens: set[str], media_videos: list[Path]) -> bool:
    blob = norm(str(path))
    for tok in scene_tokens:
        if len(tok) < 8:
            continue
        if tok in blob:
            return True
    m = EP_RE.search(path.name)
    if not m:
        return False
    season, ep = int(m.group(1)), int(m.group(2))
    show_guess = norm(path.parent.name)
    if len(show_guess) < 4:
        return False
    for mv in media_videos:
        if not EP_RE.search(mv.name):
            continue
        m2 = EP_RE.search(mv.name)
        if not m2:
            continue
        if int(m2.group(1)) != season or int(m2.group(2)) != ep:
            continue
        if show_guess[:12] in norm(mv.parent.as_posix()) or show_guess[:12] in norm(
            mv.as_posix()
        ):
            return True
    return False


def collect_media_videos() -> list[Path]:
    out: list[Path] = []
    for root, _, files in os.walk(MEDIA):
        for name in files:
            if Path(name).suffix.lower() in VIDEO_EXT:
                out.append(Path(root) / name)
    return out


def prune_empty_dirs(start: Path) -> None:
    if not str(start).startswith(str(TORRENTS)):
        return
    cur = start
    while cur != TORRENTS and cur.is_dir():
        if any(cur.iterdir()):
            break
        cur.rmdir()
        log(f"removed empty dir: {cur}")
        cur = cur.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions only; do not delete files",
    )
    args = parser.parse_args()

    if not TORRENTS.is_dir():
        log(f"torrents root missing: {TORRENTS}")
        return 1

    protected = qbit_protected_roots()
    log(f"qBittorrent protected roots (in-progress): {len(protected)}")

    media_by_inode = media_paths_by_inode()
    scene_tokens = load_arr_matchers()
    media_videos = collect_media_videos()
    log(f"media video files: {len(media_videos)}; scene tokens: {len(scene_tokens)}")

    to_delete: list[Path] = []
    skipped_protected = skipped_nomatch = 0

    for root, _, files in os.walk(TORRENTS):
        root_path = Path(root)
        if "incomplete" in root_path.parts:
            continue
        for name in files:
            p = root_path / name
            if p.suffix.lower() not in VIDEO_EXT:
                continue
            if is_protected(p, protected):
                skipped_protected += 1
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            ino = st.st_ino
            media_links = [
                mp for mp in media_by_inode.get(ino, []) if str(mp).startswith(str(MEDIA))
            ]
            if media_links:
                to_delete.append(p)
                continue
            if path_matches_arr(p, scene_tokens, media_videos):
                to_delete.append(p)
            else:
                skipped_nomatch += 1

    total_bytes = sum(p.stat().st_size for p in to_delete if p.is_file())
    log(
        f"{'DRY-RUN: ' if args.dry_run else ''}delete {len(to_delete)} file(s), "
        f"~{total_bytes / 1e9:.1f} GB; protected={skipped_protected} no_match={skipped_nomatch}"
    )

    deleted = 0
    parent_dirs: set[Path] = set()
    for p in to_delete:
        if args.dry_run:
            log(f"would delete: {p}")
            continue
        try:
            sz = p.stat().st_size
            p.unlink()
            deleted += 1
            parent_dirs.add(p.parent)
            log(f"deleted: {p} ({sz / 1e9:.2f} GB)")
        except OSError as e:
            log(f"failed: {p}: {e}")

    if not args.dry_run:
        for d in sorted(parent_dirs, key=lambda x: len(x.parts), reverse=True):
            prune_empty_dirs(d)
        # Remove leftover empty top-level release folders (non-video debris)
        for entry in list(TORRENTS.iterdir()):
            if entry.name == "incomplete" or is_protected(entry, protected):
                continue
            if entry.is_dir() and not any(entry.rglob("*")):
                if args.dry_run:
                    continue
                try:
                    entry.rmdir()
                    log(f"removed empty release dir: {entry.name[:80]}")
                except OSError:
                    pass
        append_recovery_log(
            f"torrent-library-cleanup: deleted {deleted} files (~{total_bytes / 1e9:.1f} GB)",
            tag="qbit",
        )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        log(f"API error: {e}")
        sys.exit(1)
