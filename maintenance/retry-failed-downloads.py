#!/usr/bin/env python3
"""Re-search Sonarr/Radarr for failed grabs and missing monitored media."""
from __future__ import annotations

import argparse
import sys
import urllib.error

sys.path.insert(0, "/mnt/jellyfin/scripts/lib")
from arr_common import (
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    STALL_LOG_DIR,
    arr_json,
    echo_script_log,
    queue_arr_command,
    read_api_key,
)

LOG = STALL_LOG_DIR / "retry-failed-downloads.log"
SONARR_DOWNLOAD_FAILED = 4


def log(msg: str) -> None:
    echo_script_log(LOG, msg, rotate=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tv-only", action="store_true", help="Skip Radarr")
    p.add_argument("--movies-only", action="store_true", help="Skip Sonarr")
    p.add_argument(
        "--keep-blocklist",
        action="store_true",
        help="Do not clear Sonarr/Radarr blocklists before re-search",
    )
    return p.parse_args()


def paginate_history(base_url: str, api_key: str, *, event_type: int) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        q = arr_json(
            f"{base_url}/api/v3/history?page={page}&pageSize=100&eventType={event_type}",
            api_key,
        )
        recs = (q or {}).get("records") if isinstance(q, dict) else []
        if not recs:
            break
        out.extend(recs)
        if len(recs) < 100:
            break
        page += 1
    return out


def paginate_blocklist(base_url: str, api_key: str) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        q = arr_json(f"{base_url}/api/v3/blocklist?page={page}&pageSize=100", api_key)
        recs = (q or {}).get("records") if isinstance(q, dict) else []
        if not recs:
            break
        out.extend(recs)
        if len(recs) < 100:
            break
        page += 1
    return out


def clear_blocklist(base_url: str, api_key: str, label: str) -> int:
    rows = paginate_blocklist(base_url, api_key)
    if not rows:
        return 0
    ids = [int(r["id"]) for r in rows if r.get("id") is not None]
    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        arr_json(
            f"{base_url}/api/v3/blocklist/bulk",
            api_key,
            method="DELETE",
            body={"ids": batch},
        )
    log(f"{label}: cleared {len(ids)} blocklist row(s)")
    return len(ids)


def sonarr_failed_episode_ids(api_key: str) -> set[int]:
    ids: set[int] = set()
    for row in paginate_history(SONARR_URL, api_key, event_type=SONARR_DOWNLOAD_FAILED):
        eid = row.get("episodeId")
        if eid is not None:
            ids.add(int(eid))
    return ids


def main() -> int:
    args = parse_args()
    if args.tv_only and args.movies_only:
        print("Use at most one of --tv-only and --movies-only", file=sys.stderr)
        return 2

    run_sonarr = not args.movies_only
    run_radarr = not args.tv_only

    sonarr_key = read_api_key(SONARR_CONFIG)
    radarr_key = read_api_key(RADARR_CONFIG)

    if not args.keep_blocklist:
        if run_sonarr:
            clear_blocklist(SONARR_URL, sonarr_key, "Sonarr")
        if run_radarr:
            clear_blocklist(RADARR_URL, radarr_key, "Radarr")

    if run_sonarr:
        failed_eps = sonarr_failed_episode_ids(sonarr_key)
        if failed_eps:
            queue_arr_command(
                SONARR_URL,
                sonarr_key,
                "EpisodeSearch",
                episodeIds=sorted(failed_eps),
            )
            log(f"Sonarr EpisodeSearch for {len(failed_eps)} failed episode(s): {sorted(failed_eps)}")
        else:
            log("Sonarr: no downloadFailed episodes in history")

        queue_arr_command(SONARR_URL, sonarr_key, "MissingEpisodeSearch")
        log("Sonarr MissingEpisodeSearch queued")

    if run_radarr:
        queue_arr_command(RADARR_URL, radarr_key, "MissingMoviesSearch")
        log("Radarr MissingMoviesSearch queued")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as e:
        log(f"ERROR: {e}")
        raise SystemExit(1)
