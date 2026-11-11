#!/usr/bin/env bash
# Purpose: Jellyfin DB + library path cleanup (phantom movies, orphans, mblinks).
# Runs: manual (stops Jellyfin container for offline DB work when needed)
set -euo pipefail
# shellcheck source=stack-paths.sh
source "$(dirname "$0")/../lib/stack-paths.sh"

DB="$JELLYFIN_DB"
CONFIG_ROOT="${JELLYFIN_CONFIG_ROOT}/root/default"
NEED_STOP=0

stop_jellyfin() {
  [[ "$NEED_STOP" == 1 ]] && return 0
  echo "Stopping Jellyfin..."
  docker stop jellyfin >/dev/null
  NEED_STOP=1
}

start_jellyfin() {
  [[ "$NEED_STOP" == 1 ]] || return 0
  echo "Starting Jellyfin..."
  docker start jellyfin >/dev/null
  for _ in $(seq 1 30); do
    curl -sf "${JELLYFIN_URL}/System/Info/Public" >/dev/null 2>&1 && break
    sleep 2
  done
}

trap start_jellyfin EXIT

fix_mblinks() {
  local fixed=0 f path
  for f in "$CONFIG_ROOT"/*/*.mblink; do
    [[ -f "$f" ]] || continue
    path=$(tr -d '\r\n' < "$f")
    if [[ "$(wc -c < "$f" | tr -d ' ')" -ne ${#path} ]]; then
      printf '%s' "$path" > "$f"
      fixed=$((fixed + 1))
      echo "Fixed mblink: $f -> $path"
    fi
  done
  [[ "$fixed" -eq 0 ]] || echo "Normalized ${fixed} .mblink path(s)."
}

fix_library_paths_in_db() {
  python3 - "$DB" <<'PY'
import json, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
rows = db.execute(
    "SELECT Id, Data FROM BaseItems WHERE Type LIKE '%CollectionFolder%' AND Data IS NOT NULL"
).fetchall()
changed = 0
for item_id, raw in rows:
    if not raw:
        continue
    data = json.loads(raw)
    locs = data.get("PhysicalLocationsList")
    if not isinstance(locs, list):
        continue
    new = [p.rstrip("\r\n") for p in locs if p and not p.startswith("/config/")]
    if new != locs:
        data["PhysicalLocationsList"] = new
        db.execute("UPDATE BaseItems SET Data=? WHERE Id=?", (json.dumps(data), item_id))
        changed += 1
if changed:
    db.commit()
    print(f"Fixed PhysicalLocationsList on {changed} library folder(s)")
PY
}

trim_sqlite() {
  stop_jellyfin
  fix_library_paths_in_db
  local legacy_id removed phantom orphan_ud placeholder

  legacy_id=$(sqlite3 "$DB" "SELECT Id FROM BaseItems WHERE path='/media' AND Name='media' LIMIT 1;")
  if [[ -n "$legacy_id" ]]; then
    removed=$(sqlite3 "$DB" "SELECT COUNT(*) FROM BaseItems WHERE TopParentId='${legacy_id}' OR Id='${legacy_id}';")
    sqlite3 "$DB" "DELETE FROM UserData WHERE ItemId IN (SELECT Id FROM BaseItems WHERE TopParentId='${legacy_id}');"
    sqlite3 "$DB" "DELETE FROM BaseItems WHERE TopParentId='${legacy_id}' OR Id='${legacy_id}';"
    echo "Removed ${removed} legacy /media library item(s)."
  else
    echo "Legacy /media library: already clean."
  fi

  phantom=$(sqlite3 "$DB" "SELECT COUNT(*) FROM BaseItems WHERE Type LIKE '%Movie%' AND (path LIKE '/media/series/%' OR path LIKE '%/series/%/Season %');")
  if [[ "$phantom" -gt 0 ]]; then
    sqlite3 "$DB" "DELETE FROM UserData WHERE ItemId IN (SELECT Id FROM BaseItems WHERE Type LIKE '%Movie%' AND (path LIKE '/media/series/%' OR path LIKE '%/series/%/Season %'));"
    sqlite3 "$DB" "DELETE FROM BaseItems WHERE Type LIKE '%Movie%' AND (path LIKE '/media/series/%' OR path LIKE '%/series/%/Season %');"
    echo "Removed ${phantom} phantom movie row(s) under /media/series."
  else
    echo "Phantom series-as-movies: none."
  fi

  orphan_ud=$(sqlite3 "$DB" "SELECT COUNT(*) FROM UserData WHERE ItemId NOT IN (SELECT Id FROM BaseItems);")
  if [[ "$orphan_ud" -gt 0 ]]; then
    sqlite3 "$DB" "DELETE FROM UserData WHERE ItemId NOT IN (SELECT Id FROM BaseItems);"
    echo "Removed ${orphan_ud} orphan UserData row(s)."
  fi

  placeholder=$(sqlite3 "$DB" "SELECT COUNT(*) FROM BaseItems WHERE Type='PLACEHOLDER';")
  if [[ "$placeholder" -gt 0 ]]; then
    sqlite3 "$DB" "DELETE FROM UserData WHERE ItemId IN (SELECT Id FROM BaseItems WHERE Type='PLACEHOLDER');"
    sqlite3 "$DB" "DELETE FROM BaseItems WHERE Type='PLACEHOLDER';"
    echo "Removed ${placeholder} PLACEHOLDER item(s)."
  fi

  jellyfin_db_offline_maintenance
}

jellyfin_db_offline_maintenance() {
  python3 <<PY
import sys
from pathlib import Path

sys.path.insert(0, "${SCRIPTS_ROOT}/lib")
from jellyfin_db_lib import connect, prune_orphan_foreign_keys, sanitize_userdata, trim_missing_media_baseitems

db = connect()
deleted = prune_orphan_foreign_keys(db)
if deleted:
    print(f"Pruned {deleted} orphan foreign-key row(s).")
else:
    print("Orphan foreign-key rows: none.")

removed = sanitize_userdata(db)
if removed:
    print(f"Removed {removed} UserData row(s) with non-standard CustomDataKey.")
else:
    print("Corrupt UserData keys: none.")

n = trim_missing_media_baseitems(db)
if n:
    print(f"Missing-file library ghosts: {n} item(s) removed.")
else:
    print("Missing-file library ghosts: none.")
PY
}

trim_lg_queue_state() {
  local RETRIES=$LG_TV_RETRIES
  [[ -f "$RETRIES" ]] || return 0
  local tmp removed=0
  tmp=$(mktemp)
  while IFS=$'\t' read -r path count; do
    [[ -n "$path" ]] || continue
    if [[ -f "$path" ]]; then
      printf '%s\t%s\n' "$path" "${count:-0}"
    else
      removed=$((removed + 1))
    fi
  done < "$RETRIES" > "$tmp"
  mv -f "$tmp" "$RETRIES"
  echo "LG TV retries: dropped ${removed} stale path(s)."
}

fix_mblinks
trim_sqlite
trim_lg_queue_state

echo ""
echo "If libraries look empty or wrong: Dashboard → Libraries → Scan library (all)."
