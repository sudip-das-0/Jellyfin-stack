# Media stack scripts (reference)

Live scripts: `/mnt/jellyfin/scripts/`; import hooks in `scripts/hooks/` (mounted in Sonarr/Radarr as `/data/scripts`). Logs: `/mnt/jellyfin/logs/` (not in git).

## Scheduled

| Unit | Script |
|------|--------|
| `torrent-stall-recovery.timer` (5 min) | `download-maintenance.sh` |
| `lg-tv-optimize.service` (always on) | `lg-tv-optimize-daemon.sh` → `lg-tv-process-queue.sh` (back-to-back, max **3** retries) |
| `lg-tv-new-media-watch.service` | `lg-tv-watch-new-media.sh` |
| `download-activity-log.timer` (5 min) | `download-activity-log.sh` |
| `qbit-network-profile.timer` (2 min) | `apply-qbit-network-profile.py` (Wi‑Fi SSID → qBit limits) |
| `media-stack.service` (boot) | `docker compose up -d` |

## One-shot / maintenance

| Script | Purpose |
|--------|---------|
| `register-stack-hooks.sh` | **Run after stack reset** — import hook + Bazarr webhooks + OpenSubtitles API key |
| `apply-bazarr-subtitle-defaults.sh` | English profile + backfill missing subs |
| `bazarr-resync-from-arr.sh` | Fix Bazarr FK/sync errors — purge cache DB + full *arr resync |
| `apply-jellyfin-opensubtitles.sh` | Install/configure Jellyfin Open Subtitles plugin (same OS.com account as Bazarr) |
| `apply-tv-direct-play-formats.py` | Sonarr/Radarr x264/AAC quality prefs |
| `media-lang-policy.py` | **Global langs:** subtitles `eng` only; audio `eng/jpn/kor/hin/ben` |
| `apply-english-audio-preference.sh` | Applies `media-lang-policy.py` to Bazarr + Jellyfin |
| `apply-media-language-policy.sh` | **`--full`**: globals + existing library (Bazarr search, queue missing `.eng.srt`). **`--settings-only`**: globals only (used on stack reset) |
| `jellyfin-db-trim.sh` | Jellyfin DB cleanup (legacy `/media`, phantom movies, orphans); alias: `jellyfin-fix-phantom-movies.sh` |

## Import pipeline (per file)

1. **Bazarr webhook** — online subs (NapiProjekt, subf2m, Animetosho, OpenSubtitles.com)
2. **`arr-import-postprocess.sh`** — episode-matched release `.srt` → `.eng.srt`, then LG TV queue
3. **`lg-tv-optimize-media.sh`** — extract embedded subs, remux for LG direct play; HEVC→H.264 tries **QSV → VAAPI → libx264**

Removed / do not use: `arr-queue-dedupe.py` (duplicate of `queue-dedupe.py`).
