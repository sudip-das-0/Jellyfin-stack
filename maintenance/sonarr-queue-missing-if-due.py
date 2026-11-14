#!/usr/bin/env python3
"""Queue Sonarr MissingEpisodeSearch when seasons are incomplete (rate-limited)."""
from __future__ import annotations

import os
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from arr_common import (
    SONARR_CONFIG,
    SONARR_URL,
    STALL_LOG_DIR,
    append_recovery_log,
    arr_json,
    load_json_state,
    queue_arr_command,
    read_api_key,
    save_json_state,
    wanted_missing_count,
)

STATE = STALL_LOG_DIR / "sonarr-missing-episode-search.json"


def cooldown_seconds() -> int:
    hours = int(os.environ.get("SONARR_MISSING_EPISODE_SEARCH_HOURS", "6"))
    return hours * 3600


def load_state() -> dict:
    return load_json_state(STATE, dict)


def missing_search_active(api_key: str) -> bool:
    cmds = arr_json(f"{SONARR_URL}/api/v3/command", api_key)
    if not isinstance(cmds, list):
        return False
    for c in cmds:
        if c.get("name") != "MissingEpisodeSearch":
            continue
        if c.get("status") in ("queued", "started"):
            return True
    return False


def main() -> int:
    api_key = read_api_key(SONARR_CONFIG)
    now = time.time()
    state = load_state()
    last = float(state.get("last_trigger") or 0)
    if now - last < cooldown_seconds():
        print(f"skip: MissingEpisodeSearch cooldown ({int(cooldown_seconds() - (now - last))}s left)")
        return 0
    if missing_search_active(api_key):
        print("skip: MissingEpisodeSearch already queued or running")
        return 0

    missing = wanted_missing_count(SONARR_URL, api_key)
    if missing <= 0:
        print("skip: no missing monitored episodes")
        return 0

    cmd = queue_arr_command(SONARR_URL, api_key, "MissingEpisodeSearch")
    state["last_trigger"] = now
    state["missing_at_trigger"] = missing
    save_json_state(STATE, state)
    msg = (
        f"sonarr-queue-missing-if-due: MissingEpisodeSearch id={cmd.get('id')} "
        f"status={cmd.get('status')} (missing={missing})"
    )
    append_recovery_log(msg, tag="sonarr")
    print(msg)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
