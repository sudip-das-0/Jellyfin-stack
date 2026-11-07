#!/usr/bin/env python3
"""Jellyfin Webhook: DB delete guard + Sonarr/Radarr sync on ItemDeleted."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

from arr_common import MEDIA_ROOT, SCRIPTS_ROOT  # noqa: E402
from jellyfin_db_lib import (  # noqa: E402
    connect,
    extract_webhook_targets,
    finish_delete,
    prepare_items_for_delete,
    sanitize_userdata,
)

_LAST_SANITIZE = 0.0

LISTEN = ("127.0.0.1", 9407)
SYNC = SCRIPTS_ROOT / "sync-manual-deletes.py"
GUARD = SCRIPTS_ROOT / "jellyfin-delete-guard.py"
_MEDIA = str(MEDIA_ROOT).rstrip("/") + "/"
MEDIA_MARKERS = (_MEDIA, "/media/", "/data/media/")
PATH_KEYS = (
    "path",
    "itempath",
    "filepath",
    "filename",
    "item_path",
    "file_path",
)


def extract_paths(body: bytes, content_type: str) -> list[str]:
    text = body.decode("utf-8", errors="replace")
    found: list[str] = []

    if "json" in content_type.lower() or text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                stack = [data]
                while stack:
                    obj = stack.pop()
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if isinstance(v, (dict, list)):
                                stack.append(v)
                            elif isinstance(v, str) and k.lower() in PATH_KEYS:
                                found.append(v)
                    elif isinstance(obj, list):
                        stack.extend(obj)
        except json.JSONDecodeError:
            pass

    for m in re.finditer(r"(/mnt/jellyfin/media/[^\s\"'<>]+|/media/[^\s\"'<>]+)", text):
        found.append(m.group(1))

    out: list[str] = []
    seen: set[str] = set()
    for p in found:
        if not any(x in p for x in MEDIA_MARKERS):
            continue
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def notification_type(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    for key in ("NotificationType", "notification_type", "Event", "event"):
        val = data.get(key)
        if isinstance(val, str):
            return val
    return ""


def run_db_guard(body: bytes) -> None:
    global _LAST_SANITIZE
    now = time.time()
    if now - _LAST_SANITIZE >= 30:
        conn = connect()
        try:
            n = sanitize_userdata(conn)
            if n:
                sys.stderr.write(
                    f"jellyfin-delete-webhook: periodic sanitize removed {n} UserData row(s)\n"
                )
        finally:
            conn.close()
        _LAST_SANITIZE = now

    item_ids, paths = extract_webhook_targets(body)
    paths.extend(extract_paths(body, "application/json"))
    # de-dupe paths
    seen: set[str] = set()
    uniq_paths: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            uniq_paths.append(p)

    event = notification_type(body)
    conn = connect()
    try:
        if event == "PlaybackStart" and item_ids:
            n = prepare_items_for_delete(conn, item_ids)
            if n:
                sys.stderr.write(
                    f"jellyfin-delete-webhook: PlaybackStart sanitized {n} UserData row(s)\n"
                )
            return

        force = event == "ItemDeleted"
        for iid in item_ids:
            res = finish_delete(
                conn, item_id=iid, force_if_still_present=force
            )
            if res["sanitized_userdata"] or res["purged_ids"]:
                sys.stderr.write(
                    f"jellyfin-delete-webhook: finish-delete id={iid} "
                    f"userdata={res['sanitized_userdata']} purged={res['purged_ids']}\n"
                )
        for p in uniq_paths:
            res = finish_delete(conn, path=p, force_if_still_present=force)
            if res["sanitized_userdata"] or res["purged_ids"]:
                sys.stderr.write(
                    f"jellyfin-delete-webhook: finish-delete path={p} "
                    f"userdata={res['sanitized_userdata']} purged={res['purged_ids']}\n"
                )
    finally:
        conn.close()

    if event == "ItemDeleted" and uniq_paths:
        cmd = [sys.executable, str(SYNC), "--path", *uniq_paths]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"jellyfin-delete-webhook: {fmt % args}\n")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            run_db_guard(body)
        except Exception as exc:
            self.log_message("db guard error: %s", exc)
            subprocess.Popen(
                [sys.executable, str(GUARD), "--purge-ghosts", "--ghost-limit", "20"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_response(404)
        self.end_headers()


def main() -> int:
    server = ThreadingHTTPServer(LISTEN, Handler)
    host, port = LISTEN
    print(f"jellyfin-delete-webhook listening on http://{host}:{port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
