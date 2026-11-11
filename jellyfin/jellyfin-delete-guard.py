#!/usr/bin/env python3
"""Keep Jellyfin DB in sync when movies/episodes are deleted from the UI.

Runs: download-maintenance.sh, recover-jellyfin-drive.sh, jellyfin-delete-webhook (ItemDeleted).
Depends: jellyfin_db_lib.py, arr_common.STALL_LOG_DIR
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/jellyfin/scripts/lib")

from arr_common import STALL_LOG_DIR, echo_script_log  # noqa: E402
from jellyfin_db_lib import (  # noqa: E402
    connect,
    finish_delete,
    media_library_mount_healthy,
    prepare_items_for_delete,
    purge_delete_ghosts,
    sanitize_userdata,
)

LOG = STALL_LOG_DIR / "jellyfin-delete-guard.log"


def log(msg: str) -> None:
    echo_script_log(LOG, msg)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sanitize-all",
        action="store_true",
        help="Drop duplicate TMDB/IMDb UserData copies; keep item-GUID continue-watching rows",
    )
    parser.add_argument(
        "--item-id",
        action="append",
        default=[],
        metavar="UUID",
        help="Movie/episode id (repeatable)",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Container or host media path (repeatable)",
    )
    parser.add_argument(
        "--purge-ghosts",
        action="store_true",
        help="Drop movie/episode rows whose file is missing on disk",
    )
    parser.add_argument(
        "--ghost-limit",
        type=int,
        default=100,
        help="Max ghosts to purge per run (default 100)",
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Sanitize UserData for given --item-id before Jellyfin delete",
    )
    args = parser.parse_args()

    if not any(
        [
            args.sanitize_all,
            args.item_id,
            args.path,
            args.purge_ghosts,
            args.prepare,
        ]
    ):
        parser.print_help()
        return 2

    conn = connect()
    try:
        if args.sanitize_all:
            n = sanitize_userdata(conn)
            if n:
                log(f"sanitize-all: removed {n} corrupt UserData row(s)")

        if args.prepare and args.item_id:
            n = prepare_items_for_delete(conn, args.item_id)
            if n:
                log(f"prepare: sanitized UserData for {len(args.item_id)} item(s), {n} row(s)")

        for iid in args.item_id:
            if args.prepare:
                continue
            res = finish_delete(conn, item_id=iid)
            if res["sanitized_userdata"] or res["purged_ids"]:
                log(
                    f"finish-delete item={iid} userdata={res['sanitized_userdata']} "
                    f"purged={res['purged_ids']}"
                )

        for p in args.path:
            res = finish_delete(conn, path=p)
            if res["sanitized_userdata"] or res["purged_ids"]:
                log(
                    f"finish-delete path={p} userdata={res['sanitized_userdata']} "
                    f"purged={res['purged_ids']}"
                )

        if args.purge_ghosts:
            if not media_library_mount_healthy():
                log("purge-ghosts: skipped — jellyfin media SSD not healthy")
            else:
                n = purge_delete_ghosts(conn, limit=args.ghost_limit)
                if n:
                    log(f"purge-ghosts: removed {n} movie/episode row(s) with missing files")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
