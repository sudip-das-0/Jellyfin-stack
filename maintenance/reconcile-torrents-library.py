#!/usr/bin/env python3
"""Reconcile /mnt/jellyfin/torrents: drop library duplicates, import orphans, queue LG."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from arr_common import (
    HOOKS_DIR,
    MEDIA_ROOT,
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    STALL_LOG_DIR,
    append_recovery_log,
    arr_json,
    echo_script_log,
    read_api_key,
)
from orphan_import_helpers import (
    STAGING,
    TORRENTS,
    VIDEO_EXT,
    import_orphan_into_library,
    resolve_episode_from_filename,
)

LOG_FILE = STALL_LOG_DIR / "orphan-import.log"
IMPORT_LIMIT = int(os.environ.get("RECONCILE_IMPORT_LIMIT", "15"))


def log(msg: str) -> None:
    echo_script_log(LOG_FILE, msg, rotate=True)


def iter_torrent_videos() -> list[Path]:
    out: list[Path] = []
    if not TORRENTS.is_dir():
        return out
    skip_parts = {"incomplete", ".reconcile-staging"}
    for root, _dirs, files in os.walk(TORRENTS):
        root_path = Path(root)
        if skip_parts.intersection(root_path.parts):
            continue
        for name in files:
            p = root_path / name
            if p.suffix.lower() in VIDEO_EXT:
                out.append(p)
    return sorted(out, key=lambda p: (len(p.relative_to(TORRENTS).parts), str(p).casefold()))


def post_import_hooks(media_path: Path) -> None:
    rel = media_path.relative_to(MEDIA_ROOT)
    data_path = f"/data/media/{rel.as_posix()}"
    hooks = HOOKS_DIR
    env = {**os.environ, "sonarr_episodefile_path": data_path}
    sidecar = hooks / "import-sidecar-subs.sh"
    if sidecar.is_file():
        subprocess.run([str(sidecar)], env=env, check=False)
    queue_lg_optimize(media_path)
    subprocess.run(
        ["systemctl", "--user", "start", "--no-block", "lg-tv-optimize.service"],
        check=False,
    )


def try_import_file(
    host_file: Path,
    sonarr_key: str,
    radarr_key: str,
    cache: dict,
    dry_run: bool,
) -> str:
    """Returns: duplicate | imported | failed | dry-run"""
    ep = resolve_episode_from_filename(host_file.name, sonarr_key, cache)
    if ep and ep.get("hasFile"):
        return "duplicate"

    if dry_run:
        return "dry-run"

    mp = import_orphan_into_library(host_file, sonarr_key, cache, move=True)
    if mp:
        post_import_hooks(mp)
        return "imported"

    # Radarr: folder scan for loose movie files
    from orphan_import_helpers import docker_torrent_path

    body = {
        "name": "DownloadedMoviesScan",
        "path": docker_torrent_path(host_file.parent),
    }
    created = arr_json(f"{RADARR_URL}/api/v3/command", radarr_key, method="POST", body=body)
    if isinstance(created, dict) and created.get("id"):
        return "imported"

    return "failed"


def run_duplicate_cleanup(dry_run: bool) -> int:
    script = Path(__file__).resolve().parent / "cleanup-torrent-library-orphans.py"
    cmd = [sys.executable, str(script)]
    if dry_run:
        cmd.append("--dry-run")
    r = subprocess.run(cmd, check=False)
    return r.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-dedupe-cleanup", action="store_true")
    parser.add_argument("--limit", type=int, default=IMPORT_LIMIT)
    args = parser.parse_args()

    if not args.skip_dedupe_cleanup:
        run_duplicate_cleanup(args.dry_run)

    sonarr_key = read_api_key(SONARR_CONFIG)
    radarr_key = read_api_key(RADARR_CONFIG)
    cache: dict = {}
    stats: dict[str, int] = {}

    attempts = 0
    for path in iter_torrent_videos():
        if attempts >= args.limit:
            break
        try:
            result = try_import_file(
                path, sonarr_key, radarr_key, cache, args.dry_run
            )
        except urllib.error.URLError as e:
            log(f"API error for {path.name}: {e}")
            result = "failed"
        stats[result] = stats.get(result, 0) + 1
        if result in ("imported", "failed", "dry-run"):
            attempts += 1
            log(f"{result}: {path}")
        if result == "duplicate":
            continue

    append_recovery_log(
        f"reconcile: {stats} limit={args.limit}",
        tag="orphan-import",
    )
    if STAGING.is_dir() and not args.dry_run:
        for p in STAGING.iterdir():
            p.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
