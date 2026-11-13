"""Shared helpers for *arr / qBittorrent maintenance scripts (stdlib only).

Sections: paths · HTTP (*arr/qBit) · queue/notifications · logging · stall helpers
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")
CONFIG_ROOT = Path(os.environ.get("CONFIG_ROOT", os.path.expanduser("~/arr")))
SCRIPTS_ROOT = Path(os.environ.get("SCRIPTS_ROOT", str(Path(__file__).resolve().parent.parent)))
LIB_DIR = SCRIPTS_ROOT / "lib"
HOOKS_DIR = SCRIPTS_ROOT / "hooks"
HOST_DIR = SCRIPTS_ROOT / "host"
TEMP_DIR = SCRIPTS_ROOT / "temp"
ARR_ROOT = CONFIG_ROOT  # *arr Docker config on internal NVMe (not in git)
JELLYFIN_MNT = Path(os.environ.get("JELLYFIN_MNT", "/mnt/jellyfin"))
JELLYFIN_UUID = os.environ.get("JELLYFIN_UUID", "0caa73f3-4f57-4549-8e6d-fbf323866d28")
JELLYFIN_MARKER = JELLYFIN_MNT / ".jellyfin-external-drive"
MEDIA_ROOT = JELLYFIN_MNT / "media"
TORRENTS_ROOT = JELLYFIN_MNT / "torrents"
JELLYFIN_CONFIG_ROOT = JELLYFIN_MNT / "jellyfin" / "config"
JELLYFIN_DB = JELLYFIN_CONFIG_ROOT / "data" / "jellyfin.db"
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8096")
BAZARR_URL = os.environ.get("BAZARR_URL", "http://127.0.0.1:6767")
LOG_ROOT = JELLYFIN_MNT / "logs"
STALL_SECONDS_DEFAULT = int(os.environ.get("STALL_SECONDS_DEFAULT", "43200"))
STALL_LOG_DIR = LOG_ROOT / "stall-recovery"
# Shared by queue-dedupe.py (reset on trim) and torrent-stall-recovery.py (stall jobs).
STALL_RECOVERY_STATE_PATH = STALL_LOG_DIR / "state.json"
LG_TV_DIR = LOG_ROOT / "lg-tv"
# qBittorrent states that use a max-active-download slot (see config/qbit-network-profiles.json).
QBIT_ACTIVE_DOWNLOAD_STATES = frozenset(
    {"downloading", "metaDL", "forcedDL", "stalledDL", "checkingDL"}
)
SONARR_CONFIG = ARR_ROOT / "config/sonarr/config.xml"
RADARR_CONFIG = ARR_ROOT / "config/radarr/config.xml"
BAZARR_CONFIG = ARR_ROOT / "config/bazarr/config/config.yaml"
BAZARR_DB = ARR_ROOT / "config/bazarr/db/bazarr.db"
SONARR_URL = os.environ.get("SONARR_URL", "http://127.0.0.1:8989")
RADARR_URL = os.environ.get("RADARR_URL", "http://127.0.0.1:7878")
QBIT_URL = os.environ.get("QBIT_URL", "http://127.0.0.1:8080")

_MEDIA_LANG_POLICY = None


def load_media_lang_policy():
    """Load lib/media-lang-policy.py (hyphenated filename, not a package)."""
    global _MEDIA_LANG_POLICY
    if _MEDIA_LANG_POLICY is None:
        import importlib.util

        path = Path(__file__).resolve().parent / "media-lang-policy.py"
        spec = importlib.util.spec_from_file_location("media_lang_policy", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MEDIA_LANG_POLICY = mod
    return _MEDIA_LANG_POLICY


def read_api_key(path: Path | None = None) -> str:
    path = path or SONARR_CONFIG
    text = path.read_text(encoding="utf-8")
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", text)
    if not m:
        raise RuntimeError(f"No ApiKey in {path}")
    return m.group(1)


def read_bazarr_api_key() -> str:
    text = BAZARR_CONFIG.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("apikey:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"No apikey in {BAZARR_CONFIG}")


def arr_json(
    url: str,
    api_key: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    timeout: int = 120,
) -> object:
    data = None
    headers = {"X-Api-Key": api_key}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw.decode()) if raw else {}


def norm_qbit_hash(h: str) -> str:
    return h.upper()


def holds_active_qbit_download_slot(t: dict) -> bool:
    """True when torrent holds one of qBit's max-active-download slots (not queued/waiting)."""
    return (t.get("state") or "") in QBIT_ACTIVE_DOWNLOAD_STATES


