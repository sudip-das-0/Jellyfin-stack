# shellcheck shell=bash
# Queue/file helpers shared by *arr hooks (run in Sonarr/Radarr containers) and host scripts.
# Container-safe: needs only bash + coreutils + flock. Sourced by lib/stack-paths.sh on the host.
# Not an entrypoint.

# Log root: /data/logs inside *arr containers, ${JELLYFIN_MNT}/logs on the host.
hook_log_root() {
  if [[ -d /data/logs ]]; then
    echo /data/logs
  else
    echo "${JELLYFIN_MNT:-/mnt/jellyfin}/logs"
  fi
}

# Rewrite a /data/media/... container path to the host path; other paths pass through.
hook_host_path() {
  local p=$1 mnt="${JELLYFIN_MNT:-/mnt/jellyfin}"
  if [[ "$p" == /data/media/* ]]; then
    echo "${mnt}/${p#/data/}"
  else
    echo "$p"
  fi
}

# True for video files the library/LG pipeline cares about.
hook_is_video_path() {
  case "${1,,}" in
    *.mkv|*.mp4|*.m2ts|*.ts) return 0 ;;
    *) return 1 ;;
  esac
}

# queue_append FILE LINE... : append lines under an exclusive lock (FILE.flock).
queue_append() {
  local file=$1
  shift
  mkdir -p "$(dirname "$file")"
  (
    flock 9
    printf '%s\n' "$@" >>"$file"
  ) 9>"${file}.flock"
}

# queue_append_unique FILE LINE : append LINE unless already present. 0=added, 1=duplicate.
queue_append_unique() {
  local file=$1 line=$2
  mkdir -p "$(dirname "$file")"
  (
    flock 9
    if grep -Fxq -- "$line" "$file" 2>/dev/null; then
      exit 1
    fi
    printf '%s\n' "$line" >>"$file"
  ) 9>"${file}.flock"
}

# queue_drain FILE : print FILE's lines and empty it atomically w.r.t. queue_append*.
queue_drain() {
  local file=$1
  [[ -f "$file" ]] || return 0
  (
    flock 9
    cat -- "$file"
    : >"$file"
  ) 9>"${file}.flock"
}

# queue_filter FILE CMD [ARGS...] : replace FILE with CMD's output, where CMD reads FILE on
# stdin. Runs entirely under the queue lock, so concurrent appends are never lost. If CMD
# fails the file is left untouched (wrap grep as `sh -c 'grep ... || true'` when an empty
# result is valid).
queue_filter() {
  local file=$1 tmp
  shift
  [[ -f "$file" ]] || return 0
  (
    flock 9
    tmp=$(mktemp "${file}.XXXXXX")
    if "$@" <"$file" >"$tmp"; then
      chmod 644 "$tmp"
      mv -f -- "$tmp" "$file"
    else
      rm -f -- "$tmp"
      exit 1
    fi
  ) 9>"${file}.flock"
}

# queue_pop_front FILE : print and remove the first non-blank line; dedupes the remainder.
# Prints nothing (rc 0) when the queue is empty.
queue_pop_front() {
  local file=$1
  [[ -s "$file" ]] || return 0
  (
    flock 9
    first=$(awk 'NF && !seen[$0]++ { print; exit }' "$file")
    if [[ -z "$first" ]]; then
      : >"$file"
      exit 0
    fi
    tmp=$(mktemp "${file}.XXXXXX")
    awk -v f="$first" 'NF && $0 != f && !seen[$0]++' "$file" >"$tmp"
    chmod 644 "$tmp"
    mv -f -- "$tmp" "$file"
    printf '%s\n' "$first"
  ) 9>"${file}.flock"
}

# queue_push_front FILE LINE : put LINE first and drop any later copy.
queue_push_front() {
  local file=$1 line=$2 tmp
  mkdir -p "$(dirname "$file")"
  (
    flock 9
    tmp=$(mktemp "${file}.XXXXXX")
    {
      printf '%s\n' "$line"
      cat -- "$file" 2>/dev/null || true
    } | awk 'NF && !seen[$0]++' >"$tmp"
    chmod 644 "$tmp"
    mv -f -- "$tmp" "$file"
  ) 9>"${file}.flock"
}

# lg_tv_normalize_stream : stdin -> unique non-blank lines with /data/media/... mapped to host.
lg_tv_normalize_stream() {
  awk -v mnt="${JELLYFIN_MNT:-/mnt/jellyfin}" '
    NF {
      if (index($0, "/data/media/") == 1) $0 = mnt "/media/" substr($0, 13)
      low = tolower($0)
      if (index(low, ".lgopt.tmp") || index(low, ".lgstream") || index(low, ".lgopt-backup")) next
      if (!seen[$0]++) print
    }'
}

# atomic_write DEST : write stdin to DEST via temp file + rename (no locking).
atomic_write() {
  local dest=$1 tmp
  tmp=$(mktemp "${dest}.XXXXXX")
  cat >"$tmp"
  chmod 644 "$tmp"
  mv -f -- "$tmp" "$dest"
}
