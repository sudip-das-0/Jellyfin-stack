#!/usr/bin/env python3
"""Register Sonarr/Radarr CustomScript notification connections (import, delete sync, unmonitor)."""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arr_common import RADARR_CONFIG, RADARR_URL, SONARR_CONFIG, SONARR_URL, arr_json, read_api_key
from arr_notifications import event_flags, list_notifications, upsert_notification

IMPORT_SCRIPT = "/data/scripts/arr-import-postprocess.sh"
DELETE_SCRIPT = "/data/scripts/arr-on-media-deleted.sh"
UNMONITOR_SCRIPT = "/data/scripts/arr-unmonitor-on-import.sh"

LEGACY_IMPORT_NAMES = {
    "sonarr": ["LG TV optimize queue (Sonarr)", "Import sidecar subs (Sonarr)"],
    "radarr": ["LG TV optimize queue (Radarr)", "Import sidecar subs (Radarr)"],
}

# (app key, base URL, config path, display name)
APPS = (
    ("sonarr", SONARR_URL, SONARR_CONFIG, "Sonarr"),
    ("radarr", RADARR_URL, RADARR_CONFIG, "Radarr"),
)


def delete_by_names(base_url: str, api_key: str, names: list[str]) -> None:
    for n in list_notifications(base_url, api_key):
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
    items = list_notifications(base_url, api_key)
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
    body.update(flags)
    body["fields"] = [
        {"name": "path", "value": script_path, "order": 0, "type": "filePath"},
        {"name": "arguments", "value": "", "order": 1, "type": "textbox"},
    ]
    upsert_notification(base_url, api_key, body, existing=items)


def cmd_import_hook(_: argparse.Namespace) -> int:
    for kind, base, config, label in APPS:
        key = read_api_key(config)
        delete_by_names(base, key, LEGACY_IMPORT_NAMES[kind])
        upsert_custom_script(
            base,
            key,
            name=f"Arr import postprocess ({label})",
            script_path=IMPORT_SCRIPT,
            flags=event_flags("onDownload", "onUpgrade"),
        )
    return 0


def _cmd_from_template(prefix: str, script: str, flags_by_kind: dict[str, dict[str, bool]]) -> int:
    for kind, base, config, label in APPS:
        upsert_custom_script(
            base,
            read_api_key(config),
            name=f"{prefix} ({label})",
            script_path=script,
            template_name=f"Arr import postprocess ({label})",
            flags=flags_by_kind[kind],
        )
    return 0


def cmd_delete_sync(_: argparse.Namespace) -> int:
    return _cmd_from_template(
        "Arr delete sync",
        DELETE_SCRIPT,
        {
            "sonarr": event_flags("onEpisodeFileDelete"),
            "radarr": event_flags("onMovieFileDelete"),
        },
    )


def cmd_unmonitor(_: argparse.Namespace) -> int:
    flags = event_flags("onImportComplete")
    return _cmd_from_template(
        "Arr unmonitor on import", UNMONITOR_SCRIPT, {"sonarr": flags, "radarr": flags}
    )


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
