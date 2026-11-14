#!/usr/bin/env python3
"""Clean Sonarr/Radarr failed queue rows; drop orphan episode folders in torrents.

Usage: cleanup-partial-series.py [--dry-run] | --torrent-library [--dry-run]
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
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    STALL_LOG_DIR,
    TORRENTS_ROOT,
    arr_json,
    echo_script_log,
    fetch_arr_queue,
    qbit_unfinished_content_roots,
    read_api_key,
)

TORRENTS = TORRENTS_ROOT
LOG_FILE = STALL_LOG_DIR / "partial-series-cleanup.log"
VERBOSE = os.environ.get("CLEANUP_VERBOSE", "") == "1"


def log(msg: str) -> None:
    echo_script_log(
        LOG_FILE, msg, rotate=True, echo=VERBOSE or msg.startswith("Done:")
    )


def should_remove_queue_row(row: dict) -> bool:
    status = row.get("status") or ""
    tracked = row.get("trackedDownloadStatus") or ""
    err = (row.get("errorMessage") or "").lower()
    if tracked in ("importFailed", "downloadFailed") or status == "failed":
        return True
    if status == "completed" and tracked == "warning":
        return True
    if status != "warning":
        return False
    if "missing files" in err or "stalled" in err:
        return True
    return bool(err)


def remove_failed_queue(api_key: str, *, base_url: str, label: str, dry_run: bool = False) -> int:
    to_remove: list[tuple[int, str]] = []
    for row in fetch_arr_queue(base_url, api_key):
        if should_remove_queue_row(row) and row.get("id"):
            to_remove.append((int(row["id"]), row.get("title") or ""))

    if dry_run:
        for qid, title in to_remove:
            log(f"[{label}] would remove id={qid} {title[:70]}")
        return len(to_remove)

    removed = 0
    for i in range(0, len(to_remove), 40):
        batch = to_remove[i : i + 40]
        ids = [qid for qid, _ in batch]
        try:
            arr_json(
                f"{base_url}/api/v3/queue/bulk",
                api_key,
                method="DELETE",
                body={"ids": ids, "removeFromClient": True, "blocklist": True},
            )
            removed += len(batch)
            if VERBOSE:
                for qid, title in batch:
                    log(f"[{label}] removed id={qid} {title[:70]}")
        except urllib.error.HTTPError:
            for qid, title in batch:
                try:
                    arr_json(
                        f"{base_url}/api/v3/queue/bulk",
                        api_key,
                        method="DELETE",
                        body={"ids": [qid], "removeFromClient": True, "blocklist": True},
                    )
                    removed += 1
                    if VERBOSE:
                        log(f"[{label}] removed id={qid} {title[:70]}")
                except urllib.error.HTTPError as e2:
                    log(f"[{label}] could not remove id={qid}: HTTP {e2.code}")
    return removed


def orphan_torrent_dirs(*, dry_run: bool = False) -> int:
    """Remove episode folders in torrents/ that are not hardlinked into the library.

    Skips anything qBittorrent is still downloading; if qBittorrent cannot be queried nothing
    is removed (fail closed).
    """
    if not TORRENTS.is_dir():
        return 0
    try:
        protected = qbit_unfinished_content_roots()
    except OSError as e:
        log(f"[torrents] qBittorrent unavailable ({e}); skipping orphan dir removal")
        return 0
    removed = 0
    ep_re = re.compile(r"[Ss]\d{1,2}[Ee]\d{1,2}")
    for entry in TORRENTS.iterdir():
        if not entry.is_dir() or entry.name in ("incomplete", "tv"):
            continue
        if not ep_re.search(entry.name):
            continue
        if any(p == entry or entry in p.parents or p in entry.parents for p in protected):
            continue
        mkvs = list(entry.glob("*.mkv"))
        if not mkvs or any(m.stat().st_nlink > 1 for m in mkvs):
            continue
        if dry_run:
            log(f"[torrents] would remove orphan {entry.name[:80]}")
        else:
            shutil.rmtree(entry)
            if VERBOSE:
                log(f"[torrents] removed orphan {entry.name[:80]}")
        removed += 1
    return removed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Log what would be removed only")
    args = parser.parse_args(argv)
    qs = remove_failed_queue(
        read_api_key(SONARR_CONFIG), base_url=SONARR_URL, label="sonarr", dry_run=args.dry_run
    )
    qr = remove_failed_queue(
        read_api_key(RADARR_CONFIG), base_url=RADARR_URL, label="radarr", dry_run=args.dry_run
    )
    t = orphan_torrent_dirs(dry_run=args.dry_run)
    log(f"Done: {'DRY-RUN ' if args.dry_run else ''}sonarr_queue={qs} radarr_queue={qr} orphan_torrent_dirs={t}")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--torrent-library":
        import subprocess

        cmd = [
            sys.executable,
            str(Path(__file__).resolve().parent / "cleanup-torrent-library-orphans.py"),
        ]
        if "--dry-run" in argv:
            cmd.append("--dry-run")
        raise SystemExit(subprocess.call(cmd))
    try:
        sys.exit(main(argv))
    except urllib.error.URLError as e:
        print(f"*arr unavailable: {e}", file=sys.stderr)
        sys.exit(1)
