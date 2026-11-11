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
| `lg-tv/` | LG WebOS remux/transcode queue |
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
2. Import hook copies matching `.srt` → `{video}.eng.srt` and queues LG optimize.
3. Bazarr searches for English subs if none were in the release.
4. **LAPSE** (Jellyfin plugin) autosyncs sidecar timing on new items; bulk sync from Dashboard → LAPSE.
5. `lg-tv-optimize` remuxes/transcodes to H.264 + stereo AAC for LG direct play (skips REMUX / large lossless).
6. Every 30 min, `download-maintenance.sh` dedupes queues, recovers 12h stalls, imports orphans, and syncs deletes.

What rewrites the video (skipping/corruption): [`docs/from-download-to-tv.md`](docs/from-download-to-tv.md)  
Download rules: [`docs/jellyfin-download-behaviour.md`](docs/jellyfin-download-behaviour.md)
