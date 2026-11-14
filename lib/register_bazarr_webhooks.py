#!/usr/bin/env python3
"""Register Sonarr/Radarr webhook notifications -> Bazarr (import/upgrade)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arr_common import (  # noqa: E402
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    read_api_key,
    read_bazarr_api_key,
)
from arr_notifications import event_flags, upsert_notification  # noqa: E402


def upsert_webhook(
    base_url: str,
    api_key: str,
    *,
    name: str,
    url: str,
    bazarr_key: str,
) -> None:
    body: dict = {
        "name": name,
        "implementation": "Webhook",
        "configContract": "WebhookSettings",
        **event_flags("onDownload", "onUpgrade"),
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
    upsert_notification(base_url, api_key, body, merge_existing=True)


def main() -> int:
    baz = read_bazarr_api_key()
    for base, config, label, app in (
        (SONARR_URL, SONARR_CONFIG, "Sonarr", "sonarr"),
        (RADARR_URL, RADARR_CONFIG, "Radarr", "radarr"),
    ):
        upsert_webhook(
            base,
            read_api_key(config),
            name=f"Bazarr subtitles ({label})",
            url=f"http://bazarr:6767/api/webhooks/{app}",
            bazarr_key=baz,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
