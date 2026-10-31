#!/usr/bin/env python3
"""Queue indexer searches for all monitored missing episodes/movies (Sonarr + Radarr)."""
from __future__ import annotations

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
    arr_json,
    read_api_key,
    rotate_log_file,
)

LOG = STALL_LOG_DIR / "queue-missing-downloads.log"


def log(msg: str) -> None:
    STALL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    rotate_log_file(LOG)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg)


def missing_count(base_url: str, api_key: str, *, tv: bool) -> int:
    path = "wanted/missing" if tv else "wanted/missing"
    q = arr_json(f"{base_url}/api/v3/{path}?pageSize=1", api_key)
    if isinstance(q, dict):
        return int(q.get("totalRecords") or 0)
    return 0


def main() -> int:
    sonarr_key = read_api_key(SONARR_CONFIG)
    radarr_key = read_api_key(RADARR_CONFIG)

    sonarr_missing = missing_count(SONARR_URL, sonarr_key, tv=True)
    radarr_missing = missing_count(RADARR_URL, radarr_key, tv=False)
    log(f"before: sonarr_missing={sonarr_missing} radarr_missing={radarr_missing}")

    sonarr_cmd = arr_json(
        f"{SONARR_URL}/api/v3/command",
        sonarr_key,
        method="POST",
        body={"name": "MissingEpisodeSearch"},
    )
    log(
        f"Sonarr MissingEpisodeSearch command id={sonarr_cmd.get('id')} "
        f"status={sonarr_cmd.get('status')}"
    )

    radarr_cmd = arr_json(
        f"{RADARR_URL}/api/v3/command",
        radarr_key,
        method="POST",
        body={"name": "MissingMoviesSearch"},
    )
    log(
        f"Radarr MissingMoviesSearch command id={radarr_cmd.get('id')} "
        f"status={radarr_cmd.get('status')}"
    )

    log(
        "Grabs appear in Sonarr/Radarr Activity → Queue, then qBittorrent "
        "(max 30 concurrent downloads on home Wi‑Fi)."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as e:
        log(f"ERROR: {e}")
        raise SystemExit(1)
