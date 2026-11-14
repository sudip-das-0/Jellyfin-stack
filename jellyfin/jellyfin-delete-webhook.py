#!/usr/bin/env python3
"""Jellyfin Webhook: DB delete guard + Sonarr/Radarr sync on ItemDeleted.

Listens on 127.0.0.1:9407 (loopback only). Runs as jellyfin-delete-webhook.service.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from arr_common import SCRIPTS_ROOT  # noqa: E402
from jellyfin_db_lib import (  # noqa: E402
    db,
    extract_webhook_targets,
    finish_delete,
    prepare_items_for_delete,
    safe_media_path,
    sanitize_userdata,
    webhook_event,
)

LISTEN = ("127.0.0.1", 9407)
SYNC = SCRIPTS_ROOT / "maintenance" / "sync-manual-deletes.py"
GUARD = SCRIPTS_ROOT / "jellyfin" / "jellyfin-delete-guard.py"
MAX_BODY = 1 << 20  # 1 MiB; Jellyfin webhook payloads are a few KiB
SANITIZE_INTERVAL_SEC = 30

# SQLite allows one writer; serialize guard runs and the periodic sanitize timestamp.
_DB_LOCK = threading.Lock()
_last_sanitize = float("-inf")


def log(msg: str) -> None:
    sys.stderr.write(f"jellyfin-delete-webhook: {msg}\n")
    sys.stderr.flush()


def _spawn(cmd: list[str]) -> None:
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _periodic_sanitize(conn) -> None:
    global _last_sanitize
    now = time.monotonic()
    if now - _last_sanitize < SANITIZE_INTERVAL_SEC:
        return
    n = sanitize_userdata(conn)
    if n:
        log(f"periodic sanitize removed {n} UserData row(s)")
    _last_sanitize = now


def run_db_guard(body: bytes) -> None:
    item_ids, raw_paths = extract_webhook_targets(body)
    paths = [p for p in raw_paths if safe_media_path(p)]
    if len(paths) != len(raw_paths):
        log(f"ignored {len(raw_paths) - len(paths)} unsafe path(s) in payload")
    event = webhook_event(body)

    with _DB_LOCK, db() as conn:
        _periodic_sanitize(conn)

        if event == "PlaybackStart" and item_ids:
            n = prepare_items_for_delete(conn, item_ids)
            if n:
                log(f"PlaybackStart sanitized {n} UserData row(s)")
            return

        targets = [("id", i, {"item_id": i}) for i in item_ids]
        targets += [("path", p, {"path": p}) for p in paths]
        for kind, value, kwargs in targets:
            res = finish_delete(conn, **kwargs)
            if res["sanitized_userdata"] or res["purged_ids"]:
                log(
                    f"finish-delete {kind}={value} "
                    f"userdata={res['sanitized_userdata']} purged={res['purged_ids']}"
                )

    if event == "ItemDeleted" and paths:
        _spawn([sys.executable, str(SYNC), "--path", *paths])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        log(fmt % args)

    def _reply(self, code: int, body: bytes = b"") -> None:
        self.send_response(code)
        if code != 204:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._reply(400)
        if length < 0:
            return self._reply(400)
        if length > MAX_BODY:
            return self._reply(413)
        body = self.rfile.read(length) if length else b""
        try:
            run_db_guard(body)
        except Exception:  # keep Jellyfin's webhook sender happy, but never hide the cause
            log("db guard error:\n" + traceback.format_exc())
            _spawn([sys.executable, str(GUARD), "--purge-ghosts", "--ghost-limit", "20"])
        self._reply(204)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            return self._reply(200, b"ok")
        self._reply(404)


def main() -> int:
    server = ThreadingHTTPServer(LISTEN, Handler)
    server.daemon_threads = True
    host, port = LISTEN
    print(f"jellyfin-delete-webhook listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
