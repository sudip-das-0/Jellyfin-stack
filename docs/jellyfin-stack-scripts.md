# Media stack scripts (reference)

Live scripts: `/mnt/jellyfin/scripts/`; import hooks in `scripts/hooks/` (mounted in Sonarr/Radarr as `/data/scripts`). Logs: `/mnt/jellyfin/logs/` (not in git).

**Path constants:** Python `arr_common.py` and Bash `stack-paths.sh` must stay aligned (`JELLYFIN_MNT`, `TORRENTS_ROOT`, `CONFIG_ROOT`, `STALL_SECONDS_DEFAULT=43200`, qBit creds via `QBIT_USER`/`QBIT_PASS`). Verify: `./stack-verify.sh`.

**External SSD only:** `ensure-jellyfin-media-mount.sh`, `jellyfin-drive-guard.timer`, marker `/mnt/jellyfin/.jellyfin-external-drive`. Recovery: `recover-jellyfin-drive.sh`. Purge NVMe stubs: `purge-internal-torrent-data.sh`.

## Scheduled

| Unit | Script |
|------|--------|
| `torrent-stall-recovery.timer` (30 min) | `download-maintenance.sh` (dedupe → stall recovery → reconcile → qBit cleanup → season completeness) |
| `queue-missing-downloads.timer` (24 h) | `queue-missing-downloads.py` |
| `torrent-library-dedupe.timer` (daily) | torrent duplicate cleanup |
| `lg-tv-optimize.service` (always on) | `lg-tv-optimize-daemon.sh` → `lg-tv-process-queue.sh` (one encode at a time; AV1: 3× HW then CPU fallback; max **4** failures → dead-letter) |
| `lg-tv-new-media-watch.service` | `lg-tv-watch-new-media.sh` |
| `download-activity-log.timer` (15 min) | `download-activity-log.sh` |
| `qbit-network-profile.timer` (2 min) | `apply-qbit-network-profile.py` (Wi‑Fi SSID → qBit limits) |
| `media-stack.service` (boot) | `ensure-jellyfin-media-mount.sh` then `docker compose up -d` |
| `jellyfin-drive-guard.timer` (2 min) | `jellyfin-drive-guard.sh` — stop qBit/Sonarr/Radarr if SSD missing |

## One-shot / maintenance

| Script | Purpose |
|--------|---------|
| `register-stack-hooks.sh` | **Run after stack reset** — import hook + Bazarr webhooks + OpenSubtitles API key |
| `register_arr_notifications.py` | *arr CustomScript connections (import / delete-sync / unmonitor) |
| `apply-bazarr-subtitle-defaults.sh` | English profile + backfill missing subs |
| `bazarr-resync-from-arr.sh` | Fix Bazarr FK/sync errors — purge cache DB + full *arr resync |
| `apply-jellyfin-opensubtitles.sh` | Install/configure Jellyfin Open Subtitles plugin (same OS.com account as Bazarr) |
| `apply-tv-direct-play-formats.py` | Sonarr/Radarr x264/AAC quality prefs |
| `media-lang-policy.py` | **Global langs:** subtitles `eng` only; audio `eng/jpn/kor/hin/ben` |
| `apply-english-audio-preference.sh` | Applies `media-lang-policy.py` to Bazarr + Jellyfin |
| `apply-media-language-policy.sh` | **`--full`**: globals + existing library (Bazarr search, queue missing `.eng.srt`). **`--settings-only`**: globals only (used on stack reset) |
| `jellyfin-db-trim.sh` | Jellyfin DB cleanup (legacy `/media`, phantom movies, orphans); alias: `jellyfin-fix-phantom-movies.sh` |
| `jellyfin_db_lib.py` | Shared Jellyfin SQLite helpers |
| `jellyfin-delete-guard.py` | UserData sanitize + ghost BaseItems (`download-maintenance.sh`) |
| `jellyfin-delete-webhook.py` | Webhook listener (`jellyfin-delete-webhook.service`) |

## Import pipeline (per file)

1. **Bazarr webhook** — online subs (NapiProjekt, subf2m, Animetosho, OpenSubtitles.com)
2. **`arr-import-postprocess.sh`** — episode-matched release `.srt` → `.eng.srt`, then LG TV queue
3. **`lg-tv-optimize-media.sh`** — extract embedded subs, remux for LG direct play; video **QSV → VAAPI → libx264** (AV1: libx264 only after **`LG_TV_AV1_HW_ATTEMPTS`** queue failures)

Removed / do not use: `arr-queue-dedupe.py` (duplicate of `queue-dedupe.py`).
