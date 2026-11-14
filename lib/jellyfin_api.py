"""Jellyfin REST helpers: API key from the DB (cached) and JSON requests with timeouts.

Shared by the apply/ and jellyfin/ scripts so each does not re-implement auth, error
handling and DB access (stdlib only).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from arr_common import JELLYFIN_DB, JELLYFIN_URL, MAX_RESPONSE_BYTES


class JellyfinAPIError(RuntimeError):
    """HTTP error from Jellyfin (carries status code and a short body excerpt)."""

    def __init__(self, method: str, path: str, code: int, detail: str) -> None:
        super().__init__(f"{method} {path} -> {code}: {detail}")
        self.method = method
        self.path = path
        self.code = code
        self.detail = detail


def _db_path() -> Path:
    return Path(os.environ.get("JELLYFIN_DB", str(JELLYFIN_DB)))


@lru_cache(maxsize=8)
def api_key(name: str | None = None) -> str:
    """AccessToken of the ApiKeys row called `name`, else the newest key. Cached per process."""
    with closing(sqlite3.connect(_db_path(), timeout=30)) as conn:
        if name:
            row = conn.execute(
                "SELECT AccessToken FROM ApiKeys WHERE Name = ? LIMIT 1", (name,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT AccessToken FROM ApiKeys ORDER BY DateCreated DESC LIMIT 1"
            ).fetchone()
    if not row or not row[0]:
        which = f"named {name!r} " if name else ""
        raise RuntimeError(f"No Jellyfin API key {which}in {_db_path()}")
    return str(row[0])


def jellyfin_request(
    path: str,
    *,
    method: str = "GET",
    body: object | None = None,
    key_name: str | None = None,
    timeout: int = 120,
) -> tuple[int, object | None]:
    """Send an authenticated request. Returns (HTTP status, parsed JSON or None if empty).

    Raises JellyfinAPIError on HTTP errors; URLError/timeouts propagate unchanged.
    """
    headers = {"Authorization": f'MediaBrowser Token="{api_key(key_name)}"'}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{JELLYFIN_URL}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError(f"response larger than {MAX_RESPONSE_BYTES} bytes")
            return resp.status, (json.loads(raw.decode()) if raw else None)
    except urllib.error.HTTPError as exc:
        detail = exc.read(400).decode("utf-8", "replace")
        raise JellyfinAPIError(method, path, exc.code, detail) from exc


def jellyfin_json(
    path: str,
    *,
    method: str = "GET",
    body: object | None = None,
    key_name: str | None = None,
    timeout: int = 120,
) -> object:
    """Like jellyfin_request but returns only the body; empty responses become {"status": code}."""
    status, data = jellyfin_request(
        path, method=method, body=body, key_name=key_name, timeout=timeout
    )
    return {"status": status} if data is None else data


def wait_ready(seconds: int = 90) -> None:
    """Block until /System/Info/Public answers, else raise RuntimeError."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{JELLYFIN_URL}/System/Info/Public", timeout=3) as r:
                r.read()
            return
        except OSError:
            time.sleep(2)
    raise RuntimeError(f"Jellyfin did not answer at {JELLYFIN_URL} within {seconds}s")
