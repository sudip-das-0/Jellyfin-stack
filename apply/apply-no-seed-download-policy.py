#!/usr/bin/env python3
"""Enforce: remove completed torrents from qBittorrent; do not seed."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, "/mnt/jellyfin/scripts/lib")
from arr_common import (
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    arr_json,
    qbit_json,
    read_api_key,
)


def patch_download_client(base_url: str, api_key: str, *, tv: bool) -> None:
    clients = arr_json(f"{base_url}/api/v3/downloadclient", api_key)
    if not isinstance(clients, list) or not clients:
        raise RuntimeError(f"No download clients at {base_url}")

    client = clients[0]
    client["removeCompletedDownloads"] = True
    client["removeFailedDownloads"] = True

    for field in client.get("fields") or []:
        name = field.get("name") or ""
        if tv and name == "tvImportedCategory":
            field["value"] = ""
        if not tv and name == "movieImportedCategory":
            field["value"] = ""

    arr_json(
        f"{base_url}/api/v3/downloadclient/{client['id']}",
        api_key,
        method="PUT",
        body=client,
    )


def patch_qbit_no_seed() -> None:
    patch = {
        "max_ratio_enabled": True,
        "max_ratio": 0,
        # 0 = pause at ratio limit; Sonarr/Radarr remove the torrent after import.
        # Do not use 1 (remove torrent) here — that drops the job before *arr can import.
        "max_ratio_act": 0,
        "max_seeding_time_enabled": True,
        "max_seeding_time": 1,
        "max_inactive_seeding_time_enabled": True,
        "max_inactive_seeding_time": 1,
        # Lower USB SSD write pressure (was apply-qbit-download-safety.sh).
        "preallocate_all": False,
        "disk_cache": 64,
        "disk_cache_ttl": 30,
    }
    body = urllib.parse.urlencode({"json": json.dumps(patch)}).encode()
    qbit_json("/api/v2/app/setPreferences", method="POST", data=body)


def main() -> int:
    sonarr_key = read_api_key(SONARR_CONFIG)
    radarr_key = read_api_key(RADARR_CONFIG)
    patch_download_client(SONARR_URL, sonarr_key, tv=True)
    patch_download_client(RADARR_URL, radarr_key, tv=False)
    patch_qbit_no_seed()
    print("OK: remove completed downloads enabled (Sonarr/Radarr); qBittorrent seeding disabled")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (urllib.error.URLError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