def torrent_has_download_progress(t: dict | None) -> bool:
    """Do not remove from qBit / Sonarr queue when a grab still has data in flight."""
    if not t:
        return False
    prog = float(t.get("progress") or 0)
    if prog >= 0.999:
        return False
    min_prog = float(os.environ.get("DEDUPE_PROTECT_PROGRESS", "0.001"))
    if prog > min_prog:
        return True
    if int(t.get("dlspeed") or 0) > 0:
        return True
    state = t.get("state") or ""
    if state in QBIT_ACTIVE_DOWNLOAD_STATES:
        return True
    if state in ("stalledDL", "queuedDL", "checkingDL") and prog > 0:
        return True
    return False


def _queue_row_has_active_download(row: dict, tmap: dict[str, dict]) -> bool:
    did = row.get("downloadId")
    if did:
        if torrent_has_download_progress(tmap.get(norm_qbit_hash(did))):
            return True
    st = (row.get("status") or "").lower()
    tracked = (row.get("trackedDownloadStatus") or "").lower()
    if st in ("downloading", "queued", "warning") or tracked in ("downloading", "ok"):
        # Row is tied to a client job even if qBit API is briefly unavailable.
        if did:
            return True
    return False


def episode_ids_with_active_downloads(api_key: str, tmap: dict[str, dict] | None = None) -> set[int]:
    """Episode IDs that already have a Sonarr queue row with an in-progress torrent."""
    if tmap is None:
        tmap = {norm_qbit_hash(t["hash"]): t for t in qbit_torrents()}
    active: set[int] = set()
    for row in fetch_arr_queue(SONARR_URL, api_key):
        eid = row.get("episodeId")
        if eid is None:
            continue
        if _queue_row_has_active_download(row, tmap):
            active.add(int(eid))
    return active


def movie_ids_with_active_downloads(api_key: str, tmap: dict[str, dict] | None = None) -> set[int]:
    if tmap is None:
        tmap = {norm_qbit_hash(t["hash"]): t for t in qbit_torrents()}
    active: set[int] = set()
    for row in fetch_arr_queue(RADARR_URL, api_key):
        mid = row.get("movieId")
        if mid is None:
            continue
        if _queue_row_has_active_download(row, tmap):
            active.add(int(mid))
    return active


def wanted_missing_count(base_url: str, api_key: str) -> int:
    q = arr_json(f"{base_url}/api/v3/wanted/missing?pageSize=1", api_key)
    if isinstance(q, dict):
        return int(q.get("totalRecords") or 0)
    return 0


def queue_arr_command(base_url: str, api_key: str, name: str, **body_fields: object) -> dict:
    """POST /api/v3/command; returns the command object."""
    body = {"name": name, **body_fields}
    result = arr_json(f"{base_url}/api/v3/command", api_key, method="POST", body=body)
    return result if isinstance(result, dict) else {}


def append_script_log(log_path: Path, msg: str, *, rotate: bool = True) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if rotate:
        rotate_log_file(log_path)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


def echo_script_log(
    log_path: Path,
    msg: str,
    *,
    rotate: bool = False,
    echo: bool = True,
) -> None:
    """Append msg to log_path; print when echo is true."""
    append_script_log(log_path, msg, rotate=rotate)
    if echo:
        print(msg)


def qbit_torrents() -> list[dict]:
    data = qbit_json("/api/v2/torrents/info")
    return data if isinstance(data, list) else []


def qbit_delete(hashes: list[str], *, delete_files: bool = True) -> None:
    if not hashes:
        return
    body = urllib.parse.urlencode(
        {"hashes": "|".join(hashes), "deleteFiles": "true" if delete_files else "false"}
    ).encode()
    qbit_json("/api/v2/torrents/delete", method="POST", data=body)


