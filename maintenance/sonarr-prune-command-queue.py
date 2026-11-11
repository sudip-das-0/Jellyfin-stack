#!/usr/bin/env python3
"""Cancel excess queued Sonarr SeasonSearch commands so imports/rescans can run."""
from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, "/mnt/jellyfin/scripts/lib")
from arr_common import SONARR_CONFIG, SONARR_URL, append_recovery_log, arr_json, read_api_key

KEEP = int(__import__("os").environ.get("SONARR_KEEP_QUEUED_SEASONSEARCH", "0"))


def main() -> int:
    api_key = read_api_key(SONARR_CONFIG)
    cmds = arr_json(f"{SONARR_URL}/api/v3/command", api_key)
    if not isinstance(cmds, list):
        return 1
    queued = [
        c
        for c in cmds
        if c.get("name") == "SeasonSearch"
        and c.get("status") in ("queued", "orphaned")
        and c.get("id")
    ]
    if len(queued) <= KEEP:
        return 0
    queued.sort(key=lambda c: c.get("queued") or "")
    to_drop = queued[:-KEEP] if KEEP else queued
    dropped = 0
    for c in to_drop:
        try:
            arr_json(f"{SONARR_URL}/api/v3/command/{c['id']}", api_key, method="DELETE")
            dropped += 1
        except urllib.error.HTTPError:
            pass
    if dropped:
        append_recovery_log(
            f"sonarr-prune-command-queue: cancelled {dropped} queued SeasonSearch (keep {KEEP})",
            tag="sonarr",
        )
        print(f"cancelled {dropped} queued SeasonSearch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
