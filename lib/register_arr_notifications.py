#!/usr/bin/env python3
"""Register Sonarr/Radarr CustomScript notification connections (import, delete sync, unmonitor)."""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arr_common import RADARR_CONFIG, RADARR_URL, SONARR_CONFIG, SONARR_URL, arr_json, read_api_key

IMPORT_SCRIPT = "/data/scripts/arr-import-postprocess.sh"
DELETE_SCRIPT = "/data/scripts/arr-on-media-deleted.sh"
UNMONITOR_SCRIPT = "/data/scripts/arr-unmonitor-on-import.sh"

LEGACY_IMPORT_NAMES = {
    "sonarr": ["LG TV optimize queue (Sonarr)", "Import sidecar subs (Sonarr)"],
    "radarr": ["LG TV optimize queue (Radarr)", "Import sidecar subs (Radarr)"],
}


def notifications(base_url: str, api_key: str) -> list[dict]:
    data = arr_json(f"{base_url}/api/v3/notification", api_key)
    return data if isinstance(data, list) else []


def delete_by_names(base_url: str, api_key: str, names: list[str]) -> None:
    for n in notifications(base_url, api_key):
        if n.get("name") in names:
            arr_json(f"{base_url}/api/v3/notification/{n['id']}", api_key, method="DELETE")
            print(f"Removed legacy connection: {n['name']}")


def upsert_custom_script(
    base_url: str,
    api_key: str,
    *,
    name: str,
    script_path: str,
    flags: dict[str, bool],
    template_name: str | None = None,
) -> None:
    items = notifications(base_url, api_key)
    existing = next((n for n in items if n.get("name") == name), None)
    if template_name:
        template = next((n for n in items if n.get("name") == template_name), None)
        if not template:
            raise RuntimeError(f"Missing template connection {template_name!r}")
        body = {k: v for k, v in template.items() if k != "id"}
    else:
        body = {
            "implementation": "CustomScript",
            "configContract": "CustomScriptSettings",
            "includeHealthWarnings": False,
            "tags": [],
        }
    body = copy.deepcopy(body)
    body["name"] = name
    for k, v in flags.items():
        body[k] = v
    body["fields"] = [
        {"name": "path", "value": script_path, "order": 0, "type": "filePath"},
        {"name": "arguments", "value": "", "order": 1, "type": "textbox"},
    ]
    if existing:
        body["id"] = existing["id"]
        arr_json(f"{base_url}/api/v3/notification/{existing['id']}", api_key, method="PUT", body=body)
        print(f"Updated {name} id={existing['id']}")
    else:
        arr_json(f"{base_url}/api/v3/notification", api_key, method="POST", body=body)
        print(f"Created {name}")


def cmd_import_hook(_: argparse.Namespace) -> int:
    son = read_api_key(SONARR_CONFIG)
    rad = read_api_key(RADARR_CONFIG)
    for base, key, kind, label in (
        (SONARR_URL, son, "sonarr", "Arr import postprocess (Sonarr)"),
        (RADARR_URL, rad, "radarr", "Arr import postprocess (Radarr)"),
    ):
        delete_by_names(base, key, LEGACY_IMPORT_NAMES[kind])
        upsert_custom_script(
            base,
            key,
            name=label,
            script_path=IMPORT_SCRIPT,
            flags={
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
            },
        )
    return 0


def cmd_delete_sync(_: argparse.Namespace) -> int:
    son = read_api_key(SONARR_CONFIG)
    rad = read_api_key(RADARR_CONFIG)
    upsert_custom_script(
        SONARR_URL,
        son,
        name="Arr delete sync (Sonarr)",
        script_path=DELETE_SCRIPT,
        template_name="Arr import postprocess (Sonarr)",
        flags={
            "onGrab": False,
            "onDownload": False,
            "onUpgrade": False,
            "onImportComplete": False,
            "onRename": False,
            "onSeriesAdd": False,
            "onSeriesDelete": False,
            "onEpisodeFileDelete": True,
            "onHealthIssue": False,
            "onApplicationUpdate": False,
            "onMovieDelete": False,
            "onMovieFileDelete": False,
        },
    )
    upsert_custom_script(
        RADARR_URL,
        rad,
        name="Arr delete sync (Radarr)",
        script_path=DELETE_SCRIPT,
        template_name="Arr import postprocess (Radarr)",
        flags={
            "onGrab": False,
            "onDownload": False,
            "onUpgrade": False,
            "onImportComplete": False,
            "onRename": False,
            "onSeriesAdd": False,
            "onSeriesDelete": False,
            "onEpisodeFileDelete": False,
            "onHealthIssue": False,
            "onApplicationUpdate": False,
            "onMovieDelete": False,
            "onMovieFileDelete": True,
        },
    )
    return 0


def cmd_unmonitor(_: argparse.Namespace) -> int:
    son = read_api_key(SONARR_CONFIG)
    rad = read_api_key(RADARR_CONFIG)
    upsert_custom_script(
        SONARR_URL,
        son,
        name="Arr unmonitor on import (Sonarr)",
        script_path=UNMONITOR_SCRIPT,
        template_name="Arr import postprocess (Sonarr)",
        flags={
            "onGrab": False,
            "onDownload": False,
            "onUpgrade": False,
            "onImportComplete": True,
            "onRename": False,
            "onSeriesAdd": False,
            "onSeriesDelete": False,
            "onEpisodeFileDelete": False,
            "onHealthIssue": False,
            "onApplicationUpdate": False,
            "onMovieDelete": False,
            "onMovieFileDelete": False,
        },
    )
    upsert_custom_script(
        RADARR_URL,
        rad,
        name="Arr unmonitor on import (Radarr)",
        script_path=UNMONITOR_SCRIPT,
        template_name="Arr import postprocess (Radarr)",
        flags={
            "onGrab": False,
            "onDownload": False,
            "onUpgrade": False,
            "onImportComplete": True,
            "onRename": False,
            "onSeriesAdd": False,
            "onSeriesDelete": False,
            "onEpisodeFileDelete": False,
            "onHealthIssue": False,
            "onApplicationUpdate": False,
            "onMovieDelete": False,
            "onMovieFileDelete": False,
        },
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("import-hook", help="arr-import-postprocess on download/upgrade").set_defaults(
        func=cmd_import_hook
    )
    sub.add_parser("delete-sync", help="arr-on-media-deleted on file delete").set_defaults(func=cmd_delete_sync)
    sub.add_parser("unmonitor", help="arr-unmonitor-on-import on import complete").set_defaults(func=cmd_unmonitor)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
