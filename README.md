# Media stack scripts

Operator scripts for Docker *arr + Jellyfin. Live copy: `/mnt/jellyfin/scripts/`.

Runtime app configs are **not** in git (`~/arr/config/`). Logs: `/mnt/jellyfin/logs/`.

## Layout

| Path | Role |
|------|------|
| `docker-compose.yml` | Jellyfin, Seerr, Sonarr, Radarr, Bazarr, Prowlarr, qBittorrent |
| `lib/` | Shared paths and helpers (`stack-paths.sh`, `arr_common.py`) |
| `hooks/` | Sonarr/Radarr CustomScripts (mounted as `/data/scripts`) |
| `maintenance/` | 30‑min download pipeline (dedupe, stall, import, cleanup) |
| `lg-tv/` | LG WebOS remux/transcode queue |
| `jellyfin/` | DB delete-guard, episode images, webhook listener |
| `apply/` | One-shot policy (no-seed, LAPSE, QSV, hook registration) |
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
