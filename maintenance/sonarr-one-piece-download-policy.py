#!/usr/bin/env python3
"""One Piece: avoid duplicate/wasted Netflix KQRM season packs; prefer per-episode imports.

- Ensures Sonarr rejects multi-episode NF WEB-DL season-pack titles (KQRM-style).
- Optionally queues SeasonSearch for early monitored seasons without library files.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, "/mnt/jellyfin/scripts/lib")
from arr_common import (
    SONARR_CONFIG,
    SONARR_URL,
    STALL_LOG_DIR,
    append_recovery_log,
    arr_json,
    queue_arr_command,
    read_api_key,
)

PROFILE_NAME = "One Piece — reject KQRM Netflix season packs"
# Season-batch torrent titles only (not One.Piece.S004E093 per-episode files).
IGNORED_TERMS = [
    "One Piece.S01.1080p.NF.WEB-DL",
    "One Piece.S02.1080p.NF.WEB-DL",
    "One Piece.S03.1080p.NF.WEB-DL",
    "One Piece.S04.1080p.NF.WEB-DL",
    "One Piece.S05.1080p.NF.WEB-DL",
    "One Piece.S06.1080p.NF.WEB-DL",
    "One Piece.S07.1080p.NF.WEB-DL",
    "One Piece.S08.1080p.NF.WEB-DL",
    "One Piece.S09.1080p.NF.WEB-DL",
    "One Piece.S10.1080p.NF.WEB-DL",
    "One Piece.S11.1080p.NF.WEB-DL",
    "One Piece.S12.1080p.NF.WEB-DL",
    "One Piece.S13.1080p.NF.WEB-DL",
    "One Piece.S14.1080p.NF.WEB-DL",
    "One Piece.S15.1080p.NF.WEB-DL",
    "One Piece.S16.1080p.NF.WEB-DL",
    "One Piece.S17.1080p.NF.WEB-DL",
    "One Piece.S18.1080p.NF.WEB-DL",
    "One Piece.S19.1080p.NF.WEB-DL",
    "One Piece.S20.1080p.NF.WEB-DL",
    "One Piece.S21.1080p.NF.WEB-DL",
]
# Monitored TVDB seasons to fill with per-episode grabs (no season packs).
DEFAULT_SEASON_SEARCH = tuple(int(x) for x in os.environ.get("ONEPIECE_SEASON_SEARCH", "1,2,3,4").split(",") if x.strip())
EPISODE_SEARCH_PER_SEASON = int(os.environ.get("ONEPIECE_EPISODE_SEARCH_PER_SEASON", "3"))
KQRM_SEASON_PACK_RE = re.compile(r"One Piece\.S\d{2}\.1080p\.NF\.WEB-DL", re.I)


def purge_kqrm_season_packs(api_key: str) -> str:
    """Drop any active KQRM Netflix season-pack rows (duplicate/wrong import path)."""
    from arr_common import fetch_arr_queue, norm_qbit_hash, qbit_delete

    rows = fetch_arr_queue(SONARR_URL, api_key)
    targets = [r for r in rows if KQRM_SEASON_PACK_RE.search(r.get("title") or "")]
    if not targets:
        return "no KQRM season packs in queue"

    ids = [int(r["id"]) for r in targets if r.get("id")]
    hashes = sorted({norm_qbit_hash(r["downloadId"]) for r in targets if r.get("downloadId")})

    qs = urllib.parse.urlencode(
        {
            "removeFromClient": "true",
            "blocklist": "true",
            "skipRedownload": "true",
        }
    )
    for i in range(0, len(ids), 40):
        batch = ids[i : i + 40]
        arr_json(
            f"{SONARR_URL}/api/v3/queue/bulk?{qs}",
            api_key,
            method="DELETE",
            body={"ids": batch},
        )
    if hashes:
        qbit_delete(hashes, delete_files=True)
    return f"purged {len(ids)} queue row(s), {len(hashes)} torrent(s)"


def ensure_release_profile(api_key: str) -> str:
    profiles = arr_json(f"{SONARR_URL}/api/v3/releaseprofile", api_key)
    if not isinstance(profiles, list):
        raise RuntimeError("unexpected releaseprofile response")

    for p in profiles:
        if (p.get("name") or "") == PROFILE_NAME:
            return f"release profile already present (id={p.get('id')})"

    body = {
        "enabled": True,
        "required": [],
        "ignored": IGNORED_TERMS,
        "indexerId": 0,
        "tags": [],
        "name": PROFILE_NAME,
    }
    result = arr_json(f"{SONARR_URL}/api/v3/releaseprofile", api_key, method="POST", body=body)
    rid = result.get("id") if isinstance(result, dict) else "?"
    return f"created release profile id={rid} ({len(IGNORED_TERMS)} ignored patterns)"


def one_piece_series_id(api_key: str) -> int | None:
    series_list = arr_json(f"{SONARR_URL}/api/v3/series", api_key)
    if not isinstance(series_list, list):
        return None
    for s in series_list:
        if (s.get("title") or "").casefold() == "one piece":
            return int(s["id"])
    return None


def season_needs_search(api_key: str, series_id: int, season_number: int) -> bool:
    detail = arr_json(f"{SONARR_URL}/api/v3/series/{series_id}", api_key)
    if not isinstance(detail, dict):
        return False
    for season in detail.get("seasons") or []:
        if season.get("seasonNumber") != season_number:
            continue
        if not season.get("monitored"):
            return False
        stats = season.get("statistics") or {}
        total = int(stats.get("totalEpisodeCount") or 0)
        have = int(stats.get("episodeFileCount") or 0)
        return total > 0 and have < total
    return False


def queue_episode_searches(
    api_key: str, *, series_id: int, seasons: tuple[int, ...], per_season: int
) -> list[str]:
    """EpisodeSearch for missing eps only — avoids SeasonSearch grabbing NF season packs."""
    out: list[str] = []
    for sn in seasons:
        if not season_needs_search(api_key, series_id, sn):
            continue
        eps = arr_json(
            f"{SONARR_URL}/api/v3/episode?seriesId={series_id}&seasonNumber={sn}",
            api_key,
        )
        if not isinstance(eps, list):
            continue
        missing = [e for e in eps if not e.get("hasFile")]
        missing.sort(key=lambda e: int(e.get("episodeNumber") or 0))
        for ep in missing[:per_season]:
            eid = int(ep["id"])
            cmd = queue_arr_command(
                SONARR_URL,
                api_key,
                "EpisodeSearch",
                episodeId=eid,
            )
            out.append(
                f"EpisodeSearch S{sn:02d}E{int(ep.get('episodeNumber') or 0):02d} "
                f"id={cmd.get('id')} status={cmd.get('status')}"
            )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--search-seasons",
        action="store_true",
        help="Queue EpisodeSearch (per-episode) for incomplete monitored seasons",
    )
    parser.add_argument("--no-profile", action="store_true", help="Skip release profile ensure")
    args = parser.parse_args()

    try:
        api_key = read_api_key(SONARR_CONFIG)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    messages: list[str] = []
    try:
        messages.append(purge_kqrm_season_packs(api_key))
        if not args.no_profile:
            messages.append(ensure_release_profile(api_key))

        if args.search_seasons:
            sid = one_piece_series_id(api_key)
            if sid is None:
                messages.append("One Piece series not found — skip season search")
            else:
                messages.extend(
                    queue_episode_searches(
                        api_key,
                        series_id=sid,
                        seasons=DEFAULT_SEASON_SEARCH,
                        per_season=EPISODE_SEARCH_PER_SEASON,
                    )
                )
    except urllib.error.URLError as e:
        print(f"Sonarr unavailable: {e}", file=sys.stderr)
        return 1

    if messages:
        line = "one-piece-policy: " + "; ".join(messages)
        append_recovery_log(line, tag="sonarr")
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
