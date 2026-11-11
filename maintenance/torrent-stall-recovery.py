#!/usr/bin/env python3
"""
Stall recovery for Sonarr/Radarr downloads in qBittorrent.

Rules:
  1. If a torrent has made no download progress for STALL_SECONDS while it holds an active
     qBittorrent download slot (not queued/waiting), grab two alternates.
  2. Among the two alternates, if exactly one is healthy (progressing), remove the other
     torrent jobs from qBit only (files stay on disk).
  3. If both alternates stall for STALL_SECONDS in active slots, remove both from qBit
     (files stay), blocklist, mark failed — use retry-failed-downloads.py to re-search later.

Stall clock uses qBit progress + downloaded bytes (not upload speed). STALL_SECONDS is set
on torrent-stall-recovery.service (43200 = 12 h).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error

sys.path.insert(0, "/mnt/jellyfin/scripts/lib")
from arr_common import (
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    STALL_LOG_DIR,
    STALL_RECOVERY_STATE_PATH,
    STALL_SECONDS_DEFAULT,
    jellyfin_volume_ready,
    qbit_remove_client_only,
    append_recovery_log,
    arr_json,
    arr_queue_remove,
    fetch_arr_queue,
    holds_active_qbit_download_slot,
    norm_qbit_hash,
    qbit_torrents,
    read_api_key,
)

STALL_SECONDS = int(os.environ.get("STALL_SECONDS", str(STALL_SECONDS_DEFAULT)))
LOG_DIR = STALL_LOG_DIR
STATE_PATH = STALL_RECOVERY_STATE_PATH


def log(msg: str) -> None:
    append_recovery_log(msg, tag=None, echo=True)


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"jobs": {}}


def save_state(state: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def torrent_by_hash(torrents: list[dict]) -> dict[str, dict]:
    return {norm_qbit_hash(t["hash"]): t for t in torrents}


def progress_key(t: dict) -> tuple[float, int]:
    """Download progress only — upload speed/bytes must not reset the stall timer."""
    return (round(t.get("progress", 0), 4), int(t.get("downloaded") or 0))


def update_stall_tracking(track: dict, t: dict | None, now: float) -> bool:
    """Update per-hash stall tracking. Returns True if stalled >= STALL_SECONDS."""
    if t is None:
        return track.get("stalled_for", 0) >= STALL_SECONDS

    if not holds_active_qbit_download_slot(t):
        track["last_progress_key"] = None
        track["stalled_since"] = None
        track["stalled_for"] = 0
        return False

    pk = progress_key(t)
    last = track.get("last_progress_key")
    if last != pk:
        track["last_progress_key"] = pk
        track["stalled_since"] = None
        track["stalled_for"] = 0
        return False

    if track.get("stalled_since") is None:
        if t.get("state") == "stalledDL" and t.get("progress", 0) < 0.01:
            track["stalled_since"] = float(t.get("last_activity") or t.get("added_on") or now)
        else:
            track["stalled_since"] = now
    stalled_for = now - track["stalled_since"]
    track["stalled_for"] = stalled_for
    return stalled_for >= STALL_SECONDS


def is_healthy(track: dict, t: dict | None, now: float) -> bool:
    if t is None:
        return False
    if t.get("progress", 0) >= 1.0:
        return True
    update_stall_tracking(track, t, now)
    return track.get("stalled_for", 0) < STALL_SECONDS and (
        t.get("dlspeed", 0) > 0
        or t.get("state") in ("downloading", "metaDL", "forcedDL", "checkingDL")
    )


def sonarr_grab_release(api_key: str, release: dict, episode_id: int) -> None:
    payload = dict(release)
    payload["episodeIds"] = [episode_id]
    arr_json(f"{SONARR_URL}/api/v3/release", api_key, method="POST", body=payload)


def radarr_grab_release(api_key: str, release: dict, movie_id: int) -> None:
    payload = dict(release)
    payload["movieIds"] = [movie_id]
    arr_json(f"{RADARR_URL}/api/v3/release", api_key, method="POST", body=payload)


def fail_queue_items(base_url: str, api_key: str, queue_ids: list[int], label: str) -> None:
    for qid in queue_ids:
        try:
            if not arr_queue_remove(
                base_url, api_key, qid, blocklist=True, skip_redownload=True
            ):
                log(f"{label} queue delete {qid}: not found")
        except urllib.error.HTTPError as e:
            log(f"{label} queue delete {qid}: HTTP {e.code}")


def pick_alternate_releases(
    releases: list[dict], tried_guids: set[str], exclude_hashes: set[str], count: int = 2
) -> list[dict]:
    eligible = []
    for r in releases:
        guid = r.get("guid") or ""
        if guid in tried_guids:
            continue
        if r.get("rejected"):
            continue
        eligible.append(r)
    eligible.sort(key=lambda x: (-(x.get("seeders") or 0), -(x.get("size") or 0)))
    return eligible[:count]


def reset_failed_if_new_grab(
    state: dict,
    job_key: str,
    job: dict,
    current_hashes: list[str],
    label: str,
    media_id: int,
) -> dict:
    if job.get("phase") != "failed":
        return job
    failed = {norm_qbit_hash(h) for h in (job.get("failed_hashes") or [])}
    cur = {norm_qbit_hash(h) for h in current_hashes}
    if cur and cur.isdisjoint(failed):
        log(f"{label} {media_id}: new torrent after fail — resetting stall recovery")
        state.setdefault("jobs", {}).pop(job_key, None)
        return ensure_job_tracks(state, job_key, current_hashes)
    return job


def prune_stale_jobs(state: dict, active_keys: set[str]) -> None:
    jobs = state.get("jobs") or {}
    for key in list(jobs.keys()):
        if key in active_keys:
            continue
        if jobs[key].get("phase") in ("done", "failed"):
            jobs.pop(key, None)


def active_queue_job_keys(sonarr_key: str, radarr_key: str) -> set[str]:
    keys: set[str] = set()
    for row in fetch_arr_queue(SONARR_URL, sonarr_key):
        eid = row.get("episodeId")
        if eid:
            keys.add(f"sonarr:{eid}")
    for row in fetch_arr_queue(RADARR_URL, radarr_key):
        mid = row.get("movieId")
        if mid:
            keys.add(f"radarr:{mid}")
    return keys


def ensure_job_tracks(state: dict, job_key: str, hashes: list[str]) -> dict:
    jobs = state.setdefault("jobs", {})
    job = jobs.setdefault(
        job_key,
        {
            "phase": "primary",
            "tried_guids": [],
            "queue_ids": [],
            "hashes": {},
            "pending_alts": 0,
        },
    )
    for h in hashes:
        hn = norm_qbit_hash(h)
        job["hashes"].setdefault(hn, {"role": "primary", "stalled_since": None, "stalled_for": 0})
    return job


def process_sonarr(
    api_key: str, torrents: list[dict], tmap: dict[str, dict], state: dict, now: float
) -> None:
    by_episode: dict[int, list[dict]] = {}
    hash_episode_ids: dict[str, list[int]] = {}
    for row in fetch_arr_queue(SONARR_URL, api_key):
        eid = row.get("episodeId")
        if not eid:
            continue
        by_episode.setdefault(eid, []).append(row)
        did = row.get("downloadId")
        if did:
            hash_episode_ids.setdefault(norm_qbit_hash(did), []).append(int(eid))

    for episode_id, rows in by_episode.items():
        job_key = f"sonarr:{episode_id}"
        hashes = [norm_qbit_hash(r["downloadId"]) for r in rows if r.get("downloadId")]
        if not hashes:
            continue
        if len(hashes) == 1:
            shared = hash_episode_ids.get(hashes[0]) or []
            if len(shared) > 1 and episode_id != min(shared):
                # One season/multi-episode torrent — stall recovery once per hash.
                continue
        job = ensure_job_tracks(state, job_key, hashes)
        job["queue_ids"] = sorted({r["id"] for r in rows})
        tried = set(job.get("tried_guids") or [])

        current_hashes = [norm_qbit_hash(r["downloadId"]) for r in rows if r.get("downloadId")]

        for hn in list(job["hashes"].keys()):
            track = job["hashes"][hn]
            update_stall_tracking(track, tmap.get(hn), now)

        job = reset_failed_if_new_grab(
            state, job_key, job, current_hashes, "Sonarr episode", episode_id
        )
        if job.get("phase") == "failed":
            continue

        if job.get("phase") == "primary":
            if len(current_hashes) > 1:
                continue  # queue-dedupe.py must trim to one torrent first

            primary_hashes = [h for h, tr in job["hashes"].items() if tr.get("role") == "primary"]
            stalled_primaries = [
                h for h in primary_hashes if job["hashes"][h].get("stalled_for", 0) >= STALL_SECONDS
            ]
            if not stalled_primaries or job.get("pending_alts"):
                continue
            if len(stalled_primaries) != 1:
                continue

            log(f"Sonarr episode {episode_id}: primary stalled ≥{STALL_SECONDS}s, fetching 2 alternates")
            try:
                releases = arr_json(
                    f"{SONARR_URL}/api/v3/release?episodeId={episode_id}",
                    api_key,
                )
            except urllib.error.HTTPError as e:
                log(f"Sonarr episode {episode_id}: release search failed HTTP {e.code}")
                continue
            if not isinstance(releases, list):
                continue

            exclude = set(hashes)
            alts = pick_alternate_releases(releases, tried, exclude, 2)
            if len(alts) < 1:
                log(f"Sonarr episode {episode_id}: no alternate releases found")
                fail_queue_items(SONARR_URL, api_key, job["queue_ids"], "Sonarr")
                job["phase"] = "failed"
                job["failed_hashes"] = list(current_hashes)
                continue

            for rel in alts:
                tried.add(rel["guid"])
                try:
                    sonarr_grab_release(api_key, rel, episode_id)
                    log(f"Sonarr episode {episode_id}: grabbed alt '{rel.get('title', '')[:80]}' seeders={rel.get('seeders')}")
                except urllib.error.HTTPError as e:
                    log(f"Sonarr episode {episode_id}: grab failed HTTP {e.code}")

            job["tried_guids"] = list(tried)
            job["pending_alts"] = len(alts)
            job["phase"] = "alternates"
            qbit_remove_client_only(stalled_primaries)
            for h in stalled_primaries:
                job["hashes"].pop(h, None)
            continue

        if job.get("phase") == "alternates":
            alt_hashes = [h for h, tr in job["hashes"].items() if tr.get("role") == "alternate"]
            # Attach newly appeared torrents for this episode from queue
            for hn in current_hashes:
                if hn not in job["hashes"]:
                    job["hashes"][hn] = {
                        "role": "alternate",
                        "stalled_since": None,
                        "stalled_for": 0,
                    }
                    alt_hashes.append(hn)
                elif job["hashes"][hn].get("role") == "primary":
                    job["hashes"][hn]["role"] = "alternate"
                    alt_hashes.append(hn)

            if job.get("pending_alts") and len(alt_hashes) < job["pending_alts"]:
                # Wait until qBittorrent picks up the new jobs
                continue
            job["pending_alts"] = 0

            if len(alt_hashes) < 1:
                continue
            if len(current_hashes) > 2:
                continue

            tracks = {h: job["hashes"][h] for h in alt_hashes}
            health = {h: is_healthy(tracks[h], tmap.get(h), now) for h in alt_hashes}
            stalled_alts = [
                h for h in alt_hashes if tracks[h].get("stalled_for", 0) >= STALL_SECONDS
            ]

            healthy = [h for h, ok in health.items() if ok]
            if len(healthy) >= 1 and len(alt_hashes) >= 2:
                keep = max(healthy, key=lambda h: tmap.get(h, {}).get("progress", 0))
                remove = [h for h in current_hashes if h != keep]
                log(
                    f"Sonarr episode {episode_id}: keeping one healthy torrent, "
                    f"removing {len(remove)} other torrent(s)"
                )
                qbit_remove_client_only(remove)
                job["hashes"] = {keep: job["hashes"].get(keep, {"role": "alternate"})}
                job["phase"] = "done"
                continue

            if len(alt_hashes) >= 2 and len(stalled_alts) >= 2 and all(
                tracks[h].get("stalled_for", 0) >= STALL_SECONDS for h in alt_hashes[:2]
            ):
                log(f"Sonarr episode {episode_id}: both alternates stalled ≥{STALL_SECONDS}s — marking failed")
                qbit_remove_client_only(alt_hashes)
                fail_queue_items(SONARR_URL, api_key, job["queue_ids"], "Sonarr")
                job["phase"] = "failed"
                job["failed_hashes"] = list(current_hashes)
                job["hashes"] = {}


def process_radarr(
    api_key: str, torrents: list[dict], tmap: dict[str, dict], state: dict, now: float
) -> None:
    by_movie: dict[int, list[dict]] = {}
    for row in fetch_arr_queue(RADARR_URL, api_key):
        mid = row.get("movieId")
        if not mid:
            continue
        by_movie.setdefault(mid, []).append(row)

    for movie_id, rows in by_movie.items():
        job_key = f"radarr:{movie_id}"
        hashes = [norm_qbit_hash(r["downloadId"]) for r in rows if r.get("downloadId")]
        if not hashes:
            continue
        job = ensure_job_tracks(state, job_key, hashes)
        job["queue_ids"] = sorted({r["id"] for r in rows})
        tried = set(job.get("tried_guids") or [])

        current_hashes = [norm_qbit_hash(r["downloadId"]) for r in rows if r.get("downloadId")]

        for hn in list(job["hashes"].keys()):
            track = job["hashes"][hn]
            update_stall_tracking(track, tmap.get(hn), now)

        job = reset_failed_if_new_grab(
            state, job_key, job, current_hashes, "Radarr movie", movie_id
        )
        if job.get("phase") == "failed":
            continue

        if job.get("phase") == "primary":
            if len(current_hashes) > 1:
                continue

            primary_hashes = [h for h, tr in job["hashes"].items() if tr.get("role") == "primary"]
            stalled_primaries = [
                h for h in primary_hashes if job["hashes"][h].get("stalled_for", 0) >= STALL_SECONDS
            ]
            if not stalled_primaries or job.get("pending_alts"):
                continue
            if len(stalled_primaries) != 1:
                continue

            log(f"Radarr movie {movie_id}: primary stalled ≥{STALL_SECONDS}s, fetching 2 alternates")
            try:
                releases = arr_json(
                    f"{RADARR_URL}/api/v3/release?movieId={movie_id}",
                    api_key,
                )
            except urllib.error.HTTPError as e:
                log(f"Radarr movie {movie_id}: release search failed HTTP {e.code}")
                continue
            if not isinstance(releases, list):
                continue

            alts = pick_alternate_releases(releases, tried, set(hashes), 2)
            if len(alts) < 1:
                log(f"Radarr movie {movie_id}: no alternate releases found")
                fail_queue_items(RADARR_URL, api_key, job["queue_ids"], "Radarr")
                job["phase"] = "failed"
                job["failed_hashes"] = list(current_hashes)
                continue

            for rel in alts:
                tried.add(rel["guid"])
                try:
                    radarr_grab_release(api_key, rel, movie_id)
                    log(f"Radarr movie {movie_id}: grabbed alt '{rel.get('title', '')[:80]}' seeders={rel.get('seeders')}")
                except urllib.error.HTTPError as e:
                    log(f"Radarr movie {movie_id}: grab failed HTTP {e.code}")

            job["tried_guids"] = list(tried)
            job["pending_alts"] = len(alts)
            job["phase"] = "alternates"
            qbit_remove_client_only(stalled_primaries)
            for h in stalled_primaries:
                job["hashes"].pop(h, None)
            continue

        if job.get("phase") == "alternates":
            alt_hashes = [h for h, tr in job["hashes"].items() if tr.get("role") == "alternate"]
            for hn in current_hashes:
                if hn not in job["hashes"]:
                    job["hashes"][hn] = {
                        "role": "alternate",
                        "stalled_since": None,
                        "stalled_for": 0,
                    }
                    alt_hashes.append(hn)
                elif job["hashes"][hn].get("role") == "primary":
                    job["hashes"][hn]["role"] = "alternate"
                    alt_hashes.append(hn)

            if job.get("pending_alts") and len(alt_hashes) < job["pending_alts"]:
                continue
            job["pending_alts"] = 0

            if len(alt_hashes) < 1:
                continue
            if len(current_hashes) > 2:
                continue

            tracks = {h: job["hashes"][h] for h in alt_hashes}
            health = {h: is_healthy(tracks[h], tmap.get(h), now) for h in alt_hashes}
            stalled_alts = [
                h for h in alt_hashes if tracks[h].get("stalled_for", 0) >= STALL_SECONDS
            ]

            healthy = [h for h, ok in health.items() if ok]
            if len(healthy) >= 1 and len(alt_hashes) >= 2:
                keep = max(healthy, key=lambda h: tmap.get(h, {}).get("progress", 0))
                remove = [h for h in current_hashes if h != keep]
                log(
                    f"Radarr movie {movie_id}: keeping one healthy torrent, "
                    f"removing {len(remove)} other torrent(s)"
                )
                qbit_remove_client_only(remove)
                job["hashes"] = {keep: job["hashes"].get(keep, {"role": "alternate"})}
                job["phase"] = "done"
                continue

            if len(alt_hashes) >= 2 and len(stalled_alts) >= 2 and all(
                tracks[h].get("stalled_for", 0) >= STALL_SECONDS for h in alt_hashes[:2]
            ):
                log(f"Radarr movie {movie_id}: both alternates stalled ≥{STALL_SECONDS}s — marking failed")
                qbit_remove_client_only(alt_hashes)
                fail_queue_items(RADARR_URL, api_key, job["queue_ids"], "Radarr")
                job["phase"] = "failed"
                job["failed_hashes"] = list(current_hashes)
                job["hashes"] = {}


def main() -> int:
    if not jellyfin_volume_ready():
        log("skipped — jellyfin SSD not ready (drive guard)")
        return 0
    try:
        sonarr_key = read_api_key(SONARR_CONFIG)
        radarr_key = read_api_key(RADARR_CONFIG)
    except RuntimeError as e:
        log(f"ERROR: {e}")
        return 1

    state = load_state()
    now = time.time()
    try:
        torrents = qbit_torrents()
    except urllib.error.URLError as e:
        log(f"qBittorrent unavailable: {e}")
        return 1

    tmap = torrent_by_hash(torrents)
    process_sonarr(sonarr_key, torrents, tmap, state, now)
    process_radarr(radarr_key, torrents, tmap, state, now)
    active = active_queue_job_keys(sonarr_key, radarr_key)
    prune_stale_jobs(state, active)
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
