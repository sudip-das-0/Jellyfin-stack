#!/usr/bin/env python3
"""Import orphan videos under /mnt/jellyfin/torrents (runs full reconcile pass)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "reconcile-torrents-library.py"


def main() -> int:
    limit = os.environ.get("RECONCILE_IMPORT_LIMIT", "500")
    argv = [sys.executable, str(SCRIPT), "--skip-dedupe-cleanup", "--limit", str(limit)]
    if "--dry-run" in sys.argv[1:]:
        argv.append("--dry-run")
    return subprocess.call(argv)


if __name__ == "__main__":
    raise SystemExit(main())
