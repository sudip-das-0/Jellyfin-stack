#!/usr/bin/env python3
"""Jellyfin SQLite helpers: safe purge of movies/episodes and UserData sanitization.

Used by jellyfin-delete-guard.py, jellyfin-delete-webhook.py, jellyfin-db-trim.sh.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, "/mnt/jellyfin/scripts/lib")
from arr_common import JELLYFIN_DB, JELLYFIN_MNT  # noqa: E402

DB = Path(os.environ.get("JELLYFIN_DB", str(JELLYFIN_DB)))
HOST_ROOT = Path(os.environ.get("JELLYFIN_HOST_ROOT", str(JELLYFIN_MNT)))
MARKER = HOST_ROOT / ".jellyfin-external-drive"

MEDIA_TYPES = ("%Movie%", "%Episode%")

# Known-good UserData keys (Jellyfin core + common plugins).
ALLOWED_USERDATA_KEYS = frozenset(
    {
        "",
        "IntroSkipState",
        "introskipper",
    }
)

CHILD_TABLES: tuple[tuple[str, str], ...] = (
    ("UserData", "ItemId"),
    ("ItemValuesMap", "ItemId"),
    ("PeopleBaseItemMap", "ItemId"),
    ("BaseItemImageInfos", "ItemId"),
    ("BaseItemProviders", "ItemId"),
    ("BaseItemMetadataFields", "ItemId"),
    ("BaseItemTrailerTypes", "ItemId"),
    ("MediaStreamInfos", "ItemId"),
    ("Chapters", "ItemId"),
    ("KeyframeData", "ItemId"),
    ("AttachmentStreamInfos", "ItemId"),
    ("MediaSegments", "ItemId"),
    ("TrickplayInfos", "ItemId"),
)

ORPHAN_FK_PAIRS: tuple[tuple[str, str, str, str], ...] = (
    ("PeopleBaseItemMap", "ItemId", "BaseItems", "Id"),
    ("ItemValuesMap", "ItemId", "BaseItems", "Id"),
    ("AncestorIds", "ItemId", "BaseItems", "Id"),
    ("AncestorIds", "ParentItemId", "BaseItems", "Id"),
    ("BaseItemProviders", "ItemId", "BaseItems", "Id"),
    ("BaseItemImageInfos", "ItemId", "BaseItems", "Id"),
    ("UserData", "ItemId", "BaseItems", "Id"),
    ("KeyframeData", "ItemId", "BaseItems", "Id"),
    ("HomeSection", "DisplayPreferencesId", "DisplayPreferences", "Id"),
)


def media_library_mount_healthy() -> bool:
    """True only when the jellyfin media SSD is mounted and readable."""
    media = HOST_ROOT / "media"
    try:
        if not MARKER.is_file():
            return False
        if not media.is_dir():
            return False
        next(media.iterdir())
        return True
    except OSError:
        return False


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def is_corrupt_userdata_key(key: str) -> bool:
    if key in ALLOWED_USERDATA_KEYS:
        return False
    if key.startswith("tt") and len(key) > 2:
        rest = key[2:]
        if rest.isdigit() or (rest.replace("0", "").isdigit() and rest):
            return True
    if key.isdigit():
        return True
    if len(key) == 36 and key.count("-") == 4:
        return True
    return False


def container_path_to_host(path: str) -> Path | None:
    if not path:
        return None
    if path.startswith("/media/"):
        return HOST_ROOT / "media" / path[len("/media/") :]
    if path.startswith("/mnt/jellyfin/"):
        return Path(path)
    return None


def sanitize_userdata(
    conn: sqlite3.Connection,
    *,
    item_id: str | None = None,
) -> int:
    """Remove UserData rows whose CustomDataKey breaks Jellyfin's in-app delete."""
    if item_id:
        rows = conn.execute(
            "SELECT CustomDataKey FROM UserData WHERE ItemId = ?",
            (item_id.upper(),),
        ).fetchall()
        bad = [r[0] for r in rows if is_corrupt_userdata_key(r[0])]
        if not bad:
            return 0
        ph = ",".join("?" for _ in bad)
        cur = conn.execute(
            f"DELETE FROM UserData WHERE ItemId = ? AND CustomDataKey IN ({ph})",
            (item_id.upper(), *bad),
        )
        conn.commit()
        return cur.rowcount

    cur = conn.execute("SELECT ItemId, CustomDataKey FROM UserData")
    to_drop: list[tuple[str, str]] = []
    for iid, key in cur.fetchall():
        if is_corrupt_userdata_key(key):
            to_drop.append((iid, key))
    if not to_drop:
        return 0
    for iid, key in to_drop:
        conn.execute(
            "DELETE FROM UserData WHERE ItemId = ? AND CustomDataKey = ?",
            (iid, key),
        )
    conn.commit()
    return len(to_drop)


