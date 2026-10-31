#!/usr/bin/env python3
"""Remove qBittorrent missingFiles/dead stalledDL torrents (no mass queuedDL purge)."""
from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arr_common import append_recovery_log, qbit_json

VERBOSE = os.environ.get("QBIT_CLEANUP_VERBOSE", "") == "1"
# Magnets stuck in metaDL with no metadata block all active download slots.
META_STUCK_SECONDS = int(os.environ.get("QBIT_META_STUCK_SECONDS", "3600"))


def should_remove(t: dict, *, now: float) -> tuple[bool, str]:
    state = t.get("state") or ""
    if state == "missingFiles":
        return True, "missingFiles"
    progress = float(t.get("progress") or 0)
    seeds = int(t.get("num_seeds") or 0)
    added = float(t.get("added_on") or now)
    age = now - added
    if state in ("stalledDL", "metaDL") and progress <= 0 and seeds <= 0:
        if t.get("has_metadata") is True:
            return True, f"{state}-dead"
        if state == "metaDL" and not t.get("has_metadata") and age >= META_STUCK_SECONDS:
            return True, "metaDL-no-metadata"
    return False, ""


def main() -> int:
    torrents = qbit_json("/api/v2/torrents/info")
    if not isinstance(torrents, list):
        append_recovery_log("qbit cleanup: unexpected API response", tag="qbit")
        return 1

    now = time.time()
    remove: list[str] = []
    for t in torrents:
        ok, reason = should_remove(t, now=now)
        if not ok:
            continue
        h = t.get("hash")
        if h:
            remove.append(h)
            if VERBOSE:
                append_recovery_log(
                    f"remove ({reason}): {(t.get('name') or '?')[:90]}", tag="qbit"
                )

    if not remove:
        return 0

    body = urllib.parse.urlencode({"hashes": "|".join(remove), "deleteFiles": "true"}).encode()
    qbit_json("/api/v2/torrents/delete", method="POST", data=body)
    append_recovery_log(f"removed {len(remove)} dead/missing torrent(s)", tag="qbit")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        append_recovery_log(f"qBittorrent unavailable: {e}", tag="qbit")
        sys.exit(1)
