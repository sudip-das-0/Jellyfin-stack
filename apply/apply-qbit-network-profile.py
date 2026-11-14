#!/usr/bin/env python3
"""Apply qBittorrent speed/concurrency limits based on the current Wi‑Fi SSID."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from arr_common import (
    LOG_ROOT,
    SCRIPTS_ROOT,
    echo_script_log,
    load_json_state,
    qbit_json,
    qbit_set_preferences,
    save_json_state,
)

CONFIG_PATH = SCRIPTS_ROOT / "config/qbit-network-profiles.json"
STATE_PATH = LOG_ROOT / "qbit-network-profile.state.json"
LOG_PATH = LOG_ROOT / "stall-recovery" / "qbit-network-profile.log"


def log(msg: str) -> None:
    echo_script_log(LOG_PATH, msg)


def current_wifi_ssid() -> str | None:
    try:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    for line in out.splitlines():
        active, _, ssid = line.partition(":")
        if active == "yes" and ssid.strip():
            return ssid.strip()
    return None


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def resolve_profile_name(cfg: dict, ssid: str | None) -> str:
    if not ssid:
        return cfg["default_profile"]
    ssid_map = cfg.get("ssid_profiles") or {}
    for key, profile in ssid_map.items():
        if key.strip().casefold() == ssid.casefold():
            return profile
    return cfg["default_profile"]


def profile_settings(cfg: dict, profile_name: str) -> dict:
    profiles = cfg.get("profiles") or {}
    if profile_name not in profiles:
        raise RuntimeError(f"Unknown profile {profile_name!r} in {CONFIG_PATH}")
    return profiles[profile_name]


def read_state() -> dict:
    state = load_json_state(STATE_PATH, dict)
    return state if isinstance(state, dict) else {}


def write_state(state: dict) -> None:
    save_json_state(STATE_PATH, state)


def qbit_preferences() -> dict:
    prefs = qbit_json("/api/v2/app/preferences")
    if not isinstance(prefs, dict):
        raise RuntimeError("Unexpected qBittorrent preferences response")
    return prefs


def needs_update(prefs: dict, settings: dict) -> bool:
    for key, value in settings.items():
        if prefs.get(key) != value:
            return True
    return False


def main() -> int:
    cfg = load_config()
    ssid = current_wifi_ssid()
    profile_name = resolve_profile_name(cfg, ssid)
    settings = profile_settings(cfg, profile_name)

    state = read_state()
    prefs = qbit_preferences()
    if (
        state.get("profile") == profile_name
        and state.get("ssid") == ssid
        and not os.environ.get("QBIT_NETWORK_PROFILE_FORCE")
        and not needs_update(prefs, settings)
    ):
        return 0

    if needs_update(prefs, settings):
        qbit_set_preferences(settings)
        log(
            f"profile={profile_name} ssid={ssid!r} "
            f"dl_limit={settings['dl_limit']} "
            f"max_active_downloads={settings['max_active_downloads']}"
        )
    else:
        log(f"unchanged profile={profile_name} ssid={ssid!r}")

    write_state({"profile": profile_name, "ssid": ssid, "settings": settings})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — maintenance script
        log(f"ERROR: {exc}")
        sys.exit(1)
