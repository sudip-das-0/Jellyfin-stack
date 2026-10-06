#!/usr/bin/env python3
"""SeasonSearch for incomplete monitored seasons (default 12h cooldown per season)."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arr_common import SONARR_CONFIG, SONARR_URL, STALL_LOG_DIR, arr_json, iter_incomplete_seasons, read_api_key

STATE = STALL_LOG_DIR / "sonarr-missing-search.json"
MIN_INTERVAL = int(os.environ.get("SONARR_MISSING_SEARCH_HOURS", "12")) * 3600


def load_state() -> dict[str, float]:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def main() -> int:
    api_key = read_api_key(SONARR_CONFIG)
    state = load_state()
    now = time.time()
    triggered = 0

    for title, sid, sn, have, total in iter_incomplete_seasons(api_key, partial_only=False):
        key = f"{sid}:s{sn}"
        if now - state.get(key, 0) < MIN_INTERVAL:
            continue
        try:
            arr_json(
                f"{SONARR_URL}/api/v3/command",
                api_key,
                method="POST",
                body={"name": "SeasonSearch", "seriesId": sid, "seasonNumber": sn},
            )
            state[key] = now
            triggered += 1
            print(f"[missing-search] SeasonSearch {title} S{sn:02d} ({have}/{total})")
        except urllib.error.HTTPError as e:
            print(f"[missing-search] failed {title} S{sn}: {e}", file=sys.stderr)

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
