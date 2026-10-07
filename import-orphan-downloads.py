#!/usr/bin/env python3
"""Import completed files left in /mnt/jellyfin/torrents after qBit removed the job too early.

Uses Sonarr/Radarr DownloadedEpisodesScan / DownloadedMoviesScan per file (reliable on v4).
Safe to re-run (skips files that no longer match a missing episode/movie).
Not on the maintenance timer — run when imports stalled.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
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
    read_api_key,
)

TORRENTS = Path("/mnt/jellyfin/torrents")
INCOMPLETE = TORRENTS / "incomplete"
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".wmv", ".ts"}


def iter_download_videos() -> list[Path]:
    out: list[Path] = []
    if not TORRENTS.is_dir():
        return out
    for entry in TORRENTS.iterdir():
        if entry == INCOMPLETE:
            continue
        if entry.is_file() and entry.suffix.lower() in VIDEO_EXT:
            out.append(entry)
            continue
        if entry.is_dir():
            for f in entry.iterdir():
                if f.is_file() and f.suffix.lower() in VIDEO_EXT:
                    out.append(f)
    return sorted(out)


def docker_path(host_path: Path) -> str:
    return "/data/torrents" + str(host_path)[len(str(TORRENTS)) :].replace("\\", "/")


def run_import_command(
    base_url: str,
    api_key: str,
    *,
    command: str,
    path: str,
    dry_run: bool,
) -> bool:
    if dry_run:
        return True
    body = {"name": command, "path": path, "importMode": "Auto"}
    created = arr_json(f"{base_url}/api/v3/command", api_key, method="POST", body=body)
    if not isinstance(created, dict) or not created.get("id"):
        return False
    cid = created["id"]
    for _ in range(60):
        time.sleep(0.5)
        status = arr_json(f"{base_url}/api/v3/command/{cid}", api_key)
        if not isinstance(status, dict):
            return False
        state = status.get("status")
        if state == "completed":
            msg = (status.get("message") or "").lower()
            if msg in ("failed", "failed to import"):
                return False
            return status.get("result") == "successful"
        if state == "failed":
            return False
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max files to scan (0 = all)")
    args = parser.parse_args()

    sonarr_key = read_api_key(SONARR_CONFIG)
    radarr_key = read_api_key(RADARR_CONFIG)
    files = iter_download_videos()
    imported = 0
    for path in files:
        if args.limit and imported >= args.limit:
            break
        dpath = docker_path(path)
        try:
            if args.dry_run:
                append_recovery_log(f"[dry-run] scan {path.name}", tag="orphan-import")
                imported += 1
                continue
            if run_import_command(
                SONARR_URL,
                sonarr_key,
                command="DownloadedEpisodesScan",
                path=dpath,
                dry_run=False,
            ):
                append_recovery_log(f"sonarr scan imported {path.name}", tag="orphan-import")
                imported += 1
                continue
            if run_import_command(
                RADARR_URL,
                radarr_key,
                command="DownloadedMoviesScan",
                path=dpath,
                dry_run=False,
            ):
                append_recovery_log(f"radarr scan imported {path.name}", tag="orphan-import")
                imported += 1
        except urllib.error.HTTPError as e:
            append_recovery_log(
                f"HTTP {e.code} scanning {path.name}: {e.reason}",
                tag="orphan-import",
            )
    print(f"Done: imports={imported} scanned={len(files)} dry_run={args.dry_run}")
    STALL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
