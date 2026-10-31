#!/usr/bin/env python3
"""Re-search Sonarr/Radarr for failed grabs and missing monitored media."""
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

LOG = STALL_LOG_DIR / "retry-failed-downloads.log"
SONARR_DOWNLOAD_FAILED = 4


def log(msg: str) -> None:
    STALL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    rotate_log_file(LOG)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg)


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
    sonarr_key = read_api_key(SONARR_CONFIG)
    radarr_key = read_api_key(RADARR_CONFIG)

    clear_blocklist(SONARR_URL, sonarr_key, "Sonarr")
    clear_blocklist(RADARR_URL, radarr_key, "Radarr")

    failed_eps = sonarr_failed_episode_ids(sonarr_key)
    if failed_eps:
        arr_json(
            f"{SONARR_URL}/api/v3/command",
            sonarr_key,
            method="POST",
            body={"name": "EpisodeSearch", "episodeIds": sorted(failed_eps)},
        )
        log(f"Sonarr EpisodeSearch for {len(failed_eps)} failed episode(s): {sorted(failed_eps)}")
    else:
        log("Sonarr: no downloadFailed episodes in history")

    arr_json(
        f"{SONARR_URL}/api/v3/command",
        sonarr_key,
        method="POST",
        body={"name": "MissingEpisodeSearch"},
    )
    log("Sonarr MissingEpisodeSearch queued")

    arr_json(
        f"{RADARR_URL}/api/v3/command",
        radarr_key,
        method="POST",
        body={"name": "MissingMoviesSearch"},
    )
    log("Radarr MissingMoviesSearch queued")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as e:
        log(f"ERROR: {e}")
        raise SystemExit(1)
