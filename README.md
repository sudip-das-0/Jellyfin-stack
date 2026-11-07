# Media stack (git repo on `/mnt/jellyfin/scripts`)

Docker *arr apps + Jellyfin. **Scripts, compose, docs, and systemd templates** live on the 2TB library SSD at `/mnt/jellyfin/scripts/` (this repo). **Runtime Docker configs** stay on the internal NVMe at `/home/devdas/arr/config/` (not in git). **Logs** are under `/mnt/jellyfin/logs/` (not in git). Legacy HDD copy: `/mnt/jellyfin-hdd` (read-only archive after `migrate-hdd-to-ssd.sh finish`).

## Layout

| Path | Role |
|------|------|
| `/mnt/jellyfin/scripts/` | This repo — all operator scripts + `hooks/` for Sonarr/Radarr |
| `/home/devdas/arr/config/` | Prowlarr, qBit, *arr, Seerr configs |
| `/mnt/jellyfin/media/` | Library |
| `/mnt/jellyfin/torrents/` | Downloads |
| `/mnt/jellyfin/logs/` | App + maintenance logs |

## Services

| Service | Port | Notes |
|---------|------|--------|
| Jellyfin | 8096 | `/mnt/jellyfin/media`, QSV transcode, no throttle gap |
| Jellyseerr | 5055 | Requests (Jellyfin 12–compatible) |
| Sonarr | 8989 | TV |
| Radarr | 7878 | Movies |
| Bazarr | 6767 | English subs → sidecar `.eng.srt` |
| Prowlarr | 9696 | Indexers |
| qBittorrent | 8080 | `/mnt/jellyfin/torrents`, `/data` in containers |

Start: `systemctl --user start media-stack.service` or `cd /mnt/jellyfin/scripts && docker compose up -d`.

## Host automation (systemd user)

| Unit | Role |
|------|------|
| `torrent-stall-recovery.timer` | Every 30 min → `download-maintenance.sh` (12h stall window) |
| `lg-tv-optimize.service` | Continuous LG remux queue (`lg-tv-optimize-daemon.sh`, **no gap** between jobs) |
| `lg-tv-new-media-watch.service` | Queue new imports for LG optimize |
| `download-activity-log.timer` | Snapshot queues to `/mnt/jellyfin/logs/downloads/` |
| `queue-missing-downloads.timer` | Every **24 h** → Sonarr/Radarr missing search (`queue-missing-downloads.py`) |
| `torrent-library-dedupe.timer` | Daily → torrent duplicate cleanup in `torrents/` |
| `qbit-network-profile.timer` | Every 2 min → Wi‑Fi SSID → qBittorrent limits (`apply-qbit-network-profile.py`) |
| `media-stack.service` | Boot → `docker compose up -d` |

Wire hooks after changes: `./register-stack-hooks.sh`

Verify: `./stack-verify.sh` (checks `hooks/lg-tv-queue-path.sh`, `hooks/import-sidecar-subs.sh`, timers above)

Re-queue media that missed LG optimize: `./lg-tv-queue-unoptimized.sh` (after script moves, ensure `~/arr/scripts` → `/mnt/jellyfin/scripts`).

## Subtitles

| Script | When |
|--------|------|
| `hooks/import-sidecar-subs.sh` | **On import** — episode-matched release `.srt` → `{video}.eng.srt` (canonical copy in `hooks/`) |
| `repair-sidecars-from-torrents.py` | Fix missing/wrong `.eng.srt` from files still under `/mnt/jellyfin/torrents` |
| `jellyfin-refresh-episode-images.py` | TMDB/screen-grab episode Primary when missing (stops duplicate series poster in episode grids) |
| `apply-media-language-policy.sh --full` | Library normalize + repair + Bazarr backfill + LG queue for missing subs |
| `audit-subtitle-sidecars.py` / `sync-subtitle-sidecars.py` | Audit timing; alass sync for flagged items |

Host helper (optional): `import-sidecar-from-release.py` + `sidecar_release.py` (same matching rules as the hook).

Optional instant qBit profile on Wi‑Fi change: install `hooks/qbit-network-profile-dispatcher.sh` under NetworkManager (see comments in that file).

## Jellyfin UI delete (no re-downloads)

Deleting in **Jellyfin** only sticks if the file is removed from disk and Sonarr/Radarr stop monitoring the title.

1. When deleting in Jellyfin, enable **Delete from server** (removes the file under `/mnt/jellyfin/media`).
2. Run once: `./apply-jellyfin-ui-delete-sync.sh` — Webhook plugin (ItemDeleted) → local listener → `sync-manual-deletes.py`; Sonarr/Radarr **file-delete** custom scripts as backup.
3. **Every 30 min**, `download-maintenance.sh` also runs `sync-manual-deletes.py` for anything missed.

Permanent removal from the *arr apps (with blocklist): still use Sonarr/Radarr **Delete series/movie** with delete files + exclusion.

## Unmonitor completed imports

After Sonarr/Radarr finish importing, episodes/movies are **unmonitored** so they are not re-grabbed. Apply once: `./apply-unmonitor-completed-policy.sh` (Sonarr **Auto unmonitor previously downloaded episodes**, Radarr **movies** equivalent, plus **On Import** hooks). Optional backfill for titles already on disk (TV + movies): `UNMONITOR_BACKFILL=1 ./apply-unmonitor-completed-policy.sh`.

## Maintenance chain (`download-maintenance.sh`)

