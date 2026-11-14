"""Parse torrent filenames, stage import-friendly names, import orphans into library."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from arr_common import (
    MEDIA_ROOT,
    SCRIPTS_ROOT,
    SONARR_URL,
    TEMP_DIR,
    TORRENTS_ROOT,
    VIDEO_EXTENSIONS,
    arr_json,
    video_container_magic_ok,
)

TORRENTS = TORRENTS_ROOT
MEDIA = MEDIA_ROOT
STAGING = TORRENTS / ".reconcile-staging"
VIDEO_EXT = VIDEO_EXTENSIONS  # kept for reconcile-torrents-library imports
EP_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})")
# [Group] Title - 123 [1080p] — fansub absolute episode
ABS_FANSUB = re.compile(r"\[[^\]]+\]\s*(.+?)\s*-\s*(\d{2,4})\s*\[", re.I)
# Title.S004E093 — padded season, E-field is often an absolute episode
ABS_PADDED_SEASON = re.compile(r"(?<![0-9])S\d{3}E(\d{2,3})(?![0-9])", re.I)
ALIASES_PATH = TEMP_DIR / "series-aliases.json"


def load_series_aliases() -> dict[str, str]:
    if not ALIASES_PATH.is_file():
        return {}
    try:
        raw = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k).casefold(): str(v) for k, v in raw.items() if k and v}


SERIES_ALIASES = load_series_aliases()


def docker_torrent_path(host_path: Path) -> str:
    return "/data/torrents" + str(host_path)[len(str(TORRENTS)) :].replace("\\", "/")


def host_media_path(docker_path: str) -> Path:
    if docker_path.startswith("/data/media/"):
        return MEDIA / docker_path[len("/data/media/") :]
    return Path(docker_path)


def sonarr_parse(title: str, api_key: str) -> dict | None:
    q = urllib.parse.quote(title, safe="")
    try:
        data = arr_json(f"{SONARR_URL}/api/v3/parse?title={q}", api_key)
    except Exception:
        return None
    if isinstance(data, dict):
        return data.get("parsedEpisodeInfo") or None
    return None


# Per-run cache shared by callers (they only pass an empty dict through). Reserved keys
# start with NUL so they can never collide with the casefolded series titles stored beside them.
_K_SERIES = "\0series"
_K_BY_ID = "\0by_id"


def _load_series(api_key: str, cache: dict) -> list[dict]:
    """Fetch /series once per cache and index it by title and id."""
    if _K_SERIES not in cache:
        data = arr_json(f"{SONARR_URL}/api/v3/series", api_key)
        series_list = [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []
        cache[_K_SERIES] = series_list
        cache[_K_BY_ID] = {s.get("id"): s for s in series_list}
        for s in series_list:
            cache.setdefault((s.get("title") or "").casefold(), s)
    return cache[_K_SERIES]


def series_by_id(series_id: int, api_key: str, cache: dict) -> dict | None:
    _load_series(api_key, cache)
    return cache[_K_BY_ID].get(series_id)


def series_by_title(title: str, api_key: str, cache: dict) -> dict | None:
    _load_series(api_key, cache)
    key = title.casefold()
    found = cache.get(key)
    if found is None:
        alias = SERIES_ALIASES.get(key)
        if alias and alias.casefold() != key:
            found = cache.get(alias.casefold())
    return found


def _episodes(series_id: int, api_key: str, cache: dict) -> list[dict]:
    key = f"\0eps:{series_id}"
    if key not in cache:
        eps = arr_json(f"{SONARR_URL}/api/v3/episode?seriesId={series_id}", api_key)
        cache[key] = eps if isinstance(eps, list) else []
    return cache[key]


def episode_for_absolute(series_id: int, abs_num: int, api_key: str, cache: dict | None = None) -> dict | None:
    for ep in _episodes(series_id, api_key, {} if cache is None else cache):
        if ep.get("absoluteEpisodeNumber") == abs_num:
            return ep
    return None


def episode_for_season_ep(
    series_id: int, season: int, ep: int, api_key: str, cache: dict | None = None
) -> dict | None:
    for e in _episodes(series_id, api_key, {} if cache is None else cache):
        if e.get("seasonNumber") == season and e.get("episodeNumber") == ep:
            return e
    return None


def resolve_episode_from_filename(name: str, api_key: str, cache: dict) -> dict | None:
    parsed = sonarr_parse(name, api_key)
    series_title = None
    season = None
    episodes: list[int] = []
    absolutes: list[int] = []

    if parsed:
        series_title = (parsed.get("seriesTitle") or "").strip()
        season = parsed.get("seasonNumber")
        episodes = list(parsed.get("episodeNumbers") or [])
        absolutes = list(parsed.get("absoluteEpisodeNumbers") or [])

    m = ABS_FANSUB.search(name)
    if m:
        series_title = series_title or m.group(1).strip()
        if not absolutes:
            absolutes = [int(m.group(2))]

    m = ABS_PADDED_SEASON.search(name)
    if m and not absolutes:
        absolutes = [int(m.group(1))]

    if not series_title:
        return None

    series = series_by_title(series_title, api_key, cache)
    if not series:
        return None

    sid = int(series["id"])
    if episodes and season and int(season) > 0:
        ep = episode_for_season_ep(sid, int(season), int(episodes[0]), api_key, cache)
        if ep:
            return ep
    if absolutes:
        ep = episode_for_absolute(sid, int(absolutes[0]), api_key, cache)
        if ep:
            return ep
    return None


def import_orphan_into_library(
    host_file: Path, api_key: str, cache: dict, *, move: bool = True
) -> Path | None:
    """Move/link orphan into series folder and RescanSeries (works when command queue is clear)."""
    if not video_container_magic_ok(host_file):
        return None
    ep = resolve_episode_from_filename(host_file.name, api_key, cache)
    if not ep or ep.get("hasFile"):
        return None
    series = series_by_id(ep.get("seriesId"), api_key, cache)
    if not series:
        return None

    dest_dir = host_media_path(series.get("path") or "")
    sn = int(ep.get("seasonNumber") or 0)
    if series.get("seasonFolder"):
        dest_dir = dest_dir / f"Season {sn}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / host_file.name
    if dest.resolve() == host_file.resolve():
        pass
    elif dest.exists():
        if dest.stat().st_ino != host_file.stat().st_ino:
            sys.stderr.write(
                f"orphan-import: {dest} already exists and differs from {host_file}; not importing\n"
            )
            return None
    elif move:
        shutil.move(str(host_file), str(dest))
    else:
        shutil.copy2(host_file, dest)

    cmd = arr_json(
        f"{SONARR_URL}/api/v3/command",
        api_key,
        method="POST",
        body={"name": "RescanSeries", "seriesId": int(series["id"])},
    )
    cache.pop(f"\0eps:{series['id']}", None)  # hasFile flips after the rescan
    _wait_for_command(cmd, api_key)
    # The command is done (or timed out); the episode file row may land a moment later.
    for attempt in range(6):
        ep2 = arr_json(f"{SONARR_URL}/api/v3/episode/{ep['id']}", api_key)
        if isinstance(ep2, dict) and ep2.get("hasFile"):
            return media_path_from_episode(ep2, api_key)
        time.sleep(1)
    return None


def _wait_for_command(cmd: object, api_key: str, timeout: float = 60.0) -> None:
    """Poll a Sonarr command until it finishes; returns early when the id is unknown."""
    cmd_id = cmd.get("id") if isinstance(cmd, dict) else None
    if cmd_id is None:
        time.sleep(2)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = arr_json(f"{SONARR_URL}/api/v3/command/{cmd_id}", api_key)
        if isinstance(state, dict) and state.get("status") in {"completed", "failed", "aborted", "cancelled", "orphaned"}:
            return
        time.sleep(0.5)


def queue_lg_optimize(host_media_path: Path) -> None:
    if os.environ.get("LG_TV_OPTIMIZE_ENABLED", "0") != "1":
        return
    script = SCRIPTS_ROOT / "hooks/lg-tv-queue-path.sh"
    if not script.is_file() or not host_media_path.is_file():
        return
    env = {**os.environ, "LG_TV_QUEUE_NO_START": "1"}
    subprocess.run([str(script), str(host_media_path)], check=False, env=env)


def media_path_from_episode(ep: dict, api_key: str) -> Path | None:
    if not ep.get("hasFile"):
        return None
    eid = ep.get("id")
    if eid is None:
        return None
    files = arr_json(f"{SONARR_URL}/api/v3/episodefile?episodeId={eid}", api_key)
    if not isinstance(files, list) or not files:
        return None
    raw = files[0].get("path") or ""
    return host_media_path(raw)
