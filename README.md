# Media stack scripts

Operator scripts for Docker *arr + Jellyfin. Live copy: `/mnt/jellyfin/scripts/`.

Runtime app configs are **not** in git (`~/arr/config/`). Logs: `/mnt/jellyfin/logs/`.

## LAN URLs

Host LAN IP is pinned at **192.168.0.175** (Wi‑Fi profile `Sonai 1`). Point TVs, phones, and browsers here — DHCP may also assign `192.168.0.229`, which is the same machine but is **not** what clients are configured to use.

On this PC, `http://127.0.0.1:<port>` works too.

| Service | URL |
|---------|-----|
| Jellyfin | http://192.168.0.175:8096 |
| Seerr (requests) | http://192.168.0.175:5055 |
| qBittorrent | http://192.168.0.175:8080 |
| Radarr (movies) | http://192.168.0.175:7878 |
| Sonarr (TV / anime) | http://192.168.0.175:8989 |
| Prowlarr (indexers) | http://192.168.0.175:9696 |
| Bazarr (subtitles) | http://192.168.0.175:6767 |

## Layout

| Path | Role |
|------|------|
| `docker-compose.yml` | Jellyfin, Seerr, Sonarr, Radarr, Bazarr, Prowlarr, qBittorrent |
| `lib/` | Shared paths and helpers (`stack-paths.sh`, `arr_common.py`) |
| `hooks/` | Sonarr/Radarr CustomScripts (mounted as `/data/scripts`) |
| `maintenance/` | 30‑min download pipeline (dedupe, stall, import, cleanup) |
| `lg-tv/` | LG WebOS remux/transcode queue (`lg-tv-queue-unoptimized.sh`, export/promote helpers) |
| `jellyfin/` | DB delete-guard, episode images, webhook listener |
| `apply/` | One-shot policy (no-seed, LAPSE, VAAPI transcode, hook registration) |
| `temp/` | Host-only extras (gitignored): show-specific policies, blocked titles |
| `host/` | SSD mount guard / recover |
| `systemd/user/` | User units (copy to `~/.config/systemd/user/`) |
| `docs/` | Setup + download behaviour |

**New PC:** plug in the SSD, then `./setup-new-host.sh` (packages, `/mnt/jellyfin` mount, Docker, systemd). Copy `~/arr/config` from the old machine afterwards.

Start: `cd /mnt/jellyfin/scripts && docker compose up -d` or `systemctl --user start media-stack.service`.

Re-wire *arr hooks after a stack reset: `./register-stack-hooks.sh`  
Check wiring: `./stack-verify.sh`

## After a torrent finishes

1. Sonarr/Radarr **hardlink** the file into `/mnt/jellyfin/media` and **remove** it from qBittorrent (no seeding).
2. Import hook copies matching `.srt` → `{video}.eng.srt` (optional LG optimize queue when `LG_TV_OPTIMIZE_ENABLED=1`).
3. Bazarr searches for English subs if none were in the release.
4. **LAPSE** (Jellyfin plugin) autosyncs sidecar timing on new items; bulk sync from Dashboard → LAPSE.
5. **LG TV library pipeline** (optional): set `LG_TV_OPTIMIZE_ENABLED=1` in `scripts/.env`, enable `lg-tv-optimize.service` + `lg-tv-new-media-watch.service`. Policy toggles (`LG_TV_MAX_GIB_PER_HOUR`, `LG_TV_SKIP_REMUX`, …) live in `.env` only — see `.env.example`.
6. **Manual REMUX → TV copy:** `lg-tv/lg-tv-export-processed.sh` (library → `~/Downloads/processed/` without touching masters), then `lg-tv-promote-processed.sh` to swap into `/mnt/jellyfin/media` (backs up to `*.lgopt-backup`). Does **not** downscale to 1080p; keeps source resolution, caps H.264 ~15 Mbps.
7. Every 30 min, `download-maintenance.sh` dedupes queues, recovers 12h stalls, imports orphans, and syncs deletes.

What rewrites the video (skipping/corruption): [`docs/from-download-to-tv.md`](docs/from-download-to-tv.md)  
Download rules: [`docs/jellyfin-download-behaviour.md`](docs/jellyfin-download-behaviour.md)
