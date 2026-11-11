#!/usr/bin/env python3
"""Trim/rotate maintenance and LG optimize logs under /mnt/jellyfin/logs."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from arr_common import (
    STALL_LOG_DIR,
    append_recovery_log,
    prune_dated_logs,
    rotate_log_file,
    trim_log_tail,
)

LG_LOG = STALL_LOG_DIR / "lg-tv-optimize.log"
QBIT_LOG = STALL_LOG_DIR / "qbit-missing-cleanup.log"
_LG_NOISE = (
    "gave up after",
    "re-queued (attempt",
    "vaapi hevc path failed",
    "encode tier failed",
    "encode tier timed out",
    "all encode tiers failed",
    "optimize produced no output",
)
_LG_DONE = (
    "done:",
    "skip (already LG-friendly):",
    "skip (already LG-friendly, untagged audio):",
)


def scrub_lg_resolved_log() -> int:
    if not LG_LOG.is_file():
        return 0
    lines = LG_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    path_re = re.compile(r"(/mnt/jellyfin/media/.+?\.(?:mkv|mp4))")
    resolved: set[str] = set()
    for line in lines:
        if not any(m in line for m in _LG_DONE):
            continue
        m = path_re.search(line)
        if m:
            resolved.add(m.group(1))
    if not resolved:
        return 0
    kept: list[str] = []
    dropped = 0
    for line in lines:
        m = path_re.search(line)
        if m and m.group(1) in resolved and any(n in line for n in _LG_NOISE):
            dropped += 1
            continue
        kept.append(line)
    if dropped:
        rotate_log_file(LG_LOG)
        LG_LOG.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        append_recovery_log(f"scrubbed {dropped} stale LG log line(s)", tag="prune")
    return dropped


def main() -> int:
    removed = prune_dated_logs(STALL_LOG_DIR, "recovery-", keep_days=14)
    rotate_log_file(STALL_LOG_DIR / "partial-series-cleanup.log")
    rotate_log_file(STALL_LOG_DIR / "torrent-library-cleanup.log")
    rotate_log_file(QBIT_LOG)
    trim_log_tail(LG_LOG)
    scrub_lg_resolved_log()
    if removed:
        append_recovery_log(f"pruned {removed} old recovery-*.log file(s)", tag="prune")
    return 0


if __name__ == "__main__":
    sys.exit(main())
