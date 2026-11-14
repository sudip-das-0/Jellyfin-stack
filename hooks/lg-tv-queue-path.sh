#!/usr/bin/env bash
# Queue a media file for LG TV optimize (host or Sonarr/Radarr container).
set -euo pipefail
# shellcheck source=hook-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/hook-lib.sh"

[[ "${LG_TV_OPTIMIZE_ENABLED:-0}" == "1" ]] || exit 0

raw="${1:-${sonarr_episodefile_path:-${radarr_moviefile_path:-}}}"
[[ -n "$raw" ]] || exit 0

path=$(hook_host_path "$raw")
hook_is_video_path "$path" || exit 0
lg_tv_is_optimizable_video "$path" || exit 0
[[ -f "$raw" || -f "$path" ]] || exit 0

LG_DIR="$(hook_log_root)/lg-tv"
QUEUE="${LG_DIR}/optimize.queue"
SKIPPED="${LG_DIR}/optimize.skipped"
BASELINE="${LG_DIR}/watcher.baseline"
RETRIES="${LG_DIR}/optimize.retries.tsv"

mkdir -p "$LG_DIR"
touch "$SKIPPED"
grep -Fxq -- "$path" "$SKIPPED" 2>/dev/null && exit 0
queue_append_unique "$QUEUE" "$path" || exit 0
queue_append_unique "$BASELINE" "$path" || true

# Bulk queue runs (LG_TV_QUEUE_NO_START=1) skip worker start and retry trimming;
# process-queue trims on drain.
[[ "${LG_TV_QUEUE_NO_START:-0}" == 1 ]] && exit 0

systemctl --user start --no-block lg-tv-optimize.service 2>/dev/null || true

# A fresh queue entry resets earlier failures for this path (and its /data alias).
if [[ -f "$RETRIES" ]]; then
  data_alias=""
  [[ "$path" == "${JELLYFIN_MNT:-/mnt/jellyfin}"/media/* ]] && data_alias="/data/${path#"${JELLYFIN_MNT:-/mnt/jellyfin}"/}"
  _drop_retry() {
    awk -F '\t' -v p="$path" -v d="$data_alias" 'NF && $1 != p && (d == "" || $1 != d)'
  }
  queue_filter "$RETRIES" _drop_retry || true
fi
