#!/usr/bin/env python3
"""Register Sonarr/Radarr webhook notifications → Bazarr (import/upgrade)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/mnt/jellyfin/scripts/lib")
from arr_common import (  # noqa: E402
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    arr_json,
    read_api_key,
    read_bazarr_api_key,
)


def upsert_webhook(
    base_url: str,
    api_key: str,
    *,
    name: str,
    url: str,
    bazarr_key: str,
) -> None:
    items = arr_json(f"{base_url}/api/v3/notification", api_key)
    if not isinstance(items, list):
        raise RuntimeError(f"Unexpected notification list from {base_url}")
    existing = next((n for n in items if n.get("name") == name), None)
    body: dict = {
        "name": name,
        "implementation": "Webhook",
        "configContract": "WebhookSettings",
        "onGrab": False,
        "onDownload": True,
        "onUpgrade": True,
        "onImportComplete": False,
        "onRename": False,
        "onSeriesAdd": False,
        "onSeriesDelete": False,
        "onEpisodeFileDelete": False,
        "onHealthIssue": False,
        "onApplicationUpdate": False,
        "onMovieDelete": False,
        "onMovieFileDelete": False,
        "includeHealthWarnings": False,
        "tags": [],
        "fields": [
            {"name": "url", "value": url},
            {"name": "method", "value": 1},
            {"name": "username", "value": ""},
            {"name": "password", "value": ""},
            {
                "name": "headers",
                "value": [{"key": "X-Api-Key", "value": bazarr_key}],
            },
        ],
    }
    if existing:
        body = {**existing, **body}
        arr_json(
            f"{base_url}/api/v3/notification/{existing['id']}",
            api_key,
            method="PUT",
            body=body,
        )
        print(f"Updated {name} id={existing['id']}")
    else:
        arr_json(f"{base_url}/api/v3/notification", api_key, method="POST", body=body)
        print(f"Created {name}")


def main() -> int:
    son = read_api_key(SONARR_CONFIG)
    rad = read_api_key(RADARR_CONFIG)
    baz = read_bazarr_api_key()
    upsert_webhook(
        SONARR_URL,
        son,
        name="Bazarr subtitles (Sonarr)",
        url="http://bazarr:6767/api/webhooks/sonarr",
        bazarr_key=baz,
    )
    upsert_webhook(
        RADARR_URL,
        rad,
        name="Bazarr subtitles (Radarr)",
        url="http://bazarr:6767/api/webhooks/radarr",
        bazarr_key=baz,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
