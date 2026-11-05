#!/usr/bin/env bash
# Jellyfin DB + library path cleanup (legacy /media index, phantom movies, orphans).
set -euo pipefail

DB=/mnt/jellyfin/jellyfin/config/data/jellyfin.db
CONFIG_ROOT=/mnt/jellyfin/jellyfin/config/root/default
JELLYFIN_URL="${JELLYFIN_URL:-http://127.0.0.1:8096}"
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
  python3 <<'PY'
import json, sqlite3
db = sqlite3.connect("/mnt/jellyfin/jellyfin/config/data/jellyfin.db")
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

  trim_missing_media_baseitems
}

trim_missing_media_baseitems() {
  python3 <<'PY'
import os
import sqlite3

DB = "/mnt/jellyfin/jellyfin/config/data/jellyfin.db"
HOST_ROOT = "/mnt/jellyfin"


def host_path(db_path: str) -> str | None:
    if not db_path:
        return None
    if db_path.startswith("/media/"):
        return HOST_ROOT + db_path[len("/media") :]
    if db_path.startswith("/mnt/jellyfin/"):
        return db_path
    return None


db = sqlite3.connect(DB)
rows = db.execute(
    "SELECT Id, Type, path FROM BaseItems WHERE path IS NOT NULL AND path != ''"
).fetchall()
missing: list[str] = []
for item_id, _typ, path in rows:
    hp = host_path(path)
    if not hp:
        continue
    if os.path.exists(hp):
        continue
    if _typ.endswith("Series") or _typ.endswith("Season"):
        if os.path.isdir(hp):
            continue
    missing.append(item_id)

if not missing:
    print("Missing-file library ghosts: none.")
    raise SystemExit(0)

print(f"Missing-file library ghosts: {len(missing)} item(s)")
placeholders = ",".join("?" for _ in missing)
for table, col in (
    ("UserData", "ItemId"),
    ("ItemValuesMap", "ItemId"),
    ("PeopleBaseItemMap", "ItemId"),
    ("BaseItemImageInfos", "ItemId"),
    ("BaseItemProviders", "ItemId"),
    ("BaseItemMetadataFields", "ItemId"),
    ("MediaStreamInfos", "ItemId"),
    ("Chapters", "ItemId"),
    ("KeyframeData", "ItemId"),
    ("AttachmentStreamInfos", "ItemId"),
    ("MediaSegments", "ItemId"),
    ("TrickplayInfos", "ItemId"),
):
    try:
        db.execute(
            f"DELETE FROM {table} WHERE {col} IN ({placeholders})", missing
        )
    except sqlite3.OperationalError:
        pass
try:
    db.execute(
        f"DELETE FROM AncestorIds WHERE ItemId IN ({placeholders}) OR ParentItemId IN ({placeholders})",
        missing + missing,
    )
except sqlite3.OperationalError:
    pass
db.execute(
    f"DELETE FROM BaseItems WHERE Id IN ({placeholders}) AND Type LIKE '%Episode%'",
    missing,
)
db.execute(
    f"DELETE FROM BaseItems WHERE Id IN ({placeholders}) AND Type LIKE '%Season%'",
    missing,
)
db.execute(f"DELETE FROM BaseItems WHERE Id IN ({placeholders})", missing)
db.commit()
print(f"Removed {len(missing)} BaseItems whose path is missing on disk.")
PY
}

trim_lg_queue_state() {
  # shellcheck source=stack-paths.sh
  source /mnt/jellyfin/scripts/stack-paths.sh
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
