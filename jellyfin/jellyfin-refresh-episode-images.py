#!/usr/bin/env python3
"""Refresh Jellyfin TV episode images when Primary is missing (UI falls back to series poster)."""
from __future__ import annotations

import argparse
import sqlite3
import sys
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from arr_common import MEDIA_ROOT, STALL_LOG_DIR, echo_script_log  # noqa: E402
from jellyfin_api import JellyfinAPIError, jellyfin_request  # noqa: E402
from jellyfin_db_lib import db  # noqa: E402

LOG = STALL_LOG_DIR / "jellyfin-episode-images.log"
EPISODE_TYPE = "MediaBrowser.Controller.Entities.TV.Episode"


def log(msg: str) -> None:
    echo_script_log(LOG, msg.rstrip())


def host_path_to_container(path: str) -> str:
    p = path.strip()
    if p.startswith("/data/media/"):
        return "/media/" + p[len("/data/media/") :]
    media = str(MEDIA_ROOT)
    if p.startswith(media + "/"):
        return "/media/" + p[len(media) + 1 :]
    if p.startswith("/media/"):
        return p
    raise ValueError(f"Not a library media path: {path}")


def series_ids_missing_primary(conn: sqlite3.Connection, *, limit_series: int | None = None) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT e.SeriesId
        FROM BaseItems e
        LEFT JOIN BaseItemImageInfos b ON b.ItemId = e.Id AND b.ImageType = 0
        WHERE e.Type = ? AND b.Id IS NULL AND e.SeriesId IS NOT NULL
        ORDER BY e.SeriesId
        """,
        (EPISODE_TYPE,),
    ).fetchall()
    ids = [r[0] for r in rows if r[0]]
    return ids if limit_series is None else ids[:limit_series]


def series_id_for_media_path(conn: sqlite3.Connection, container_path: str) -> str | None:
    row = conn.execute(
        "SELECT SeriesId FROM BaseItems WHERE Type=? AND Path=? LIMIT 1",
        (EPISODE_TYPE, container_path),
    ).fetchone()
    return row[0] if row else None


def refresh_series(series_id: str, key_name: str) -> None:
    q = urllib.parse.urlencode(
        {
            "Recursive": "true",
            "ImageRefreshMode": "FullRefresh",
            "MetadataRefreshMode": "Default",
            "ReplaceAllImages": "false",
        }
    )
    code, _ = jellyfin_request(
        f"/Items/{series_id}/Refresh?{q}", method="POST", key_name=key_name, timeout=120
    )
    log(f"refresh series {series_id}: HTTP {code}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--path",
        action="append",
        dest="paths",
        metavar="FILE",
        help="Host or /data/media path of one episode (may repeat)",
    )
    p.add_argument(
        "--all-missing",
        action="store_true",
        help="Refresh every series that has at least one episode without Primary",
    )
    p.add_argument(
        "--limit-series",
        type=int,
        default=25,
        help="Max series to refresh per run with --all-missing (default 25)",
    )
    p.add_argument(
        "--api-key-name",
        default="scan-script",
        help="Jellyfin ApiKeys.Name (default scan-script)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.paths and not args.all_missing:
        print("Use --all-missing and/or --path", file=sys.stderr)
        return 2

    series_ids: set[str] = set()

    # One read-only connection for every lookup in this run.
    with db() as conn:
        if args.all_missing:
            series_ids.update(series_ids_missing_primary(conn, limit_series=args.limit_series))

        for raw in args.paths or []:
            try:
                cpath = host_path_to_container(raw)
            except ValueError as e:
                log(f"skip path: {e}")
                continue
            sid = series_id_for_media_path(conn, cpath)
            if sid:
                series_ids.add(sid)
            else:
                log(f"no Jellyfin episode for path {cpath}")

    if not series_ids:
        log("nothing to refresh")
        return 0

    for sid in sorted(series_ids):
        try:
            refresh_series(sid, args.api_key_name)
        except (urllib.error.URLError, JellyfinAPIError, RuntimeError) as e:
            log(f"ERROR refresh {sid}: {e}")
            return 1
    log(f"queued image refresh for {len(series_ids)} series")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