def purge_baseitems(conn: sqlite3.Connection, item_ids: Iterable[str]) -> int:
    ids = [i.upper() for i in item_ids if i]
    if not ids:
        return 0
    ph = ",".join("?" for _ in ids)
    for table, col in CHILD_TABLES:
        try:
            conn.execute(f'DELETE FROM "{table}" WHERE "{col}" IN ({ph})', ids)
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute(
            f'DELETE FROM "AncestorIds" WHERE "ItemId" IN ({ph}) OR "ParentItemId" IN ({ph})',
            ids + ids,
        )
    except sqlite3.OperationalError:
        pass
    cur = conn.execute(f'DELETE FROM "BaseItems" WHERE "Id" IN ({ph})', ids)
    conn.commit()
    return cur.rowcount


def item_exists(conn: sqlite3.Connection, item_id: str) -> bool:
    row = conn.execute(
        'SELECT 1 FROM "BaseItems" WHERE "Id" = ? LIMIT 1',
        (item_id.upper(),),
    ).fetchone()
    return row is not None


def lookup_ids_by_path(conn: sqlite3.Connection, raw_path: str) -> list[str]:
    """Resolve Jellyfin BaseItems ids for a container or host media path."""
    raw = (raw_path or "").strip()
    if not raw:
        return []
    candidates: set[str] = {raw}
    media_root = HOST_ROOT / "media"
    if raw.startswith("/media/"):
        hp = media_root / raw[len("/media/") :]
        candidates.add(str(hp))
    elif raw.startswith(str(HOST_ROOT)):
        p = Path(raw)
        try:
            rel = p.relative_to(media_root)
            candidates.add("/media/" + rel.as_posix())
        except ValueError:
            pass

    ids: list[str] = []
    seen: set[str] = set()
    for p in candidates:
        for row in conn.execute(
            'SELECT "Id" FROM "BaseItems" WHERE "Path" = ? AND (Type LIKE ? OR Type LIKE ?)',
            (p, *MEDIA_TYPES),
        ):
            iid = row[0]
            if iid not in seen:
                seen.add(iid)
                ids.append(iid)
    return ids


def media_file_missing(conn: sqlite3.Connection, item_id: str) -> bool | None:
    row = conn.execute(
        'SELECT Path, Type FROM "BaseItems" WHERE "Id" = ?',
        (item_id.upper(),),
    ).fetchone()
    if not row:
        return None
    path, typ = row
    if not path or not (typ.endswith("Movie") or typ.endswith("Episode")):
        return None
    hp = container_path_to_host(path)
    if not hp:
        return None
    return not hp.is_file()


def finish_delete(
    conn: sqlite3.Connection,
    *,
    item_id: str | None = None,
    path: str | None = None,
    force_if_still_present: bool = False,
) -> dict[str, Any]:
    """After UI delete: drop bad UserData and purge any leftover movie/episode row."""
    result: dict[str, Any] = {
        "sanitized_userdata": 0,
        "purged_ids": [],
    }
    target_ids: list[str] = []
    if item_id:
        target_ids.append(item_id.upper())
    if path:
        for iid in lookup_ids_by_path(conn, path):
            if iid not in target_ids:
                target_ids.append(iid)

    for iid in list(target_ids):
        result["sanitized_userdata"] += sanitize_userdata(conn, item_id=iid)

    purge: list[str] = []
    mount_ok = media_library_mount_healthy()
    for iid in target_ids:
        if not item_exists(conn, iid):
            continue
        missing = media_file_missing(conn, iid)
        if not mount_ok:
            continue
        if missing is True:
            purge.append(iid)

    if purge:
        purge_baseitems(conn, purge)
        result["purged_ids"] = purge
    return result


