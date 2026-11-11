#!/usr/bin/env python3
"""Remove finished torrents from qBittorrent (no seeding). Keeps data on disk (deleteFiles=false)."""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from arr_common import append_recovery_log, qbit_all_protected_hashes, qbit_json

VERBOSE = os.environ.get("QBIT_CLEANUP_VERBOSE", "") == "1"


def is_completed(t: dict) -> bool:
    progress = float(t.get("progress") or 0)
    if progress >= 0.999:
        return True
    state = t.get("state") or ""
    return state in ("uploading", "stalledUP", "pausedUP", "queuedUP", "forcedUP")


def main() -> int:
    torrents = qbit_json("/api/v2/torrents/info", timeout=60)
    if not isinstance(torrents, list):
        append_recovery_log("qbit completed cleanup: unexpected API response", tag="qbit")
        return 1

    protected = qbit_all_protected_hashes()
    remove: list[str] = []
    skipped_protected = 0
    for t in torrents:
        if not is_completed(t):
            continue
        h = t.get("hash")
        if not h:
            continue
        if str(h).casefold() in protected:
            skipped_protected += 1
            continue
        remove.append(h)
        if VERBOSE:
            append_recovery_log(
                f"remove completed: {(t.get('name') or '?')[:90]}", tag="qbit"
            )

    if not remove:
        return 0

    body = urllib.parse.urlencode(
        {"hashes": "|".join(remove), "deleteFiles": "false"}
    ).encode()
    qbit_json("/api/v2/torrents/delete", method="POST", data=body)
    msg = f"removed {len(remove)} completed torrent(s) (no seed)"
    if skipped_protected:
        msg += f"; skipped {skipped_protected} still in *arr queue"
    append_recovery_log(msg, tag="qbit")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        append_recovery_log(f"qBittorrent unavailable: {e}", tag="qbit")
        sys.exit(1)
