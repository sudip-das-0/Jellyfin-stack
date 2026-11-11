#!/usr/bin/env bash
# Apply media-lang-policy globally + backfill existing library (English subs, audio allow-list).
# Ongoing/new imports: arr-import-postprocess.sh + lg-tv-new-media-watch (no extra step).
set -euo pipefail

DIR=$(dirname "$(readlink -f "$0")")
MODE=full
for arg in "$@"; do
  case "$arg" in
    --settings-only) MODE=settings ;;
    --library-only) MODE=library ;;
    --full|full) MODE=full ;;
  esac
done

apply_settings() {
  "$DIR/apply-english-audio-preference.sh"
}

apply_existing_library() {
  echo "=== Existing library: English subtitles backfill ==="

  echo "Normalizing sidecars to one English file per video ({stem}.eng.srt)..."
  /usr/bin/python3 "$DIR/../maintenance/normalize-subtitle-sidecars.py"

  echo "Repairing sidecars from torrent release folders (episode-matched)..."
  /usr/bin/python3 "$DIR/../maintenance/repair-sidecars-from-torrents.py" || true

  echo "Bazarr: assign English profile + search missing subs on disk..."
  "$DIR/apply-bazarr-subtitle-defaults.sh"

  echo "Queue LG TV pipeline for videos missing .eng.srt (extract embedded EN + remux if needed)..."
  local queued=0 had=0
  export LG_TV_QUEUE_NO_START=1
  while IFS= read -r -d '' video; do
    had=$((had + 1))
    stem="${video%.*}"
    [[ -f "${stem}.eng.srt" ]] && continue
    /mnt/jellyfin/scripts/hooks/lg-tv-queue-path.sh "$video"
    queued=$((queued + 1))
  done < <(
    find /mnt/jellyfin/media -type f \( -name '*.mkv' -o -name '*.mp4' \) \
      ! -name '*.lgopt.tmp.mkv' ! -name '*.lgstream.mkv' -print0 2>/dev/null
  )
  unset LG_TV_QUEUE_NO_START
  echo "Videos scanned: ${had}; queued for LG pipeline (no .eng.srt yet): ${queued}"

  if [[ "$queued" -gt 0 ]]; then
    systemctl --user start --no-block lg-tv-optimize.service 2>/dev/null || true
    echo "Started lg-tv-optimize.service (drain queue in background)."
  fi

  echo "Refreshing Jellyfin library (pick up sidecar changes)..."
  API_KEY=$(sqlite3 /mnt/jellyfin/jellyfin/config/data/jellyfin.db \
    "SELECT AccessToken FROM ApiKeys WHERE Name='arr-stack' LIMIT 1;" 2>/dev/null || true)
  if [[ -n "${API_KEY:-}" ]]; then
    curl -sf -X POST -H "Authorization: MediaBrowser Token=\"$API_KEY\"" \
      "http://127.0.0.1:8096/Library/Refresh" -o /dev/null 2>/dev/null || true
  fi
}

case "$MODE" in
  settings) apply_settings ;;
  library) apply_existing_library ;;
  full)
    apply_settings
    apply_existing_library
    ;;
esac

echo "Media language policy apply complete (mode=$MODE)."