def _item_missing_on_disk(typ: str, path: str, *, unknown_path_missing: bool) -> bool:
    hp = container_path_to_host(path)
    if hp is None:
        return unknown_path_missing
    if hp.is_file():
        return False
    if typ.endswith("Series") or typ.endswith("Season"):
        return not hp.is_dir()
    return True


def collect_missing_baseitem_ids(
    conn: sqlite3.Connection,
    *,
    type_filter_sql: str = "",
    limit: int | None = None,
    unknown_path_missing: bool = False,
) -> list[str]:
    rows = conn.execute(
        f"""SELECT Id, Type, Path FROM BaseItems
           WHERE Path IS NOT NULL AND Path != '' {type_filter_sql}"""
    ).fetchall()
    missing: list[str] = []
    for item_id, typ, path in rows:
        if _item_missing_on_disk(typ, path, unknown_path_missing=unknown_path_missing):
            missing.append(item_id)
        if limit is not None and len(missing) >= limit:
            break
    return missing


def _purge_missing_baseitems(conn: sqlite3.Connection, item_ids: list[str]) -> int:
    if not item_ids:
        return 0
    for iid in item_ids:
        sanitize_userdata(conn, item_id=iid)
    purge_baseitems(conn, item_ids)
    return len(item_ids)


def purge_delete_ghosts(conn: sqlite3.Connection, *, limit: int = 100) -> int:
    """Remove movie/episode rows whose media file is gone (failed UI delete cleanup)."""
    if not media_library_mount_healthy():
        return 0
    missing = collect_missing_baseitem_ids(
        conn,
        type_filter_sql="AND (Type LIKE '%Movie%' OR Type LIKE '%Episode%')",
        limit=limit,
        unknown_path_missing=False,
    )
    return _purge_missing_baseitems(conn, missing)


def prune_orphan_foreign_keys(conn: sqlite3.Connection) -> int:
    deleted = 0
    cur = conn.execute(
        """DELETE FROM BaseItems WHERE ParentId IS NOT NULL AND ParentId != ''
           AND ParentId NOT IN (SELECT Id FROM BaseItems)"""
    )
    deleted += cur.rowcount
    for table, col, parent, pcol in ORPHAN_FK_PAIRS:
        cur = conn.execute(
            f'DELETE FROM "{table}" WHERE "{col}" NOT IN (SELECT "{pcol}" FROM "{parent}")'
        )
        deleted += cur.rowcount
    conn.commit()
    return deleted


def extract_webhook_targets(body: bytes) -> tuple[list[str], list[str]]:
    """Return (item_ids, paths) from Jellyfin Webhook JSON body."""
    item_ids: list[str] = []
    paths: list[str] = []
    seen_i: set[str] = set()
    seen_p: set[str] = set()
    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return item_ids, paths

    id_keys = frozenset(
        {
            "itemid",
            "id",
            "item_id",
        }
    )
    path_keys = frozenset(
        {
            "path",
            "itempath",
            "filepath",
            "filename",
            "item_path",
            "file_path",
        }
    )

    stack: list[Any] = [data]
    while stack:
        obj = stack.pop()
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = k.lower()
                if isinstance(v, str):
                    if kl in id_keys and len(v) >= 32 and v.count("-") >= 4:
                        uid = v.upper()
                        if uid not in seen_i:
                            seen_i.add(uid)
                            item_ids.append(uid)
                    if kl in path_keys and ("/media/" in v or "/mnt/jellyfin/media/" in v):
                        if v not in seen_p:
                            seen_p.add(v)
                            paths.append(v)
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(obj, list):
            stack.extend(obj)
    return item_ids, paths


def prepare_items_for_delete(conn: sqlite3.Connection, item_ids: Iterable[str]) -> int:
    """Sanitize UserData before Jellyfin deletes (PlaybackStart / proactive)."""
    n = 0
    for iid in item_ids:
        n += sanitize_userdata(conn, item_id=iid)
    return n


def trim_missing_media_baseitems(conn: sqlite3.Connection) -> int:
    """Remove movie/episode (etc.) rows whose path does not exist on disk (Jellyfin stopped)."""
    if not media_library_mount_healthy():
        return 0
    missing = collect_missing_baseitem_ids(conn, unknown_path_missing=False)
    return _purge_missing_baseitems(conn, missing)
