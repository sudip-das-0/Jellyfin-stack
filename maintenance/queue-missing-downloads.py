#!/usr/bin/env python3
"""Queue indexer searches for monitored missing episodes/movies (Sonarr + Radarr)."""
from __future__ import annotations

import argparse
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
    echo_script_log,
    queue_arr_command,
    read_api_key,
    wanted_missing_count,
)

LOG = STALL_LOG_DIR / "queue-missing-downloads.log"


def log(msg: str) -> None:
    echo_script_log(LOG, msg, rotate=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--tv-only",
        action="store_true",
        help="Sonarr MissingEpisodeSearch only (skip Radarr)",
    )
    p.add_argument(
        "--movies-only",
        action="store_true",
        help="Radarr MissingMoviesSearch only (skip Sonarr)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.tv_only and args.movies_only:
        print("Use at most one of --tv-only and --movies-only", file=sys.stderr)
        return 2

    run_sonarr = not args.movies_only
    run_radarr = not args.tv_only

    sonarr_key = read_api_key(SONARR_CONFIG)
    radarr_key = read_api_key(RADARR_CONFIG)

    if run_sonarr:
        sonarr_missing = wanted_missing_count(SONARR_URL, sonarr_key)
        log(f"before: sonarr_missing={sonarr_missing}")
        sonarr_cmd = queue_arr_command(SONARR_URL, sonarr_key, "MissingEpisodeSearch")
        log(
            f"Sonarr MissingEpisodeSearch command id={sonarr_cmd.get('id')} "
            f"status={sonarr_cmd.get('status')}"
        )

    if run_radarr:
        radarr_missing = wanted_missing_count(RADARR_URL, radarr_key)
        log(f"before: radarr_missing={radarr_missing}")
        radarr_cmd = queue_arr_command(RADARR_URL, radarr_key, "MissingMoviesSearch")
        log(
            f"Radarr MissingMoviesSearch command id={radarr_cmd.get('id')} "
            f"status={radarr_cmd.get('status')}"
        )

    log(
        "Grabs appear in Sonarr/Radarr Activity → Queue, then qBittorrent "
        "(max 50 concurrent downloads on home Wi‑Fi; see config/qbit-network-profiles.json)."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as e:
        log(f"ERROR: {e}")
        raise SystemExit(1)