def qbit_remove_client_only(hashes: list[str]) -> None:
    """Drop torrent jobs from qBittorrent; keep files on TORRENTS_ROOT."""
    qbit_delete(hashes, delete_files=False)


def fetch_arr_queue(base_url: str, api_key: str) -> list[dict]:
    records: list[dict] = []
    page = 1
    while True:
        extra = "&includeUnknownSeriesItems=true" if base_url.rstrip("/") == SONARR_URL.rstrip("/") else ""
        q = arr_json(f"{base_url}/api/v3/queue?page={page}&pageSize=100{extra}", api_key)
        if not isinstance(q, dict):
            break
        batch = q.get("records") or []
        records.extend(batch)
        if page * q.get("pageSize", 100) >= q.get("totalRecords", 0):
            break
        page += 1
    return records


def arr_queue_remove(
    base_url: str,
    api_key: str,
    queue_id: int,
    *,
    remove_from_client: bool = True,
    blocklist: bool = False,
    skip_redownload: bool = True,
) -> bool:
    qs = urllib.parse.urlencode(
        {
            "removeFromClient": "true" if remove_from_client else "false",
            "blocklist": "true" if blocklist else "false",
            "skipRedownload": "true" if skip_redownload else "false",
        }
    )
    url = f"{base_url}/api/v3/queue/{queue_id}?{qs}"
    req = urllib.request.Request(url, method="DELETE", headers={"X-Api-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def jellyfin_volume_ready(*, min_free_mb: int | None = None) -> bool:
    """True when bash ensure-jellyfin-media-mount.sh succeeds (mount + I/O + free space)."""
    import subprocess

    env = os.environ.copy()
    if min_free_mb is not None:
        env["JELLYFIN_MIN_FREE_MB"] = str(min_free_mb)
    try:
        subprocess.run(
            [str(HOST_DIR / "ensure-jellyfin-media-mount.sh")],
            capture_output=True,
            timeout=60,
            env=env,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


def qbit_json(
    path: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    user: str | None = None,
    password: str | None = None,
    timeout: int = 120,
) -> object:
    user = user or os.environ.get("QBIT_USER", "Dimpu")
    password = password or os.environ.get("QBIT_PASS", "adminadmin")
    req = urllib.request.Request(f"{QBIT_URL}{path}", data=data, method=method)
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw.decode()) if raw else None


def rotate_log_file(path: Path, max_bytes: int = 2_000_000) -> None:
    if not path.is_file() or path.stat().st_size <= max_bytes:
        return
    old = path.with_suffix(path.suffix + ".old")
    if old.exists():
        old.unlink()
    path.replace(old)


def trim_log_tail(path: Path, max_bytes: int = 5_000_000, keep_bytes: int = 2_000_000) -> None:
    if not path.is_file():
        return
    size = path.stat().st_size
    if size <= max_bytes:
        return
    with path.open("rb") as f:
        f.seek(max(0, size - keep_bytes))
        data = f.read()
    path.write_bytes(data)


def prune_dated_logs(directory: Path, prefix: str, *, keep_days: int = 14) -> int:
    """Remove ``prefixYYYY-MM-DD.log`` files older than keep_days."""
    if not directory.is_dir():
        return 0
    cutoff = datetime.now(TZ).date() - timedelta(days=keep_days)
    removed = 0
    pat = re.compile(rf"^{re.escape(prefix)}(\d{{4}}-\d{{2}}-\d{{2}})\.log$")
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        m = pat.match(entry.name)
        if not m:
            continue
        try:
            day = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            entry.unlink(missing_ok=True)
            removed += 1
    return removed


def qbit_completed_grace_hashes() -> set[str]:
    """Recently finished torrents — allow time for Sonarr/Radarr import before qBit cleanup."""
    import time

    grace = int(os.environ.get("QBIT_COMPLETED_PROTECT_SEC", "7200"))
    protected: set[str] = set()
    try:
        torrents = qbit_torrents()
    except urllib.error.URLError:
        return protected
    if not isinstance(torrents, list):
        return protected
    now = time.time()
    for t in torrents:
        progress = float(t.get("progress") or 0)
        if progress < 0.999:
            continue
        comp = int(t.get("completion_on") or 0)
        if comp > 0 and (now - comp) < grace:
            h = t.get("hash")
            if h:
                protected.add(str(h).casefold())
    return protected


def qbit_queue_protected_hashes() -> set[str]:
    """qBittorrent info-hashes still tracked in Sonarr/Radarr download queues."""
    protected: set[str] = set()
    for base_url, config in (
        (SONARR_URL, SONARR_CONFIG),
        (RADARR_URL, RADARR_CONFIG),
    ):
        api_key = read_api_key(config)
        for row in fetch_arr_queue(base_url, api_key):
            download_id = row.get("downloadId")
            if download_id:
                protected.add(str(download_id).casefold())
    return protected


def qbit_import_blocked_hashes() -> set[str]:
    """Torrents Sonarr/Radarr marked completed in client but import is still blocked (keep data)."""
    protected: set[str] = set()
    for base_url, config in (
        (SONARR_URL, SONARR_CONFIG),
        (RADARR_URL, RADARR_CONFIG),
    ):
        api_key = read_api_key(config)
        for row in fetch_arr_queue(base_url, api_key):
            state = (row.get("trackedDownloadState") or "").casefold()
            status = (row.get("trackedDownloadStatus") or "").casefold()
            if state == "importblocked" or status == "warning":
                download_id = row.get("downloadId")
                if download_id:
                    protected.add(str(download_id).casefold())
    return protected


def qbit_all_protected_hashes() -> set[str]:
    """Queue rows + import-blocked + post-complete import grace window."""
    out = qbit_queue_protected_hashes()
    out |= qbit_import_blocked_hashes()
    out |= qbit_completed_grace_hashes()
    return out


def append_recovery_log(
    msg: str,
    *,
    tag: str | None = "maint",
    echo: bool = False,
) -> None:
    STALL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    ts = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"{ts} [{tag}] {msg}" if tag else f"{ts} {msg}"
    with (STALL_LOG_DIR / f"recovery-{day}.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    if echo:
        print(line)


def iter_incomplete_seasons(
    api_key: str,
    *,
    partial_only: bool = False,
) -> list[tuple[str, int, int, int, int]]:
    """
    Returns list of (title, series_id, season_number, have, total) for monitored
    seasons that are incomplete on disk. partial_only: only 0 < have < total.
    """
    series_list = arr_json(f"{SONARR_URL}/api/v3/series", api_key)
    if not isinstance(series_list, list):
        return []

    out: list[tuple[str, int, int, int, int]] = []
    for show in series_list:
        sid = show["id"]
        title = show.get("title", "?")
        detail = arr_json(f"{SONARR_URL}/api/v3/series/{sid}", api_key)
        if not isinstance(detail, dict):
            continue
        for season in detail.get("seasons") or []:
            if not season.get("monitored"):
                continue
            sn = season.get("seasonNumber")
            if sn is None or sn < 1:
                continue
            stats = season.get("statistics") or {}
            total = int(stats.get("totalEpisodeCount") or 0)
            have = int(stats.get("episodeFileCount") or 0)
            if total <= 0 or have >= total:
                continue
            if partial_only and not (0 < have < total):
                continue
            out.append((title, sid, int(sn), have, total))
    return out


def video_container_magic_ok(path: Path) -> bool:
    """Quick host-side check: file starts like a real MKV/MP4 (not sparse/qBit placeholder)."""
    try:
        with path.open("rb") as f:
            head = f.read(12)
    except OSError:
        return False
    if len(head) < 4:
        return False
    ext = path.suffix.lower()
    if ext in {".mkv", ".webm"}:
        return head[:4] == b"\x1aE\xdf\xa3"
    if ext in {".mp4", ".m4v"}:
        return len(head) >= 8 and head[4:8] == b"ftyp"
    return True

