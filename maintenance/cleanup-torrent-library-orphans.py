#!/usr/bin/env python3
"""Remove torrent-folder copies when the title is already in /mnt/jellyfin/media.

Run via: ``cleanup-partial-series.py --torrent-library [--dry-run]`` (not on the maintenance timer).

Safe rules:
- Never touch /mnt/jellyfin/torrents/incomplete
- Skip in-progress qBittorrent jobs unless Sonarr/Radarr already has the episode/movie on disk
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

sys.path.insert(0, "/mnt/jellyfin/scripts/lib")
from arr_common import (
    MEDIA_ROOT,
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    STALL_LOG_DIR,
    TORRENTS_ROOT,
    append_recovery_log,
    arr_json,
    echo_script_log,
    qbit_delete,
    qbit_json,
    read_api_key,
)
from orphan_import_helpers import resolve_episode_from_filename

TORRENTS = TORRENTS_ROOT
MEDIA = MEDIA_ROOT
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".wmv", ".ts", ".m2ts"}
LOG_FILE = STALL_LOG_DIR / "torrent-library-cleanup.log"

EP_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})")
NORM_RE = re.compile(r"[^a-z0-9]+")


def log(msg: str) -> None:
    echo_script_log(LOG_FILE, msg, rotate=True)


def host_torrent_path(raw: str) -> str:
    tor = str(TORRENTS)
    if raw.startswith("/data/torrents"):
        return tor + raw[len("/data/torrents") :]
    if raw.startswith(tor):
        return raw
    return raw


def norm(s: str) -> str:
    return NORM_RE.sub("", (s or "").lower())


def map_docker_media(path: str) -> Path:
    media = str(MEDIA)
    if path.startswith("/data/media/"):
        return MEDIA / path[len("/data/media/") :]
    if path.startswith(media + "/"):
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


def qbit_content_index() -> list[tuple[Path, str]]:
    """All torrent content roots → info-hash (for removing client jobs on duplicates)."""
    index: list[tuple[Path, str]] = []
    try:
        torrents = qbit_json("/api/v2/torrents/info")
    except urllib.error.URLError:
        return index
    if not isinstance(torrents, list):
        return index
    for t in torrents:
        h = t.get("hash")
        if not h:
            continue
        content = t.get("content_path") or ""
        if not content:
            save = t.get("save_path") or "/data/torrents"
            content = os.path.join(save, t.get("name") or "")
        index.append((Path(host_torrent_path(content)), str(h)))
    return index


def torrent_hash_for_path(path: Path, index: list[tuple[Path, str]]) -> str | None:
    path = path.resolve()
    best_hash: str | None = None
    best_depth = -1
    for root, thash in index:
        root = root.resolve()
        try:
            path.relative_to(root)
            depth = len(root.parts)
        except ValueError:
            if path != root:
                continue
            depth = len(root.parts)
        if depth >= best_depth:
            best_depth = depth
            best_hash = thash
    return best_hash


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


def show_name_tokens(path: Path) -> list[str]:
    """Tokens for matching loose files saved directly under torrents/."""
    tokens: list[str] = []
    parent = path.parent.name
    if parent not in ("torrents", ".") and len(parent) >= 4:
        tokens.append(norm(parent))
    m = EP_RE.search(path.name)
    if m:
        prefix = path.name[: m.start()].strip(" .-_[]")
        if len(prefix) >= 4:
            tokens.append(norm(prefix))
        for part in re.split(r"[.\s_\[\]-]+", prefix):
            part = part.strip()
            if len(part) >= 5:
                tokens.append(norm(part))
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def episode_duplicate_in_library(path: Path, media_videos: list[Path]) -> bool:
    """Same SxxExx already in media/ with a matching show token in the torrent name."""
    m = EP_RE.search(path.name)
    if not m:
        return False
    season, ep = int(m.group(1)), int(m.group(2))
    tnorm = norm(path.name)
    show_tokens = show_name_tokens(path)
    for mv in media_videos:
        m2 = EP_RE.search(mv.name)
        if not m2:
            continue
        if int(m2.group(1)) != season or int(m2.group(2)) != ep:
            continue
        mv_norm = norm(mv.as_posix())
        for tok in show_tokens:
            if len(tok) >= 5 and tok in mv_norm:
                return True
        try:
            rel = mv.relative_to(MEDIA)
        except ValueError:
            continue
        if len(rel.parts) < 2:
            continue
        series_norm = norm(rel.parts[1])
        for chunk in re.split(r"[^a-z0-9]+", series_norm):
            if len(chunk) >= 5 and chunk in tnorm:
                return True
    return False


def path_matches_arr(path: Path, scene_tokens: set[str], media_videos: list[Path]) -> bool:
    blob = norm(str(path))
    m_ep = EP_RE.search(path.name)
    for tok in scene_tokens:
        if len(tok) < 8:
            continue
        if tok in blob:
            # TV episodes: scene/release tokens alone are not enough (series may be partial).
            if m_ep:
                if episode_duplicate_in_library(path, media_videos):
                    return True
            else:
                return True
    if episode_duplicate_in_library(path, media_videos):
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


def is_library_duplicate(
    path: Path,
    *,
    media_by_inode: dict[int, list[Path]],
    scene_tokens: set[str],
    media_videos: list[Path],
    sonarr_key: str,
    cache: dict,
) -> tuple[bool, bool]:
    """Returns (is_duplicate, is_hardlink_to_media)."""
    try:
        ino = path.stat().st_ino
        nlink = path.stat().st_nlink
    except OSError:
        return False, False
    media_links = [mp for mp in media_by_inode.get(ino, []) if str(mp).startswith(str(MEDIA))]
    if media_links:
        return True, nlink > 1
    if path_matches_arr(path, scene_tokens, media_videos):
        return True, False
    ep = resolve_episode_from_filename(path.name, sonarr_key, cache)
    if ep and ep.get("hasFile"):
        return True, False
    return False, False


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


def dir_has_video(root: Path) -> bool:
    for r, _, files in os.walk(root):
        for name in files:
            if Path(name).suffix.lower() in VIDEO_EXT:
                return True
    return False


def prune_junk_release_dirs(protected: set[Path], dry_run: bool) -> int:
    """Remove top-level torrent release folders with no video (subs/jpg/nfo debris)."""
    skip_names = {"incomplete", ".reconcile-staging"}
    removed = 0
    for entry in sorted(TORRENTS.iterdir()):
        if not entry.is_dir() or entry.name in skip_names:
            continue
        if is_protected(entry, protected):
            continue
        if dir_has_video(entry):
            continue
        if dry_run:
            log(f"would remove junk release dir: {entry}")
            removed += 1
            continue
        try:
            shutil.rmtree(entry)
            removed += 1
            log(f"removed junk release dir: {entry.name[:80]}")
        except OSError as e:
            log(f"failed junk dir {entry.name[:60]}: {e}")
    return removed


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
    qbit_index = qbit_content_index()
    log(f"qBittorrent protected roots (in-progress): {len(protected)}")

    media_by_inode = media_paths_by_inode()
    scene_tokens = load_arr_matchers()
    media_videos = collect_media_videos()
    sonarr_key = read_api_key(SONARR_CONFIG)
    sonarr_cache: dict = {}
    log(f"media video files: {len(media_videos)}; scene tokens: {len(scene_tokens)}")

    to_delete: list[tuple[Path, bool]] = []
    skipped_protected = skipped_nomatch = 0

    for root, _, files in os.walk(TORRENTS):
        root_path = Path(root)
        if "incomplete" in root_path.parts:
            continue
        for name in files:
            p = root_path / name
            if p.suffix.lower() not in VIDEO_EXT:
                continue
            dup, hardlink = is_library_duplicate(
                p,
                media_by_inode=media_by_inode,
                scene_tokens=scene_tokens,
                media_videos=media_videos,
                sonarr_key=sonarr_key,
                cache=sonarr_cache,
            )
            if dup:
                to_delete.append((p, hardlink))
                continue
            if is_protected(p, protected):
                skipped_protected += 1
                continue
            skipped_nomatch += 1

    total_bytes = sum(p.stat().st_size for p, _ in to_delete if p.is_file())
    log(
        f"{'DRY-RUN: ' if args.dry_run else ''}delete {len(to_delete)} duplicate file(s), "
        f"~{total_bytes / 1e9:.1f} GB; protected_non_dup={skipped_protected} "
        f"no_match={skipped_nomatch}"
    )

    deleted = 0
    parent_dirs: set[Path] = set()
    qbit_remove_files: set[str] = set()
    qbit_drop_only: set[str] = set()

    for p, hardlink in to_delete:
        if args.dry_run:
            tag = "hardlink" if hardlink else "copy"
            log(f"would delete ({tag}): {p}")
            th = torrent_hash_for_path(p, qbit_index)
            if th:
                log(f"  qBittorrent hash: {th[:8]}…")
            continue
        th = torrent_hash_for_path(p, qbit_index)
        if hardlink:
            if th:
                qbit_drop_only.add(th)
            try:
                p.unlink()
                deleted += 1
                parent_dirs.add(p.parent)
                log(f"unlinked hardlink duplicate: {p}")
            except OSError as e:
                log(f"failed hardlink unlink {p}: {e}")
        elif th:
            qbit_remove_files.add(th)
        else:
            try:
                sz = p.stat().st_size
                p.unlink()
                deleted += 1
                parent_dirs.add(p.parent)
                log(f"deleted orphan copy: {p} ({sz / 1e9:.2f} GB)")
            except OSError as e:
                log(f"failed: {p}: {e}")

    if not args.dry_run:
        drop_only = [h for h in qbit_drop_only if h not in qbit_remove_files]
        if qbit_remove_files:
            qbit_delete(sorted(qbit_remove_files), delete_files=True)
            log(f"removed {len(qbit_remove_files)} torrent job(s) with files (library duplicate)")
        if drop_only:
            qbit_delete(drop_only, delete_files=False)
            log(f"removed {len(drop_only)} torrent job(s) after hardlink unlink")

    junk_dirs = 0
    if not args.dry_run:
        for d in sorted(parent_dirs, key=lambda x: len(x.parts), reverse=True):
            prune_empty_dirs(d)
        for entry in list(TORRENTS.iterdir()):
            if entry.name in ("incomplete", ".reconcile-staging") or is_protected(
                entry, protected
            ):
                continue
            if entry.is_dir() and not any(entry.rglob("*")):
                try:
                    entry.rmdir()
                    log(f"removed empty release dir: {entry.name[:80]}")
                except OSError:
                    pass
        junk_dirs = prune_junk_release_dirs(protected, dry_run=False)
        append_recovery_log(
            f"torrent-library-cleanup: deleted {deleted} files (~{total_bytes / 1e9:.1f} GB), "
            f"junk_dirs={junk_dirs}",
            tag="qbit",
        )
    else:
        junk_dirs = prune_junk_release_dirs(protected, dry_run=True)
        log(f"DRY-RUN: would remove {junk_dirs} junk release dir(s)")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        log(f"API error: {e}")
        sys.exit(1)
