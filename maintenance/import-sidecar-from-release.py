#!/usr/bin/env python3
"""Host-side import helper (same rules as hooks/import-sidecar-subs.sh)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, "/mnt/jellyfin/scripts/lib")

import sidecar_release as sr  # noqa: E402


def release_search_dirs() -> list[Path]:
    keys = (
        "sonarr_episodefile_sourcefolder",
        "sonarr_sourcefolder",
        "radarr_moviefile_sourcefolder",
        "radarr_sourcefolder",
    )
    dirs: list[Path] = []
    for key in keys:
        raw = os.environ.get(key, "").strip()
        if raw:
            dirs.append(Path(raw))
    return dirs


def main() -> int:
    dest = os.environ.get("sonarr_episodefile_path") or os.environ.get("radarr_moviefile_path")
    if not dest:
        return 0
    video = Path(dest)
    if not video.is_file():
        return 0
    imported = sr.import_sidecar_for_video(video, release_search_dirs(), overwrite=False)
    if imported:
        print(f"import-sidecar: {imported}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
