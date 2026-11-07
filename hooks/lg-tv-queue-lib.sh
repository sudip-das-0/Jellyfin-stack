# shellcheck shell=bash
# Shared LG optimize queue helpers (source from stack scripts; not an entrypoint).
# Requires: stack-paths.sh sourced, LG_TV_QUEUE, LG_TV_SKIPPED, HOOKS_DIR.

lg_tv_queue_is_duplicate() {
  local path=$1
  grep -Fxq "$path" "$LG_TV_SKIPPED" 2>/dev/null && return 0
  grep -Fxq "$path" "$LG_TV_QUEUE" 2>/dev/null && return 0
  return 1
}

lg_tv_enqueue_path() {
  local path=$1 dry=${2:-0}
  local helper="${HOOKS_DIR}/lg-tv-queue-path.sh"
  [[ -n "$path" ]] || return 1
  if lg_tv_queue_is_duplicate "$path"; then
    return 2
  fi
  if [[ "$dry" -eq 1 ]]; then
    echo "would queue: $path"
    return 0
  fi
  "$helper" "$path"
}

lg_tv_maybe_start_worker() {
  local queued=$1 dry=${2:-0}
  if [[ "$dry" -eq 0 && "$queued" -gt 0 ]]; then
    systemctl --user start --no-block lg-tv-optimize.service 2>/dev/null || true
  fi
