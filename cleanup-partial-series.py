#!/usr/bin/env python3
"""Clean Sonarr/Radarr failed queue rows; drop orphan episode folders in torrents."""
from __future__ import annotations

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
    TORRENTS_ROOT,
    arr_json,
    read_api_key,
    rotate_log_file,
)

TORRENTS = TORRENTS_ROOT
LOG_FILE = STALL_LOG_DIR / "partial-series-cleanup.log"
VERBOSE = os.environ.get("CLEANUP_VERBOSE", "") == "1"


def log(msg: str) -> None:
    STALL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    rotate_log_file(LOG_FILE)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")
    if VERBOSE or msg.startswith("Done:"):
        print(msg)


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


def remove_failed_queue(api_key: str, *, base_url: str, label: str) -> int:
    to_remove: list[tuple[int, str]] = []
    page = 1
    while True:
        extra = "&includeUnknownSeriesItems=true" if base_url == SONARR_URL else ""
        q = arr_json(f"{base_url}/api/v3/queue?page={page}&pageSize=100{extra}", api_key)
        records = (q or {}).get("records") if isinstance(q, dict) else []
        if not records:
            break
        for row in records:
            if should_remove_queue_row(row) and row.get("id"):
                to_remove.append((int(row["id"]), row.get("title") or ""))
        if len(records) < 100:
            break
        page += 1

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


def orphan_torrent_dirs() -> int:
    if not TORRENTS.is_dir():
        return 0
    removed = 0
    ep_re = re.compile(r"[Ss]\d{1,2}[Ee]\d{1,2}")
    for entry in TORRENTS.iterdir():
        if not entry.is_dir() or entry.name in ("incomplete", "tv"):
            continue
        if not ep_re.search(entry.name):
            continue
        mkvs = list(entry.glob("*.mkv"))
        if not mkvs or any(m.stat().st_nlink > 1 for m in mkvs):
            continue
        shutil.rmtree(entry)
        removed += 1
        if VERBOSE:
            log(f"[torrents] removed orphan {entry.name[:80]}")
    return removed


def main() -> int:
    qs = remove_failed_queue(read_api_key(SONARR_CONFIG), base_url=SONARR_URL, label="sonarr")
    qr = remove_failed_queue(read_api_key(RADARR_CONFIG), base_url=RADARR_URL, label="radarr")
    t = orphan_torrent_dirs()
    log(f"Done: sonarr_queue={qs} radarr_queue={qr} orphan_torrent_dirs={t}")
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
        sys.exit(main())
    except urllib.error.URLError as e:
        print(f"*arr unavailable: {e}", file=sys.stderr)
        sys.exit(1)