1. `prune-stall-logs.py` — rotate/trim logs (14-day `recovery-*.log`, tail LG optimize log)
2. `jellyfin-delete-guard.py --sanitize-all` — strip corrupt Jellyfin UserData keys (safe online)
3. `jellyfin-delete-guard.py --purge-ghosts` — remove phantom BaseItems after UI deletes
4. `sync-manual-deletes.py` — unmonitor titles removed from disk (Jellyfin UI / manual deletes)
5. `unmonitor-on-import.py --drain-queue` — unmonitor episodes/movies after import (backstop for hooks)
6. `queue-dedupe.py` — one torrent per episode/movie (updates shared `stall-recovery/state.json` on trim)
7. `torrent-stall-recovery.py` — 12h no **download** progress **in active qBit slot** → 2 alternates; qBit job removed **without** deleting files → fail + blocklist if both alternates stall
8. `sonarr-prune-command-queue.py` — cap queued `SeasonSearch` commands so imports can run
9. `reconcile-torrents-library.py` — drop torrent copies already in `media/`; import orphans; LG + sidecars (`RECONCILE_IMPORT_LIMIT`, default 15)
10. `cleanup-qbit-missing.py` — `missingFiles` / dead 0-seed torrents
11. `cleanup-qbit-completed.py` — remove finished torrents (2h post-complete grace via `QBIT_COMPLETED_PROTECT_SEC`)
12. `cleanup-partial-series.py` — Sonarr + Radarr bad queue rows; orphan torrent dirs
13. `lg-tv-topup-queue.sh` — enqueue library files missing from LG optimize queue
14. Jellyfin episode image refresh helpers
15. `series-season-completeness.py` — exit 1 on partial monitored seasons → `sonarr-queue-missing-if-due.py` (MissingEpisodeSearch, 6h cooldown; manual per-season: `sonarr-search-incomplete-seasons.py`, max `SONARR_MAX_SEASON_SEARCH_PER_RUN` per run)

Cleanup checklist: [`docs/CLEANUP-PLAN.md`](docs/CLEANUP-PLAN.md)

Manual missing search: `queue-missing-downloads.py` (`--tv-only` / `--movies-only`). Failed grabs: `retry-failed-downloads.py` (same flags; clears blocklist unless `--keep-blocklist`).

Shared Python: `arr_common.py` (`fetch_arr_queue`, `STALL_RECOVERY_STATE_PATH`, `QBIT_ACTIVE_DOWNLOAD_STATES`, `JELLYFIN_*`, `STALL_SECONDS_DEFAULT=43200`, `qbit_remove_client_only`) · Bash paths: `stack-paths.sh` (keep in sync)

Legacy torrent dirs on NVMe: `./purge-internal-torrent-data.sh` (removes `~/data/torrents` stubs; optional NVMe `/mnt/jellyfin` skeleton when SSD unmounted).

Optional one-off: `./cleanup-partial-series.py --torrent-library [--dry-run]` — drop torrent copies already in `media/` (not on the timer).

Download policy (re-apply after stack reset): `./apply-no-seed-download-policy.sh`

**USB SSD unplug / I/O errors in qBit:** If many torrents show **Error** or **Missing files**, the jellyfin drive may be unmounted while containers still hold a stale bind mount. Run `./recover-jellyfin-drive.sh` (mount by UUID + restart stack + refresh *arr downloads).

**Downloads only on the external SSD:** qBit/Sonarr/Radarr use `/data` → `/mnt/jellyfin` (UUID `0caa73f3-…`). `ensure-jellyfin-media-mount.sh` + `jellyfin-drive-guard.timer` stop downloaders if that mount is missing (nothing should land on NVMe). Marker: `/mnt/jellyfin/.jellyfin-external-drive`.

## LG TV direct-play pipeline

Import (Sonarr/Radarr) → `hooks/arr-import-postprocess.sh` → episode-matched sidecar subs → `logs/lg-tv/optimize.queue` → `lg-tv-optimize-media.sh` (H.264 + AAC stereo, external subs only; then removes the matching file under `/mnt/jellyfin/torrents`). Set `LG_TV_DELETE_TORRENT_ORIGINAL=0` to keep download copies. Batch transcodes prefer Intel QSV/VAAPI decode+encode (`encode tier ok (av1-qsv|hevc-qsv|h264-remux-hw|…)` in `lg-tv-optimize.log`). Re-apply stack policy: `./apply-qsv-transcode-policy.sh` (Jellyfin `encoding.xml` + `lg-tv-optimize.service` env). Failed encodes re-queue at **front** (`LG_TV_OPTIMIZE_MAX_RETRIES`, default 4: 3× HW-only AV1 attempts then 1× with `libx264` allowed). Override: `LG_TV_HW_DECODE=0`, `LG_TV_AV1_QSV=0`, `LG_TV_AV1_HW_ATTEMPTS`. Verify: `./stack-verify.sh`.

## Logs (under `/mnt/jellyfin/logs/`)

| Path | Content |
|------|---------|
| `downloads/download-*.log` | Periodic queue snapshots |
| `lg-tv/optimize.queue` (+ lock, retries, deadletter) | LG worker state |
| `stall-recovery/recovery-*.log` | Dedupe + stall recovery (14-day prune) |
| `stall-recovery/lg-tv-optimize.log` | LG remux (tail-trimmed when large) |
| `sonarr/`, `radarr/`, `qbittorrent/` | Docker app logs |

Extended docs: [`docs/`](docs/) · conventions: [`docs/SCRIPT-CONVENTIONS.md`](docs/SCRIPT-CONVENTIONS.md) · systemd templates: [`systemd/user/`](systemd/user/)
