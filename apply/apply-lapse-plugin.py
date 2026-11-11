#!/usr/bin/env python3
"""Install LAPSE, enable it on every library, and start a bulk subtitle sync."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, "/mnt/jellyfin/scripts/lib")
from arr_common import BAZARR_CONFIG, JELLYFIN_CONFIG_ROOT, JELLYFIN_DB, JELLYFIN_URL  # noqa: E402

PLUGIN_GUID = "486090e1-ca92-46e1-8549-9f6bb914a1d0"
PLUGIN_VERSION = "2.1.4.0"
PLUGIN_DIR = JELLYFIN_CONFIG_ROOT / "plugins" / f"LAPSE_{PLUGIN_VERSION}"
REPO_NAME = "LAPSE"
REPO_URL = "https://raw.githubusercontent.com/rs-jensen/lapse-jellyfin-plugin/main/manifest.json"
SYSTEM_XML = JELLYFIN_CONFIG_ROOT / "config" / "system.xml"
DLL_URL = "https://github.com/rs-jensen/lapse-jellyfin-plugin/releases/download/v2.1.4/lapse-jellyfin-plugin-v2.1.4.zip"


def jellyfin_token() -> str:
    con = sqlite3.connect(str(JELLYFIN_DB))
    try:
        row = con.execute("SELECT AccessToken FROM ApiKeys LIMIT 1").fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        raise RuntimeError("No Jellyfin API key")
    return str(row[0])


def jf(path: str, *, method: str = "GET", body: object | None = None, timeout: int = 120) -> object:
    data = None
    headers = {"Authorization": f'MediaBrowser Token="{jellyfin_token()}"'}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{JELLYFIN_URL}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.status in (202, 204) and not raw:
                return {"status": resp.status}
            return json.loads(raw.decode()) if raw else {"status": resp.status}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc


def wait_jellyfin(seconds: int = 90) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{JELLYFIN_URL}/System/Info/Public", timeout=3).read()
            return
        except Exception:  # noqa: BLE001
            time.sleep(2)
    raise RuntimeError("Jellyfin did not come back on :8096")


def docker_restart_jellyfin() -> None:
    cmd = ["docker", "restart", "jellyfin"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        subprocess.run(["sudo", *cmd], check=True)
    wait_jellyfin()


def ensure_plugin_files() -> None:
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    dll = PLUGIN_DIR / "Jellyfin.Plugin.Lapse.dll"
    if not dll.is_file():
        zip_path = Path("/tmp/lapse-jellyfin-plugin.zip")
        urllib.request.urlretrieve(DLL_URL, zip_path)
        subprocess.run(["unzip", "-o", str(zip_path), "-d", str(PLUGIN_DIR)], check=True)
    meta = {
        "category": "General",
        "changelog": "LAPSE subtitle auto-sync plugin v2.1.4",
        "description": "Sync subtitles to audio using the LAPSE engine.",
        "guid": PLUGIN_GUID,
        "name": "LAPSE",
        "overview": "Auto-sync subtitles that have drifted out of time",
        "owner": "rs-jensen",
        "targetAbi": "12.0.0.0",
        "timestamp": "2026-11-10T08:53:31.0000000Z",
        "version": PLUGIN_VERSION,
        "status": "Active",
        "autoUpdate": False,
        "assemblies": [],
    }
    (PLUGIN_DIR / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"Plugin files in {PLUGIN_DIR}")


def ensure_repo_xml() -> bool:
    text = SYSTEM_XML.read_text(encoding="utf-8")
    if REPO_URL in text:
        return False
    block = (
        "    <RepositoryInfo>\n"
        f"      <Name>{REPO_NAME}</Name>\n"
        f"      <Url>{REPO_URL}</Url>\n"
        "      <Enabled>true</Enabled>\n"
        "    </RepositoryInfo>\n"
    )
    needle = "  </PluginRepositories>"
    if needle not in text:
        raise RuntimeError(f"No PluginRepositories in {SYSTEM_XML}")
    SYSTEM_XML.write_text(text.replace(needle, block + needle, 1), encoding="utf-8")
    print(f"Added plugin repository {REPO_URL}")
    return True


def _guid(value: str) -> str:
    return str(value).replace("-", "").lower()


def plugin_loaded() -> bool:
    try:
        plugins = jf("/Plugins")
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(plugins, list):
        return False
    want = _guid(PLUGIN_GUID)
    return any(_guid(p.get("Id", "")) == want for p in plugins)


def wait_plugin(seconds: int = 60) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if plugin_loaded():
            return True
        time.sleep(2)
    return False


def os_credentials() -> tuple[str, str]:
    text = Path(BAZARR_CONFIG).read_text(encoding="utf-8")
    user = password = ""
    in_block = False
    for line in text.splitlines():
        if line.startswith("opensubtitlescom:"):
            in_block = True
            continue
        if in_block and line and not line.startswith(" ") and not line.startswith("\t"):
            break
        if in_block and ":" in line:
            key, val = line.split(":", 1)
            key, val = key.strip(), val.strip()
            if key == "username":
                user = val
            elif key == "password":
                password = val
    return user, password


def install_engine() -> None:
    engines = jf("/Lapse/Engines")
    if isinstance(engines, list):
        lapse = next((e for e in engines if str(e.get("Id", "")).lower() == "lapse"), None)
        if lapse and lapse.get("Installed"):
            print(f"LAPSE engine already installed ({lapse.get('InstalledVersion') or lapse.get('ReportedVersion')})")
            return
    print("Installing LAPSE engine (download from GitHub)...")
    result = jf("/Lapse/Engines/lapse/Install", method="POST", timeout=300)
    print(f"Engine install: {result}")


def configure_settings() -> None:
    settings = jf("/Lapse/Settings")
    if not isinstance(settings, dict):
        raise RuntimeError(f"Unexpected settings: {settings}")
    settings["outputMode"] = "OverwriteWithBackup"
    settings["lowConfidenceAction"] = "OverwriteAnyway"
    settings["skipAlreadyInSync"] = False
    settings["skipSyncedInUnattendedRuns"] = True
    settings["arrWebhookEnabled"] = True
    user, password = os_credentials()
    if user and password:
        settings["openSubtitlesEnabled"] = True
        settings["openSubtitlesUsername"] = user
        settings["openSubtitlesPassword"] = password
        settings["openSubtitlesLanguage"] = "en"
        key = os.environ.get("OPENSUBTITLES_API_KEY", "").strip()
        if key:
            settings["openSubtitlesApiKey"] = key
    jf("/Lapse/Settings", method="POST", body=settings)
    print("LAPSE settings: overwrite with backup, apply even when unsure")
    try:
        jf(
            "/Lapse/Engines/lapse/Parameter",
            method="POST",
            body={"key": "force", "value": "true"},
        )
        print("LAPSE engine: sync even when unsure")
    except RuntimeError as exc:
        print(f"force parameter skipped: {exc}")


def enable_libraries() -> None:
    libraries = jf("/Lapse/Libraries")
    if not isinstance(libraries, list) or not libraries:
        print("No LAPSE libraries returned")
        return
    payload = {"libraries": []}
    for lib in libraries:
        payload["libraries"].append(
            {
                "itemId": lib.get("itemId") or lib.get("ItemId"),
                "enabled": True,
                "autoSyncEnabled": True,
                "scheduleEnabled": False,
                "scheduleFrequency": lib.get("scheduleFrequency") or "Daily",
                "scheduleDay": lib.get("scheduleDay"),
                "scheduleTime": lib.get("scheduleTime") or "03:00",
            }
        )
        print(f"Enable + auto-sync: {lib.get('Name') or lib.get('name')}")
    jf("/Lapse/Libraries", method="POST", body=payload)


def queue_running() -> bool:
    try:
        snap = jf("/Lapse/Queue")
    except RuntimeError:
        return False
    return bool(isinstance(snap, dict) and snap.get("Running"))


def wait_queue_idle(seconds: int = 1800) -> None:
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            snap = jf("/Lapse/Queue")
        except RuntimeError as exc:
            print(f"queue poll failed: {exc}")
            time.sleep(5)
            continue
        if not isinstance(snap, dict):
            return
        status = (
            snap.get("Running"),
            snap.get("Completed"),
            snap.get("Total"),
            snap.get("CurrentItemName"),
        )
        if status != last:
            print(
                f"LAPSE queue: {snap.get('Completed')}/{snap.get('Total')} "
                f"{snap.get('JobName')} {snap.get('CurrentItemName')}"
            )
            last = status
        if not snap.get("Running"):
            return
        time.sleep(5)
    print("LAPSE queue still running after wait; bulk sync will retry later")


def start_bulk_sync() -> None:
    try:
        result = jf("/Lapse/BulkSync", method="POST", body={"scope": "Library"})
        print(f"Bulk sync started: {result}")
    except RuntimeError as exc:
        msg = str(exc)
        if "409" in msg or "already running" in msg.lower() or "no eligible" in msg.lower():
            print(f"Bulk sync not started: {exc}")
            return
        raise


def sync_named_series(name: str) -> None:
    try:
        q = urllib.parse.quote(name)
        items = jf(f"/Items?searchTerm={q}&recursive=true&includeItemTypes=Series&limit=5")
    except RuntimeError as exc:
        print(f"Series lookup failed: {exc}")
        return
    rows = items.get("Items", []) if isinstance(items, dict) else []
    match = next((r for r in rows if name.lower() in str(r.get("Name", "")).lower()), None)
    if not match:
        print(f"No Jellyfin series named {name!r}")
        return
    series_id = match["Id"]
    try:
        result = jf("/Lapse/Series/Sync", method="POST", body={"itemId": series_id})
        print(f"Queued series sync for {match.get('Name')}: {result}")
    except RuntimeError as exc:
        print(f"Series sync for {name} skipped (bulk may already cover it): {exc}")


def main() -> int:
    ensure_plugin_files()
    xml_changed = ensure_repo_xml()
    wait_jellyfin()
    if xml_changed or not plugin_loaded():
        print("Restarting Jellyfin to load LAPSE...")
        docker_restart_jellyfin()
    if not wait_plugin(60):
        print("LAPSE plugin still not loaded; check jellyfin logs", file=sys.stderr)
        return 1
    print("LAPSE plugin is loaded")
    install_engine()
    configure_settings()
    enable_libraries()
    if os.environ.get("LAPSE_SERIES"):
        sync_named_series(os.environ["LAPSE_SERIES"])
        if os.environ.get("LAPSE_WAIT"):
            wait_queue_idle(int(os.environ.get("LAPSE_WAIT_SEC", "1800")))
    if not queue_running():
        start_bulk_sync()
    queue = jf("/Lapse/Queue")
    print(f"Queue: {json.dumps(queue)[:500]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
