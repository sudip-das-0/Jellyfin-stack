#!/usr/bin/env python3
"""Exit 1 when a monitored Sonarr season is partially on disk (0 < have < total)."""
from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from arr_common import SONARR_CONFIG, STALL_LOG_DIR, iter_incomplete_seasons, read_api_key


def main() -> int:
    api_key = read_api_key(SONARR_CONFIG)
    rows = iter_incomplete_seasons(api_key, partial_only=True)
    log_path = STALL_LOG_DIR / "series-completeness.log"
    STALL_LOG_DIR.mkdir(parents=True, exist_ok=True)

    if rows:
        lines = [f"{t} S{sn:02d}: {have}/{total} episodes on disk" for t, _sid, sn, have, total in rows]
        msg = "INCOMPLETE monitored season(s):\n" + "\n".join(f"  - {line}" for line in lines)
        log_path.write_text(msg + "\n", encoding="utf-8")
        print(msg, file=sys.stderr)
        return 1

    log_path.write_text("OK: all monitored seasons complete on disk.\n", encoding="utf-8")
    print("OK: all monitored seasons complete on disk.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        print(f"Sonarr unavailable: {e}", file=sys.stderr)
        sys.exit(2)
