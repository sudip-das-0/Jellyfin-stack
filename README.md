# Media stack (`/home/devdas/arr`)

Docker *arr apps + Jellyfin. Config on SSD; media and logs on `/mnt/jellyfin`.

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

Start: `systemctl --user start media-stack.service` (from this directory: `docker compose up -d`).

## Host automation (systemd user)

| Unit | Role |
|------|------|
| `torrent-stall-recovery.timer` | Every 5 min → `download-maintenance.sh` |
| `lg-tv-optimize.service` | Continuous LG remux queue (`lg-tv-optimize-daemon.sh`, **no gap** between jobs) |
| `lg-tv-new-media-watch.service` | Queue new imports for LG optimize |
| `download-activity-log.timer` | Snapshot queues to `/mnt/jellyfin/logs/downloads/` |

Wire hooks after changes: `./scripts/register-stack-hooks.sh`

Verify: `./scripts/stack-verify.sh`

## Maintenance chain (`download-maintenance.sh`)

1. `prune-stall-logs.py` — rotate/trim logs (14-day `recovery-*.log`, tail LG optimize log)
2. `queue-dedupe.py` — one torrent per episode/movie
3. `torrent-stall-recovery.py` — 1h stall → 2 alternates → fail + blocklist
4. `cleanup-qbit-missing.py` — `missingFiles` / dead 0-seed torrents
5. `cleanup-qbit-completed.py` — remove finished torrents (no seeding)
6. `cleanup-partial-series.py` — Sonarr + Radarr bad queue rows; orphan torrent dirs
7. `series-season-completeness.py` — warns on partial seasons (exit 1 → season search, maintenance still **exit 0**)

Optional one-off: `./scripts/cleanup-partial-series.py --torrent-library [--dry-run]` — drop torrent copies already in `media/` (not on the timer).

Download policy (re-apply after stack reset): `./scripts/apply-no-seed-download-policy.sh`

Shared Python: `scripts/arr_common.py`

Verbose maintenance logs (optional): `DEDUPE_VERBOSE=1`, `CLEANUP_VERBOSE=1`, `QBIT_CLEANUP_VERBOSE=1`.

## LG TV direct-play pipeline

Import (Sonarr/Radarr) → `scripts/hooks/arr-import-postprocess.sh` → sidecar subs → `logs/lg-tv/optimize.queue` → `lg-tv-optimize-media.sh` (H.264 + AAC stereo, external subs only; then removes the matching file under `/mnt/jellyfin/torrents`). Set `LG_TV_DELETE_TORRENT_ORIGINAL=0` to keep download copies.

| Script | Use |
|--------|-----|
| `lg-tv-requeue-deadletter.sh` | After encode fixes, re-queue files that hit 3 retries |
| `lg-tv-queue-min-height.sh` | Optional manual batch queue by resolution |
| `apply-tv-series-playback.sh` | Next-episode settings (LG app: sign out/in once) |

Env: `LG_TV_ENCODE_TIMEOUT_SEC` (default 3h), `LG_TV_OPTIMIZE_GAP_SEC=0` (daemon).

## Jellyfin maintenance

| Script | Use |
|--------|-----|
| `jellyfin-db-trim.sh` | Phantom movies, library path fixes (stops Jellyfin briefly) |

(`jellyfin-fix-phantom-movies.sh` is a thin alias of the above.)

## Logs (under `/mnt/jellyfin/logs/`)

| Path | Content |
|------|---------|
| `downloads/download-*.log` | Periodic queue snapshots |
| `lg-tv/optimize.queue` (+ lock, retries, deadletter) | LG worker state |
| `stall-recovery/recovery-*.log` | Dedupe + stall recovery (14-day prune) |
| `stall-recovery/series-completeness.log` | Partial seasons snapshot |
| `stall-recovery/lg-tv-optimize.log` | LG remux (tail-trimmed when large) |
| `stall-recovery/state.json` | Stall recovery + dedupe job state |
| `sonarr/`, `radarr/`, `qbittorrent/` | Docker app logs |

All operator scripts live under **`/home/devdas/arr/scripts/`**; see `scripts/README.md`.

## Policy summary

- **Stalled grabs:** 1h no progress → 2 alternates → fail with blocklist + no auto-redownload of same release; new torrent resets stall state.
- **Incomplete seasons:** Partial (started but not finished) flagged; `SeasonSearch` at most every 12h per season when incomplete.
- **Transcode:** Server Jellyfin encoding has no throttle delay; LG prep queue runs back-to-back via the continuous worker.

Extended docs: [`docs/`](docs/) (setup, download behaviour, script index). Compose backup: [`docs/jellyfin-docker-compose.yml.backup`](docs/jellyfin-docker-compose.yml.backup). systemd templates: [`systemd/user/`](systemd/user/).
