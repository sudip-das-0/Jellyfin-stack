# Media stack (git repo on `/mnt/jellyfin/scripts`)

Docker *arr apps + Jellyfin. **Scripts, compose, docs, and systemd templates** live on the jellyfin drive (this repo). **Runtime Docker configs** stay on the internal SSD at `/home/devdas/arr/config/` (not in git). **Logs** are under `/mnt/jellyfin/logs/` (not in git).

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
| `torrent-stall-recovery.timer` | Every 5 min → `download-maintenance.sh` |
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
| `apply-media-language-policy.sh --full` | Library normalize + repair + Bazarr backfill + LG queue for missing subs |
| `audit-subtitle-sidecars.py` / `sync-subtitle-sidecars.py` | Audit timing; alass sync for flagged items |

Host helper (optional): `import-sidecar-from-release.py` + `sidecar_release.py` (same matching rules as the hook).

Optional instant qBit profile on Wi‑Fi change: install `hooks/qbit-network-profile-dispatcher.sh` under NetworkManager (see comments in that file).

## Maintenance chain (`download-maintenance.sh`)

1. `prune-stall-logs.py` — rotate/trim logs (14-day `recovery-*.log`, tail LG optimize log)
2. `queue-dedupe.py` — one torrent per episode/movie
3. `torrent-stall-recovery.py` — 1h stall → 2 alternates → fail + blocklist
4. `reconcile-torrents-library.py` — drop torrent copies already in `media/`; import orphans (staging rename for anime); queue LG + sidecars (`RECONCILE_IMPORT_LIMIT` per run, default 15)
5. `cleanup-qbit-missing.py` — `missingFiles` / dead 0-seed torrents
6. `cleanup-qbit-completed.py` — remove finished torrents (no seeding; 2h post-complete grace via `QBIT_COMPLETED_PROTECT_SEC`)
7. `cleanup-partial-series.py` — Sonarr + Radarr bad queue rows; orphan torrent dirs
7. `series-season-completeness.py` — warns on partial seasons (exit 1 → season search, maintenance still **exit 0**)

Optional one-off: `./cleanup-partial-series.py --torrent-library [--dry-run]` — drop torrent copies already in `media/` (not on the timer).

Download policy (re-apply after stack reset): `./apply-no-seed-download-policy.sh`

Shared Python: `arr_common.py` · Bash paths: `stack-paths.sh`

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

Extended docs: [`docs/`](docs/) · systemd templates: [`systemd/user/`](systemd/user/)
