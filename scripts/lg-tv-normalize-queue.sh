#!/usr/bin/env bash
# Normalize lg-tv-optimize.queue paths to /mnt/jellyfin/... (dedupe /data/media aliases).
set -euo pipefail
# shellcheck source=stack-paths.sh
source /home/devdas/arr/scripts/stack-paths.sh
QUEUE=$LG_TV_QUEUE
[[ -f "$QUEUE" ]] || exit 0

tmp=$(mktemp)
python3 - "$QUEUE" "$tmp" <<'PY'
import sys
from pathlib import Path

queue, out = sys.argv[1:3]
seen: set[str] = set()
lines: list[str] = []
for raw in Path(queue).read_text().splitlines():
    p = raw.strip()
    if not p:
        continue
    if p.startswith("/data/media/"):
        p = "/mnt/jellyfin/" + p[len("/data/") :]
    if p in seen:
        continue
    seen.add(p)
    lines.append(p)
Path(out).write_text("\n".join(lines) + ("\n" if lines else ""))
print(f"normalized queue: {len(lines)} unique path(s)")
PY
mv -f "$tmp" "$QUEUE"
