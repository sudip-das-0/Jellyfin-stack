#!/usr/bin/env python3
"""Remove torrent-folder copies when the title is already in /mnt/jellyfin/media.

Run via: ``cleanup-partial-series.py --torrent-library [--dry-run]`` (not on the maintenance timer).

Safe rules:
- Never touch /mnt/jellyfin/torrents/incomplete
- Never delete files under active qBittorrent jobs (even when the episode already exists in the library)
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from arr_common import (
    MEDIA_ROOT,
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    STALL_LOG_DIR,
    TORRENTS_ROOT,
    VIDEO_EXTENSIONS,
    append_recovery_log,
    arr_json,
    echo_script_log,
    qbit_delete,
    qbit_json,
    qbit_unfinished_content_roots,
    read_api_key,
    torrent_content_root,
)
from orphan_import_helpers import resolve_episode_from_filename

TORRENTS = TORRENTS_ROOT
MEDIA = MEDIA_ROOT
VIDEO_EXT = VIDEO_EXTENSIONS
LOG_FILE = STALL_LOG_DIR / "torrent-library-cleanup.log"

EP_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})")
NORM_RE = re.compile(r"[^a-z0-9]+")


def log(msg: str) -> None:
    echo_script_log(LOG_FILE, msg, rotate=True)


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def norm(s: str) -> str:
    return NORM_RE.sub("", (s or "").lower())


def map_docker_media(path: str) -> Path:
    media = str(MEDIA)
    if path.startswith("/data/media/"):
        return MEDIA / path[len("/data/media/") :]
    if path.startswith(media + "/"):
        return Path(path)
    return Path(path)


def qbit_content_index() -> list[tuple[Path, str]]:
    """All torrent content roots → info-hash (for removing client jobs on duplicates)."""
    index: list[tuple[Path, str]] = []
    torrents = qbit_json("/api/v2/torrents/info")
    if not isinstance(torrents, list):
        raise OSError("unexpected qBittorrent /torrents/info response")
    for t in torrents:
        h = t.get("hash")
        if h:
            index.append((torrent_content_root(t), str(h)))
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


def scan_media() -> tuple[dict[int, list[Path]], list[Path]]:
    """One walk of MEDIA -> (inode -> paths, all video paths)."""
    by_inode: dict[int, list[Path]] = {}
    videos: list[Path] = []
    for root, _, files in os.walk(MEDIA):
        for name in files:
            if Path(name).suffix.lower() not in VIDEO_EXT:
                continue
            p = Path(root) / name
            videos.append(p)
            try:
                ino = p.stat().st_ino
            except OSError:
                continue
            by_inode.setdefault(ino, []).append(p)
    return by_inode, videos


EpisodeIndex = dict[tuple[int, int], list[tuple[Path, str]]]


def build_episode_index(media_videos: list[Path]) -> EpisodeIndex:
    """(season, episode) -> [(path, normalized posix path)] so lookups are not O(library)."""
    index: EpisodeIndex = {}
    for mv in media_videos:
        m = EP_RE.search(mv.name)
        if m:
            index.setdefault((int(m.group(1)), int(m.group(2))), []).append(
                (mv, norm(mv.as_posix()))
            )
    return index


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
            if mf_path and map_docker_media(mf_path).is_file() and m.get("year"):
                # Title alone also matches sequels ("The Matrix" vs "The Matrix Reloaded");
                # title+year is specific to the library copy.
                scene_tokens.add(norm(f"{m.get('title') or ''}{m['year']}"))
    except (urllib.error.URLError, OSError) as e:
        log(f"warning: Radarr match data partial: {e}")

    try:
        sonarr_key = read_api_key(SONARR_CONFIG)
        series_list = arr_json(f"{SONARR_URL}/api/v3/series", sonarr_key)
        for series in series_list or []:
            sid = series.get("id")
            if sid is None:
                continue
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


def episode_duplicate_in_library(path: Path, ep_index: EpisodeIndex) -> bool:
    """Same SxxExx already in media/ with a matching show token in the torrent name."""
    m = EP_RE.search(path.name)
    if not m:
        return False
    candidates = ep_index.get((int(m.group(1)), int(m.group(2))), [])
    if not candidates:
        return False
    tnorm = norm(path.name)
    show_tokens = [t for t in show_name_tokens(path) if len(t) >= 5]
    for mv, mv_norm in candidates:
        if any(tok in mv_norm for tok in show_tokens):
            return True
        try:
            rel = mv.relative_to(MEDIA)
        except ValueError:
            continue
        if len(rel.parts) < 2:
            continue
        series_norm = norm(rel.parts[1])
        if any(len(chunk) >= 5 and chunk in tnorm for chunk in re.split(r"[^a-z0-9]+", series_norm)):
            return True
    return False


def path_matches_arr(path: Path, scene_tokens: set[str], ep_index: EpisodeIndex) -> bool:
    m_ep = EP_RE.search(path.name)
    if m_ep:
        # TV episodes: scene/release tokens alone are not enough (series may be partial);
        # the same SxxExx must already exist in the library.
        if episode_duplicate_in_library(path, ep_index):
            return True
        show_guess = norm(path.parent.name)
        if len(show_guess) < 4:
            return False
        key = (int(m_ep.group(1)), int(m_ep.group(2)))
        return any(show_guess[:12] in mv_norm for _, mv_norm in ep_index.get(key, []))
    blob = norm(str(path))
    return any(len(tok) >= 8 and tok in blob for tok in scene_tokens)


def is_library_duplicate(
    path: Path,
    *,
    media_by_inode: dict[int, list[Path]],
    scene_tokens: set[str],
    ep_index: EpisodeIndex,
    sonarr_key: str,
    cache: dict,
) -> tuple[bool, bool]:
    """Returns (is_duplicate, is_hardlink_to_media)."""
    try:
        st = path.stat()
    except OSError:
        return False, False
    if any(_under(mp, MEDIA) for mp in media_by_inode.get(st.st_ino, [])):
        return True, st.st_nlink > 1
    if path_matches_arr(path, scene_tokens, ep_index):
        return True, False
    ep = resolve_episode_from_filename(path.name, sonarr_key, cache)
    if ep and ep.get("hasFile"):
        return True, False
    return False, False


def prune_empty_dirs(start: Path) -> None:
    if not _under(start, TORRENTS):
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

    try:
        protected = qbit_unfinished_content_roots()
        qbit_index = qbit_content_index()
    except OSError as e:
        # Without qBittorrent we cannot tell which files are still downloading: fail closed.
        log(f"qBittorrent unavailable ({e}): in-progress files cannot be protected")
        if not args.dry_run:
            log("refusing to delete anything; re-run when qBittorrent is up")
            return 1
        protected, qbit_index = set(), []
    log(f"qBittorrent protected roots (in-progress): {len(protected)}")

    media_by_inode, media_videos = scan_media()
    ep_index = build_episode_index(media_videos)
    scene_tokens = load_arr_matchers()
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
                ep_index=ep_index,
                sonarr_key=sonarr_key,
                cache=sonarr_cache,
            )
            if is_protected(p, protected):
                skipped_protected += 1
                continue
            if dup:
                to_delete.append((p, hardlink))
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
