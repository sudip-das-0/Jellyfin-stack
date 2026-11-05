#!/usr/bin/env python3
"""Listen for Jellyfin Webhook plugin ItemDeleted events → sync Sonarr/Radarr."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LISTEN = ("127.0.0.1", 9407)
SYNC = Path("/mnt/jellyfin/scripts/sync-manual-deletes.py")
MEDIA_MARKERS = ("/mnt/jellyfin/media/", "/media/", "/data/media/")
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"jellyfin-delete-webhook: {fmt % args}\n")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        paths = extract_paths(body, self.headers.get("Content-Type", ""))
        if paths:
            cmd = [sys.executable, str(SYNC), "--path", *paths]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.log_message("queued sync for %d path(s)", len(paths))
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
