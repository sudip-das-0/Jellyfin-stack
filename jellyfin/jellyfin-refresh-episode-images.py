#!/usr/bin/env python3
"""Refresh Jellyfin TV episode images when Primary is missing (UI falls back to series poster)."""
from __future__ import annotations

import argparse
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, "/mnt/jellyfin/scripts/lib")
from arr_common import JELLYFIN_DB, JELLYFIN_URL, MEDIA_ROOT, STALL_LOG_DIR, echo_script_log  # noqa: E402

LOG = STALL_LOG_DIR / "jellyfin-episode-images.log"
EPISODE_TYPE = "MediaBrowser.Controller.Entities.TV.Episode"


def log(msg: str) -> None:
    echo_script_log(LOG, msg.rstrip())


def api_key(name: str | None = None) -> str:
    conn = sqlite3.connect(JELLYFIN_DB)
    if name:
        row = conn.execute(
            "SELECT AccessToken FROM ApiKeys WHERE Name=? LIMIT 1", (name,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT AccessToken FROM ApiKeys ORDER BY DateCreated DESC LIMIT 1"
        ).fetchone()
    conn.close()
    if not row or not row[0]:
        raise RuntimeError("No Jellyfin API key in jellyfin.db")
    return row[0]


def jellyfin_post(path: str, key: str, *, timeout: int = 120) -> int:
    req = urllib.request.Request(
        f"{JELLYFIN_URL}{path}",
        method="POST",
        headers={"Authorization": f'MediaBrowser Token="{key}"'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


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


def series_ids_missing_primary(*, limit_series: int | None = None) -> list[str]:
    conn = sqlite3.connect(JELLYFIN_DB)
    rows = conn.execute(
        f"""
        SELECT DISTINCT e.SeriesId
        FROM BaseItems e
        LEFT JOIN BaseItemImageInfos b ON b.ItemId = e.Id AND b.ImageType = 0
        WHERE e.Type = ? AND b.Id IS NULL AND e.SeriesId IS NOT NULL
        ORDER BY e.SeriesId
        """,
        (EPISODE_TYPE,),
    ).fetchall()
    conn.close()
    ids = [r[0] for r in rows if r[0]]
    if limit_series is not None:
        ids = ids[:limit_series]
    return ids


def series_id_for_media_path(container_path: str) -> str | None:
    conn = sqlite3.connect(JELLYFIN_DB)
    row = conn.execute(
        "SELECT SeriesId FROM BaseItems WHERE Type=? AND Path=? LIMIT 1",
        (EPISODE_TYPE, container_path),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def refresh_series(series_id: str, key: str) -> None:
    q = urllib.parse.urlencode(
        {
            "Recursive": "true",
            "ImageRefreshMode": "FullRefresh",
            "MetadataRefreshMode": "Default",
            "ReplaceAllImages": "false",
        }
    )
    code = jellyfin_post(f"/Items/{series_id}/Refresh?{q}", key)
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

    key = api_key(args.api_key_name)
    series_ids: set[str] = set()

    if args.all_missing:
        series_ids.update(series_ids_missing_primary(limit_series=args.limit_series))

    for raw in args.paths or []:
        try:
            cpath = host_path_to_container(raw)
        except ValueError as e:
            log(f"skip path: {e}")
            continue
        sid = series_id_for_media_path(cpath)
        if sid:
            series_ids.add(sid)
        else:
            log(f"no Jellyfin episode for path {cpath}")

    if not series_ids:
        log("nothing to refresh")
        return 0

    for sid in sorted(series_ids):
        try:
            refresh_series(sid, key)
        except urllib.error.URLError as e:
            log(f"ERROR refresh {sid}: {e}")
            return 1
    log(f"queued image refresh for {len(series_ids)} series")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
