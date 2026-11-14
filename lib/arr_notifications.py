"""Sonarr/Radarr notification (connection) helpers shared by the register_* scripts."""
from __future__ import annotations

from arr_common import arr_json

# Every on* trigger the *arr notification API knows; connections list only the ones they enable.
EVENT_FLAGS = (
    "onGrab",
    "onDownload",
    "onUpgrade",
    "onImportComplete",
    "onRename",
    "onSeriesAdd",
    "onSeriesDelete",
    "onEpisodeFileDelete",
    "onHealthIssue",
    "onApplicationUpdate",
    "onMovieDelete",
    "onMovieFileDelete",
)


def event_flags(*enabled: str) -> dict[str, bool]:
    """All trigger flags False except the named ones."""
    unknown = set(enabled) - set(EVENT_FLAGS)
    if unknown:
        raise ValueError(f"unknown notification flag(s): {sorted(unknown)}")
    return {flag: flag in enabled for flag in EVENT_FLAGS}


def list_notifications(base_url: str, api_key: str) -> list[dict]:
    data = arr_json(f"{base_url}/api/v3/notification", api_key)
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected notification list from {base_url}")
    return data


def upsert_notification(
    base_url: str,
    api_key: str,
    body: dict,
    *,
    existing: list[dict] | None = None,
    merge_existing: bool = False,
) -> None:
    """Create the connection named body['name'], or update it in place.

    merge_existing keeps unrelated server-side fields of the current connection
    (body wins on conflicts); otherwise body replaces it entirely (id preserved).
    """
    name = body["name"]
    items = existing if existing is not None else list_notifications(base_url, api_key)
    current = next((n for n in items if n.get("name") == name), None)
    if current:
        body = {**current, **body} if merge_existing else {**body, "id": current["id"]}
        arr_json(
            f"{base_url}/api/v3/notification/{current['id']}", api_key, method="PUT", body=body
        )
        print(f"Updated {name} id={current['id']}")
    else:
        arr_json(f"{base_url}/api/v3/notification", api_key, method="POST", body=body)
        print(f"Created {name}")
