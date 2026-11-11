#!/usr/bin/env python3
"""Jellyfin: Netflix-style next episode for series (TV clients), no playback/latency changes."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from arr_common import JELLYFIN_DB, JELLYFIN_URL  # noqa: E402

API_KEY_NAME = os.environ.get("JELLYFIN_API_KEY_NAME", "arr-stack")
SERIES_VIEW = "5ddaa59a73205234890fdcfc683e14ed"
MOVIES_VIEW = "f137a2dd21bbc1b99aa5c0f6bf02a805"
TV_CLIENTS = ("emby", "jellyfin-webos", "jellyfin-android", "jellyfin-androidtv")
OVERLAY_PREFS = {
    "enableNextVideoInfoOverlay": "True",
    "skipForwardLength": "30000",
    "skipBackLength": "10000",
}
# Lighter home rows on web/TV shells that honor CustomPrefs (no extra library scans).
WEB_HOME_PREFS = {
    **OVERLAY_PREFS,
    "homesection0": "resume",
    "homesection1": "nextup",
    "homesection2": "none",
    "homesection3": "none",
    "homesection4": "none",
    "homesection5": "latestmedia",
    "homesection6": "none",
    "homesection7": "none",
    "homesection8": "none",
    "homesection9": "none",
}


def api(method: str, path: str, token: str, body: dict | None = None) -> dict | None:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{JELLYFIN_URL}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f'MediaBrowser Token="{token}"',
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if not raw:
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise SystemExit(f"Jellyfin API {method} {path} failed: {e.code} {detail}") from e


def main() -> None:
    db = os.environ.get("JELLYFIN_DB", str(JELLYFIN_DB))
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT AccessToken FROM ApiKeys WHERE Name = ? LIMIT 1", (API_KEY_NAME,)
    ).fetchone()
    if not row:
        raise SystemExit(f"No Jellyfin API key named {API_KEY_NAME!r} in {db}")
    token = row[0]

    users = api("GET", "/Users", token)
    if not users:
        raise SystemExit("No Jellyfin users returned")

    for user in users:
        uid = user["Id"]
        name = user.get("Name", uid)
        if user.get("Policy", {}).get("EnableMediaPlayback") is False:
            continue

        full = api("GET", f"/Users/{uid}", token)
        cfg = full.setdefault("Configuration", {})
        cfg["EnableNextEpisodeAutoPlay"] = True
        views = [v.lower() for v in cfg.get("OrderedViews") or []]
        ordered = []
        for vid in (SERIES_VIEW, MOVIES_VIEW):
            if vid not in ordered:
                ordered.append(vid)
        for vid in views:
            if vid not in ordered:
                ordered.append(vid)
        cfg["OrderedViews"] = ordered
        api("POST", f"/Users/{uid}", token, full)
        print(f"user {name}: EnableNextEpisodeAutoPlay, Series before Movies")

        for client in TV_CLIENTS:
            prefs = WEB_HOME_PREFS if client == "emby" else OVERLAY_PREFS
            payload = {
                "Client": client,
                "Id": "00000000-0000-0000-0000-000000000000",
                "CustomPrefs": prefs,
            }
            api(
                "POST",
                f"/DisplayPreferences/usersettings?userId={uid}&client={client}",
                token,
                payload,
            )
            print(f"  client {client}: next-episode overlay enabled")

    print(
        "Done. On the LG Jellyfin app: sign out and sign in once to refresh cached playback settings."
    )


if __name__ == "__main__":
    main()
