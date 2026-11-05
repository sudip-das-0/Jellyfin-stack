#!/usr/bin/env python3
"""Unmonitor Sonarr/Radarr after media removed from disk (Jellyfin UI or manual).

Sonarr/Radarr keep monitoring missing titles and re-queue qBittorrent. This script
unmonitors them, clears queue rows, and drops matching torrents. Run automatically
via download-maintenance, Jellyfin Webhook (ItemDeleted), or Sonarr/Radarr file-delete hooks.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arr_common import (
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    STALL_LOG_DIR,
    arr_json,
    fetch_arr_queue,
    arr_queue_remove,
    qbit_delete,
    qbit_torrents,
    read_api_key,
)

MEDIA = Path("/mnt/jellyfin/media")
LOG = STALL_LOG_DIR / "sync-manual-deletes.log"
DELETE_QUEUE = STALL_LOG_DIR / "delete-sync.queue"
IMPORT_EVENTS = frozenset(
    {
        "downloadFolderImported",
        "downloadImported",
        "copyFolderImported",
        "episodeFileDeleted",
    }
)


def log(msg: str) -> None:
    STALL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg)


def arr_path_to_host(path: str) -> Path:
    for prefix in ("/data/media/", "/media/"):
        if path.startswith(prefix):
            return MEDIA / path[len(prefix) :]
    if path.startswith("/mnt/jellyfin/"):
        return Path(path)
    return MEDIA / path.lstrip("/")


def host_to_arr_path(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(MEDIA.resolve())
    except ValueError:
        return str(path)
    return f"/data/media/{rel.as_posix()}"


def normalize_input_path(raw: str) -> Path | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("/data/media/"):
        return arr_path_to_host(raw)
    if raw.startswith("/media/"):
        return MEDIA / raw[len("/media/") :]
    p = Path(raw)
    if p.is_absolute():
        return p
    return MEDIA / raw.lstrip("/")


def paths_under_media(raw_paths: list[str]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for raw in raw_paths:
        p = normalize_input_path(raw)
        if not p:
            continue
        try:
            p.resolve().relative_to(MEDIA.resolve())
        except ValueError:
            continue
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def episode_missing_on_disk(ep: dict, ef_path_by_episode: dict[int, str]) -> bool:
    eid = int(ep["id"])
    ef_path = ef_path_by_episode.get(eid)
    if ef_path:
        return not arr_path_to_host(ef_path).is_file()
    if ep.get("hasFile"):
        return True
    return not ep.get("hasFile")


def sonarr_imported_episode_ids(api_key: str) -> set[int]:
    imported: set[int] = set()
    page = 1
    while True:
        q = arr_json(
            f"{SONARR_URL}/api/v3/history?page={page}&pageSize=250&sortKey=date&sortDirection=descending",
            api_key,
        )
        records = (q or {}).get("records") if isinstance(q, dict) else []
        if not records:
            break
        for row in records:
            if row.get("eventType") in IMPORT_EVENTS and row.get("episodeId"):
                imported.add(int(row["episodeId"]))
        if page * q.get("pageSize", 250) >= q.get("totalRecords", 0):
            break
        page += 1
    return imported


def _sonarr_apply_unmonitor(
    api_key: str, to_unmonitor: list[int], affected_series: set[int], stale_files: int
) -> None:
    for i in range(0, len(to_unmonitor), 200):
        chunk = to_unmonitor[i : i + 200]
        arr_json(
            f"{SONARR_URL}/api/v3/episode/monitor",
            api_key,
            method="PUT",
            body={"episodeIds": chunk, "monitored": False},
        )
    log(
        f"sonarr: stale_episodefiles={stale_files} unmonitored_episodes={len(to_unmonitor)} "
        f"series_touched={len(affected_series)}"
    )


def sonarr_unmonitor_deleted(
    api_key: str, *, require_imported: bool = True
) -> tuple[int, set[int], list[int]]:
    imported = sonarr_imported_episode_ids(api_key)
    series_list = arr_json(f"{SONARR_URL}/api/v3/series", api_key)
    if not isinstance(series_list, list):
        return 0, set(), []

    to_unmonitor: list[int] = []
    affected_series: set[int] = set()
    stale_files = 0

    for series in series_list:
        sid = series["id"]
        episodes = arr_json(f"{SONARR_URL}/api/v3/episode?seriesId={sid}", api_key)
        if not isinstance(episodes, list):
            continue
        files = arr_json(f"{SONARR_URL}/api/v3/episodefile?seriesId={sid}", api_key)
        ef_path_by_episode: dict[int, str] = {}
        if isinstance(files, list):
            for ef in files:
                path = ef.get("path") or ""
                for eid in ef.get("episodeIds") or []:
                    ef_path_by_episode[int(eid)] = path
                if path and not arr_path_to_host(path).is_file():
                    try:
                        arr_json(
                            f"{SONARR_URL}/api/v3/episodefile/{ef['id']}",
                            api_key,
                            method="DELETE",
                        )
                        stale_files += 1
                        for eid in ef.get("episodeIds") or []:
                            ef_path_by_episode.pop(int(eid), None)
                    except OSError:
                        pass
        for ep in episodes:
            eid = int(ep["id"])
            if not ep.get("monitored"):
                continue
            if not episode_missing_on_disk(ep, ef_path_by_episode):
                continue
            if require_imported and eid not in imported:
                continue
            to_unmonitor.append(eid)
            affected_series.add(sid)

    _sonarr_apply_unmonitor(api_key, to_unmonitor, affected_series, stale_files)
    return len(to_unmonitor), affected_series, to_unmonitor


def _path_match_target(path: str, targets: set[str], dir_targets: list[Path]) -> bool:
    if not path:
        return False
    host = arr_path_to_host(path)
    if path in targets or str(host.resolve()) in targets:
        return True
    return any(str(host.resolve()).startswith(str(d)) for d in dir_targets)


def sonarr_unmonitor_for_paths(
    api_key: str, media_paths: list[Path]
) -> tuple[int, set[int], list[int]]:
    """Unmonitor episodes tied to deleted Jellyfin/*arr paths (no import-history gate)."""
    if not media_paths:
        return 0, set(), []

    targets: set[str] = set()
    dir_targets: list[Path] = []
    for p in media_paths:
        if p.is_dir():
            dir_targets.append(p.resolve())
        else:
            targets.add(host_to_arr_path(p))
            targets.add(str(p.resolve()))

    series_list = arr_json(f"{SONARR_URL}/api/v3/series", api_key)
    if not isinstance(series_list, list):
        return 0, set(), []

    to_unmonitor: list[int] = []
    affected_series: set[int] = set()
    stale_files = 0

    for series in series_list:
        sid = int(series["id"])
        series_host = arr_path_to_host(series.get("path") or "")
        series_dir_hit = any(
            str(series_host.resolve()).startswith(str(d))
            or str(d).startswith(str(series_host.resolve()))
            for d in dir_targets
        )
        episodes = arr_json(f"{SONARR_URL}/api/v3/episode?seriesId={sid}", api_key)
        if not isinstance(episodes, list):
            continue
        files = arr_json(f"{SONARR_URL}/api/v3/episodefile?seriesId={sid}", api_key)
        ef_path_by_episode: dict[int, str] = {}
        if isinstance(files, list):
            for ef in files:
                path = ef.get("path") or ""
                for eid in ef.get("episodeIds") or []:
                    ef_path_by_episode[int(eid)] = path
                if _path_match_target(path, targets, dir_targets):
                    host = arr_path_to_host(path)
                    if path and host and not host.is_file():
                        try:
                            arr_json(
                                f"{SONARR_URL}/api/v3/episodefile/{ef['id']}",
                                api_key,
                                method="DELETE",
                            )
                            stale_files += 1
                            for eid in ef.get("episodeIds") or []:
                                ef_path_by_episode.pop(int(eid), None)
                        except OSError:
                            pass

        for ep in episodes:
            eid = int(ep["id"])
            if not ep.get("monitored"):
                continue
            ef_path = ef_path_by_episode.get(eid, "")
            if series_dir_hit or _path_match_target(ef_path, targets, dir_targets):
                to_unmonitor.append(eid)
                affected_series.add(sid)

    seen: set[int] = set()
    deduped: list[int] = []
    for eid in to_unmonitor:
        if eid in seen:
            continue
        seen.add(eid)
        deduped.append(eid)

    _sonarr_apply_unmonitor(api_key, deduped, affected_series, stale_files)
    if deduped:
        log(f"sonarr_path_sync: unmonitored={len(deduped)} paths={len(media_paths)}")
    return len(deduped), affected_series, deduped


def radarr_imported_movie_ids(api_key: str) -> set[int]:
    imported: set[int] = set()
    page = 1
    while True:
        q = arr_json(
            f"{RADARR_URL}/api/v3/history?page={page}&pageSize=250&sortKey=date&sortDirection=descending",
            api_key,
        )
        records = (q or {}).get("records") if isinstance(q, dict) else []
        if not records:
            break
        for row in records:
            if row.get("eventType") in IMPORT_EVENTS and row.get("movieId"):
                imported.add(int(row["movieId"]))
        if page * q.get("pageSize", 250) >= q.get("totalRecords", 0):
            break
        page += 1
    return imported


def _radarr_movie_missing(m: dict) -> bool:
    mf = m.get("movieFile") or {}
    path = mf.get("path") or ""
    if path:
        return not arr_path_to_host(path).is_file()
    return not m.get("hasFile")


def radarr_unmonitor_deleted(
    api_key: str, *, require_imported: bool = True
) -> tuple[int, list[int]]:
    imported = radarr_imported_movie_ids(api_key)
    movies = arr_json(f"{RADARR_URL}/api/v3/movie", api_key)
    if not isinstance(movies, list):
        return 0, []
    count = 0
    unmon_ids: list[int] = []
    for m in movies:
        mid = int(m["id"])
        if not m.get("monitored"):
            continue
        if not _radarr_movie_missing(m):
            continue
        mf = m.get("movieFile") or {}
        if mf.get("id") and mf.get("path") and not arr_path_to_host(mf["path"]).is_file():
            try:
                arr_json(
                    f"{RADARR_URL}/api/v3/moviefile/{mf['id']}",
                    api_key,
                    method="DELETE",
                )
            except OSError:
                pass
        if require_imported and mid not in imported:
            continue
        m["monitored"] = False
        arr_json(f"{RADARR_URL}/api/v3/movie/{mid}", api_key, method="PUT", body=m)
        count += 1
        unmon_ids.append(mid)
        log(f"radarr_unmonitored: {m.get('title')}")
    log(f"radarr: unmonitored_deleted_imports={count}")
    return count, unmon_ids


def radarr_unmonitor_for_paths(api_key: str, media_paths: list[Path]) -> tuple[int, list[int]]:
    if not media_paths:
        return 0, []
    targets: set[str] = set()
    dir_targets: list[Path] = []
    for p in media_paths:
        if p.is_dir():
            dir_targets.append(p.resolve())
        else:
            targets.add(host_to_arr_path(p))
            targets.add(str(p.resolve()))

    movies = arr_json(f"{RADARR_URL}/api/v3/movie", api_key)
    if not isinstance(movies, list):
        return 0, []

    unmon_ids: list[int] = []
    for m in movies:
        mid = int(m["id"])
        if not m.get("monitored"):
            continue
        mf = m.get("movieFile") or {}
        path = mf.get("path") or ""
        host = arr_path_to_host(path) if path else None
        movie_dir = arr_path_to_host(m.get("path") or "")
        match = path in targets or (host and str(host.resolve()) in targets)
        if not match and dir_targets:
            match = any(str(movie_dir.resolve()).startswith(str(d)) for d in dir_targets)
        if not match:
            continue
        if mf.get("id") and host and not host.is_file():
            try:
                arr_json(
                    f"{RADARR_URL}/api/v3/moviefile/{mf['id']}",
                    api_key,
                    method="DELETE",
                )
            except OSError:
                pass
        m["monitored"] = False
        arr_json(f"{RADARR_URL}/api/v3/movie/{mid}", api_key, method="PUT", body=m)
        unmon_ids.append(mid)
        log(f"radarr_path_sync: {m.get('title')}")

    if unmon_ids:
        log(f"radarr_path_sync: unmonitored={len(unmon_ids)} paths={len(media_paths)}")
    return len(unmon_ids), unmon_ids


EP_PATTERNS = (
    re.compile(r"[Ss](?P<s>\d{1,2})[Ee](?P<e>\d{1,2})"),
    re.compile(r"EP(?P<e>\d{1,3})\b", re.I),
    re.compile(r"Episode\s*(?P<e>\d{1,3})\b", re.I),
)


def torrent_matches_episode(name: str, season: int, episode: int) -> bool:
    for pat in EP_PATTERNS:
        m = pat.search(name)
        if not m:
            continue
        if "s" in pat.groupdict() and m.group("s") is not None:
            if int(m.group("s")) == season and int(m.group("e")) == episode:
                return True
        elif int(m.group("e")) == episode:
            return True
    return False


def purge_qbit_for_items(
    son_key: str,
    rad_key: str,
    episode_ids: list[int],
    movie_ids: list[int],
) -> int:
    """Drop qBittorrent jobs tied to episodes/movies just unmonitored."""
    if not episode_ids and not movie_ids:
        return 0

    ep_targets: list[tuple[str, int, int]] = []
    for eid in episode_ids:
        ep = arr_json(f"{SONARR_URL}/api/v3/episode/{eid}", son_key)
        if not isinstance(ep, dict):
            continue
        sid = ep.get("seriesId")
        series = arr_json(f"{SONARR_URL}/api/v3/series/{sid}", son_key) if sid else {}
        title = (series or {}).get("title") or ""
        ep_targets.append((title, int(ep.get("seasonNumber") or 0), int(ep.get("episodeNumber") or 0)))

    movie_titles: list[str] = []
    for mid in movie_ids:
        m = arr_json(f"{RADARR_URL}/api/v3/movie/{mid}", rad_key)
        if isinstance(m, dict) and m.get("title"):
            movie_titles.append(m["title"])

    norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
    movie_norm = [(norm(t), t) for t in movie_titles]

    hashes: list[str] = []
    seen: set[str] = set()
    for t in qbit_torrents():
        name = t.get("name") or ""
        h = t.get("hash")
        if not h or h in seen:
            continue
        nl = norm(name)
        matched = False
        for mn, raw in movie_norm:
            if mn and mn in nl:
                hashes.append(h)
                seen.add(h)
                log(f"qbit_remove_movie: {raw[:60]}")
                matched = True
                break
        if matched:
            continue
        for title, season, episode in ep_targets:
            if not torrent_matches_episode(name, season, episode):
                continue
            tnorm = norm(title)
            if tnorm and len(tnorm) >= 8 and tnorm[:10] not in nl:
                continue
            hashes.append(h)
            seen.add(h)
            log(f"qbit_remove_ep: {title} S{season:02d}E{episode:02d} | {name[:70]}")
            break

    qbit_delete(hashes, delete_files=True)
    log(f"qbit: removed_torrents={len(hashes)}")
    return len(hashes)


def prune_failed_queue(son_key: str, rad_key: str, affected_series: set[int]) -> None:
    removed = 0
    for base, key, is_tv in [(SONARR_URL, son_key, True), (RADARR_URL, rad_key, False)]:
        for item in fetch_arr_queue(base, key):
            sid = item.get("seriesId") if is_tv else None
            if is_tv and sid in affected_series:
                if arr_queue_remove(base, key, item["id"], blocklist=True):
                    removed += 1
                    continue
            err = (item.get("errorMessage") or "").lower()
            if "missing" in err or item.get("trackedDownloadStatus") in (
                "importFailed",
                "downloadFailed",
            ):
                if arr_queue_remove(base, key, item["id"], blocklist=True):
                    removed += 1
    log(f"queue_rows_removed={removed}")


def finish_sync(
    son_key: str,
    rad_key: str,
    affected: set[int],
    ep_ids: list[int],
    movie_ids: list[int],
) -> None:
    prune_failed_queue(son_key, rad_key, affected)
    purge_qbit_for_items(son_key, rad_key, ep_ids, movie_ids)


def run_full_sync(son_key: str, rad_key: str) -> None:
    _, affected, ep_ids = sonarr_unmonitor_deleted(son_key)
    _, movie_ids = radarr_unmonitor_deleted(rad_key)
    finish_sync(son_key, rad_key, affected, ep_ids, movie_ids)


def run_path_sync(son_key: str, rad_key: str, media_paths: list[Path]) -> None:
    _, affected, ep_ids = sonarr_unmonitor_for_paths(son_key, media_paths)
    _, movie_ids = radarr_unmonitor_for_paths(rad_key, media_paths)
    finish_sync(son_key, rad_key, affected, ep_ids, movie_ids)


def drain_delete_queue() -> list[str]:
    if not DELETE_QUEUE.is_file():
        return []
    lines = [ln.strip() for ln in DELETE_QUEUE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    DELETE_QUEUE.unlink(missing_ok=True)
    if lines:
        log(f"delete_queue: {len(lines)} path(s)")
    return lines


def paths_from_environ() -> list[str]:
    raw: list[str] = []
    for key, val in os.environ.items():
        kl = key.lower()
        if "path" not in kl:
            continue
        if "episodefile" in kl or "moviefile" in kl or kl.endswith("_path"):
            if val and ("/media/" in val or "/data/media/" in val):
                raw.append(val)
    return raw


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--path",
        action="append",
        default=[],
        help="Deleted media path (host, /data/media, or /media); repeatable",
    )
    p.add_argument(
        "--arr-path",
        action="append",
        default=[],
        help="Path from Sonarr/Radarr custom script env (same as --path)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    log("--- sync-manual-deletes ---")
    son_key = read_api_key(SONARR_CONFIG)
    rad_key = read_api_key(RADARR_CONFIG)

    raw_paths = (
        list(args.path or [])
        + list(args.arr_path or [])
        + drain_delete_queue()
        + paths_from_environ()
    )
    media_paths = paths_under_media(raw_paths)
    if media_paths:
        log(f"path_sync: {len(media_paths)} path(s)")
        for p in media_paths[:8]:
            log(f"  {p}")
        run_path_sync(son_key, rad_key, media_paths)
    else:
        run_full_sync(son_key, rad_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
