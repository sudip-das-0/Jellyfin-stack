#!/usr/bin/env bash
# Remove legacy torrent paths on the internal NVMe (never used by the live Docker stack).
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

log() { echo "$(date -Iseconds) [purge-internal-torrent-data] $*"; }

remove_tree_if_small() {
  local dir=$1 max_kb=${2:-64}
  [[ -d "$dir" ]] || return 0
  local kb
  kb=$(du -sk "$dir" | awk '{print $1}')
  if [[ "$kb" -le "$max_kb" ]]; then
    rm -rf "$dir"
    log "removed $dir (${kb}KB)"
  else
    log "SKIP $dir (${kb}KB > ${max_kb}KB cap — inspect manually)"
    return 1
  fi
}

# Old dev layout (pre–external SSD stack).
remove_tree_if_small "$HOME/data/torrents" 4096 || true
remove_tree_if_small "$HOME/data/media" 4096 || true
rmdir "$HOME/data" 2>/dev/null || true

# NVMe skeleton hidden under /mnt/jellyfin when SSD is unplugged — wipe only if tiny.
if mountpoint -q "$JELLYFIN_MNT"; then
  log "SSD mounted at $JELLYFIN_MNT — skipping NVMe skeleton purge (unplug SSD first to clean placeholder)"
  exit 0
fi

if [[ -d "$JELLYFIN_MNT" ]]; then
  kb=$(du -sk "$JELLYFIN_MNT" 2>/dev/null | awk '{print $1}')
  if [[ "${kb:-0}" -le 16384 ]]; then
    rm -rf "${JELLYFIN_MNT:?}"/*
    log "cleared NVMe placeholder under $JELLYFIN_MNT (${kb}KB)"
  else
    log "SKIP $JELLYFIN_MNT skeleton (${kb}KB — not a tiny placeholder)"
    exit 1
  fi
fi

log "done"
