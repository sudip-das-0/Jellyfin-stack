"""Parse torrent filenames, stage import-friendly names, import orphans into library."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

from arr_common import MEDIA_ROOT, SCRIPTS_ROOT, SONARR_URL, TEMP_DIR, TORRENTS_ROOT, arr_json, video_container_magic_ok

TORRENTS = TORRENTS_ROOT
MEDIA = MEDIA_ROOT
STAGING = TORRENTS / ".reconcile-staging"
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".wmv", ".ts"}
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


def series_by_title(title: str, api_key: str, cache: dict[str, dict]) -> dict | None:
    key = title.casefold()
    if key in cache:
        return cache[key]
    series_list = arr_json(f"{SONARR_URL}/api/v3/series", api_key)
    if not isinstance(series_list, list):
        return None
    for s in series_list:
        cache[(s.get("title") or "").casefold()] = s
    alias = SERIES_ALIASES.get(key)
    if alias:
        return series_by_title(alias, api_key, cache)
    return cache.get(key)


def episode_for_absolute(series_id: int, abs_num: int, api_key: str) -> dict | None:
    eps = arr_json(f"{SONARR_URL}/api/v3/episode?seriesId={series_id}", api_key)
    if not isinstance(eps, list):
        return None
    for ep in eps:
        if ep.get("absoluteEpisodeNumber") == abs_num:
            return ep
    return None


def episode_for_season_ep(
    series_id: int, season: int, ep: int, api_key: str
) -> dict | None:
    eps = arr_json(f"{SONARR_URL}/api/v3/episode?seriesId={series_id}", api_key)
    if not isinstance(eps, list):
        return None
    for e in eps:
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
        ep = episode_for_season_ep(sid, int(season), int(episodes[0]), api_key)
        if ep:
            return ep
    if absolutes:
        ep = episode_for_absolute(sid, int(absolutes[0]), api_key)
        if ep:
            return ep
    return None


def import_friendly_staging_path(host_file: Path, api_key: str, cache: dict) -> Path | None:
    ep = resolve_episode_from_filename(host_file.name, api_key, cache)
    if not ep or ep.get("hasFile"):
        return None

    sn = int(ep.get("seasonNumber") or 0)
    en = int(ep.get("episodeNumber") or 0)
    series_list = arr_json(f"{SONARR_URL}/api/v3/series", api_key)
    show_title = "Show"
    if isinstance(series_list, list):
        for s in series_list:
            if s.get("id") == ep.get("seriesId"):
                show_title = s.get("title") or show_title
                break

    parsed = sonarr_parse(host_file.name, api_key)
    needs_stage = bool(
        ABS_FANSUB.search(host_file.name)
        or ABS_PADDED_SEASON.search(host_file.name)
        or (parsed and not (parsed.get("episodeNumbers") or []))
    )
    if not needs_stage:
        return None

    STAGING.mkdir(parents=True, exist_ok=True)
    safe_show = re.sub(r"[^\w\s-]", "", show_title).strip().replace(" ", ".")
    staged_name = f"{safe_show}.S{sn:02d}E{en:02d}{host_file.suffix}"
    staged = STAGING / staged_name
    if staged.exists() or staged.is_symlink():
        staged.unlink(missing_ok=True)
    staged.symlink_to(host_file.resolve())
    return staged


def import_orphan_into_library(
    host_file: Path, api_key: str, cache: dict, *, move: bool = True
) -> Path | None:
    """Move/link orphan into series folder and RescanSeries (works when command queue is clear)."""
    if not video_container_magic_ok(host_file):
        return None
    ep = resolve_episode_from_filename(host_file.name, api_key, cache)
    if not ep or ep.get("hasFile"):
        return None
    series_list = arr_json(f"{SONARR_URL}/api/v3/series", api_key)
    series = None
    if isinstance(series_list, list):
        for s in series_list:
            if s.get("id") == ep.get("seriesId"):
                series = s
                break
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
        if dest.stat().st_ino == host_file.stat().st_ino:
            pass
        else:
            return None
    elif move:
        shutil.move(str(host_file), str(dest))
    else:
        shutil.copy2(host_file, dest)

    arr_json(
        f"{SONARR_URL}/api/v3/command",
        api_key,
        method="POST",
        body={"name": "RescanSeries", "seriesId": int(series["id"])},
    )
    for _ in range(60):
        time.sleep(1)
        ep2 = arr_json(f"{SONARR_URL}/api/v3/episode/{ep['id']}", api_key)
        if isinstance(ep2, dict) and ep2.get("hasFile"):
            mp = media_path_from_episode(ep2, api_key)
            return mp
    return None


def queue_lg_optimize(host_media_path: Path) -> None:
    script = SCRIPTS_ROOT / "hooks/lg-tv-queue-path.sh"
    if not script.is_file() or not host_media_path.is_file():
        return
    env = {**subprocess.os.environ, "LG_TV_QUEUE_NO_START": "1"}
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


def refresh_episode(episode_id: int, api_key: str) -> dict | None:
    try:
        return arr_json(f"{SONARR_URL}/api/v3/episode/{episode_id}", api_key)
    except Exception:
        return None
