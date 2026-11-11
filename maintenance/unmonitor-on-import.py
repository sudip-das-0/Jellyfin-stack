#!/usr/bin/env python3
"""Unmonitor Sonarr episodes (and optionally Radarr movies) after successful import."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/jellyfin/scripts/lib")
from arr_common import (
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    STALL_LOG_DIR,
    arr_json,
    echo_script_log,
    read_api_key,
)

LOG = STALL_LOG_DIR / "unmonitor-on-import.log"
SONARR_QUEUE = STALL_LOG_DIR / "unmonitor-sonarr.queue"
RADARR_QUEUE = STALL_LOG_DIR / "unmonitor-radarr.queue"


def log(msg: str) -> None:
    echo_script_log(LOG, msg)


def drain_queue(path: Path) -> list[int]:
    if not path.is_file():
        return []
    ids: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        for part in line.replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
    path.unlink(missing_ok=True)
    return ids


def sonarr_unmonitor_ids(api_key: str, episode_ids: list[int]) -> int:
    if not episode_ids:
        return 0
    unique = list(dict.fromkeys(episode_ids))
    done = 0
    for i in range(0, len(unique), 200):
        chunk = unique[i : i + 200]
        arr_json(
            f"{SONARR_URL}/api/v3/episode/monitor",
            api_key,
            method="PUT",
            body={"episodeIds": chunk, "monitored": False},
        )
        done += len(chunk)
    log(f"sonarr_unmonitor: episodes={done}")
    return done


def radarr_unmonitor_ids(api_key: str, movie_ids: list[int]) -> int:
    if not movie_ids:
        return 0
    count = 0
    for mid in dict.fromkeys(movie_ids):
        m = arr_json(f"{RADARR_URL}/api/v3/movie/{mid}", api_key)
        if not isinstance(m, dict) or not m.get("monitored"):
            continue
        m["monitored"] = False
        arr_json(f"{RADARR_URL}/api/v3/movie/{mid}", api_key, method="PUT", body=m)
        count += 1
    if count:
        log(f"radarr_unmonitor: movies={count}")
    return count


def radarr_unmonitor_all_with_files(api_key: str) -> int:
    movies = arr_json(f"{RADARR_URL}/api/v3/movie", api_key)
    if not isinstance(movies, list):
        return 0
    ids = [
        int(m["id"])
        for m in movies
        if m.get("monitored") and m.get("hasFile")
    ]
    return radarr_unmonitor_ids(api_key, ids)


def sonarr_unmonitor_all_with_files(api_key: str) -> int:
    series_list = arr_json(f"{SONARR_URL}/api/v3/series", api_key)
    if not isinstance(series_list, list):
        return 0
    to_unmonitor: list[int] = []
    for series in series_list:
        sid = series["id"]
        episodes = arr_json(f"{SONARR_URL}/api/v3/episode?seriesId={sid}", api_key)
        if not isinstance(episodes, list):
            continue
        for ep in episodes:
            if ep.get("monitored") and ep.get("hasFile"):
                to_unmonitor.append(int(ep["id"]))
    return sonarr_unmonitor_ids(api_key, to_unmonitor)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--drain-queue", action="store_true", help="Process *arr hook queues")
    p.add_argument("--sonarr-episode-ids", default="", help="Comma-separated episode IDs")
    p.add_argument(
        "--sonarr-all-with-files",
        action="store_true",
        help="One-time: unmonitor every episode that already has a file",
    )
    p.add_argument("--radarr-movie-ids", default="", help="Comma-separated movie IDs")
    p.add_argument(
        "--radarr-all-with-files",
        action="store_true",
        help="One-time: unmonitor every movie that already has a file",
    )
    p.add_argument(
        "--all-with-files",
        action="store_true",
        help="Backfill Sonarr + Radarr (episodes/movies on disk)",
    )
    return p.parse_args(argv)


def parse_id_list(raw: str) -> list[int]:
    out: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    son_key = read_api_key(SONARR_CONFIG)
    rad_key = read_api_key(RADARR_CONFIG)

    son_ids = parse_id_list(args.sonarr_episode_ids)
    rad_ids = parse_id_list(args.radarr_movie_ids)

    if args.drain_queue:
        son_ids.extend(drain_queue(SONARR_QUEUE))
        rad_ids.extend(drain_queue(RADARR_QUEUE))

    if args.all_with_files or args.sonarr_all_with_files:
        sonarr_unmonitor_all_with_files(son_key)
    if args.all_with_files or args.radarr_all_with_files:
        radarr_unmonitor_all_with_files(rad_key)
    if args.all_with_files or args.sonarr_all_with_files or args.radarr_all_with_files:
        return 0

    sonarr_unmonitor_ids(son_key, son_ids)
    radarr_unmonitor_ids(rad_key, rad_ids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
