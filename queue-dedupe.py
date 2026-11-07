#!/usr/bin/env python3
"""One torrent + one queue row per Sonarr episode / Radarr movie.

Runs: download-maintenance.sh (before stall recovery). Updates shared stall state on trim.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arr_common import (
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    STALL_LOG_DIR,
    STALL_RECOVERY_STATE_PATH,
    TZ,
    arr_queue_remove,
    fetch_arr_queue,
    norm_qbit_hash,
    qbit_delete,
    qbit_torrents,
    read_api_key,
)
LOG_DIR = STALL_LOG_DIR
DEDUPE_VERBOSE = os.environ.get("DEDUPE_VERBOSE", "") == "1"
STATE_PATH = STALL_RECOVERY_STATE_PATH
ORPHAN_QUEUE_HOURS = 6


def log(msg: str, *, detail: bool = False) -> None:
    if detail and not DEDUPE_VERBOSE:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    line = f"{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')} [dedupe] {msg}\n"
    with (LOG_DIR / f"recovery-{day}.log").open("a", encoding="utf-8") as f:
        f.write(line)
    if not detail or DEDUPE_VERBOSE:
        print(line, end="")


def torrent_score(t: dict | None) -> tuple:
    if t is None:
        return (-1.0, -1, -1, -1)
    state = t.get("state") or ""
    state_bonus = 1 if state in ("downloading", "metaDL", "forcedDL", "queuedDL") else 0
    return (
        float(t.get("progress") or 0),
        state_bonus,
        int(t.get("dlspeed") or 0),
        int(t.get("num_seeds") or 0),
    )


def parse_added(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        if iso.endswith("Z"):
            return datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def reset_stall_state(job_key: str, keep_hash: str | None) -> None:
    if not STATE_PATH.exists():
        return
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    jobs = state.get("jobs") or {}
    if job_key not in jobs:
        return
    if keep_hash:
        jobs[job_key] = {
            "phase": "primary",
            "tried_guids": jobs[job_key].get("tried_guids") or [],
            "queue_ids": [],
            "hashes": {
                keep_hash: {"role": "primary", "stalled_since": None, "stalled_for": 0}
            },
            "pending_alts": 0,
        }
    else:
        jobs.pop(job_key, None)
    state["jobs"] = jobs
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def dedupe_group(
    *,
    label: str,
    job_key: str,
    media_id: int,
    rows: list[dict],
    tmap: dict[str, dict],
    base: str,
    api_key: str,
) -> tuple[int, int]:
    """Returns (queue_removed, torrents_removed)."""
    if not rows:
        return 0, 0

    now = datetime.now(timezone.utc)
    scored: list[tuple[tuple, dict, str | None]] = []
    for row in rows:
        hid = row.get("downloadId")
        hn = norm_qbit_hash(hid) if hid else None
        t = tmap.get(hn) if hn else None
        scored.append((torrent_score(t), row, hn))

    scored.sort(key=lambda x: x[0], reverse=True)
    _best_score, best_row, best_hash = scored[0]

    q_removed = 0
    t_removed = 0
    remove_hashes: list[str] = []

    for _score, row, hn in scored[1:]:
        if arr_queue_remove(base, api_key, row["id"]):
            q_removed += 1
            log(
                f"{label} {media_id}: removed duplicate queue id={row['id']} ({row.get('title', '')[:60]})",
                detail=True,
            )
        if hn and hn in tmap:
            remove_hashes.append(hn)

    if best_hash is None or best_hash not in tmap:
        added = parse_added(best_row.get("added"))
        if added and (now - added).total_seconds() >= ORPHAN_QUEUE_HOURS * 3600:
            if arr_queue_remove(base, api_key, best_row["id"]):
                q_removed += 1
                log(
                    f"{label} {media_id}: removed orphan queue (no qBit torrent ≥{ORPHAN_QUEUE_HOURS}h)",
                    detail=True,
                )
            reset_stall_state(job_key, None)
            return q_removed, t_removed

    if remove_hashes:
        qbit_delete(remove_hashes, delete_files=True)
        t_removed = len(remove_hashes)
        log(f"{label} {media_id}: removed {t_removed} extra torrent(s) from qBittorrent", detail=True)

    if q_removed or t_removed:
        reset_stall_state(job_key, best_hash if best_hash in tmap else None)

    return q_removed, t_removed


def dedupe_sonarr(api_key: str, tmap: dict[str, dict]) -> tuple[int, int]:
    by_ep: dict[int, list[dict]] = {}
    for row in fetch_arr_queue(SONARR_URL, api_key):
        eid = row.get("episodeId")
        if eid:
            by_ep.setdefault(eid, []).append(row)

    q_total = t_total = 0
    for eid, rows in by_ep.items():
        qr, tr = dedupe_group(
            label="Sonarr episode",
            job_key=f"sonarr:{eid}",
            media_id=eid,
            rows=rows,
            tmap=tmap,
            base=SONARR_URL,
            api_key=api_key,
        )
        q_total += qr
        t_total += tr
    return q_total, t_total


def dedupe_radarr(api_key: str, tmap: dict[str, dict]) -> tuple[int, int]:
    by_movie: dict[int, list[dict]] = {}
    for row in fetch_arr_queue(RADARR_URL, api_key):
        mid = row.get("movieId")
        if mid:
            by_movie.setdefault(mid, []).append(row)

    q_total = t_total = 0
    for mid, rows in by_movie.items():
        qr, tr = dedupe_group(
            label="Radarr movie",
            job_key=f"radarr:{mid}",
            media_id=mid,
            rows=rows,
            tmap=tmap,
            base=RADARR_URL,
            api_key=api_key,
        )
        q_total += qr
        t_total += tr
    return q_total, t_total


def main() -> int:
    try:
        sonarr_key = read_api_key(SONARR_CONFIG)
        radarr_key = read_api_key(RADARR_CONFIG)
    except RuntimeError as e:
        log(f"ERROR: {e}")
        return 1

    try:
        tmap = {norm_qbit_hash(t["hash"]): t for t in qbit_torrents()}
    except urllib.error.URLError as e:
        log(f"qBittorrent unavailable: {e}")
        return 1

    sq, st = dedupe_sonarr(sonarr_key, tmap)
    rq, rt = dedupe_radarr(radarr_key, tmap)
    if sq or st or rq or rt or DEDUPE_VERBOSE:
        log(f"done: sonarr queue-{sq} torrents-{st}; radarr queue-{rq} torrents-{rt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
