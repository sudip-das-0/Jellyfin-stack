# Media stack scripts (reference)

Live scripts: `/mnt/jellyfin/scripts/`; import hooks in `scripts/hooks/` (mounted in Sonarr/Radarr as `/data/scripts`). Logs: `/mnt/jellyfin/logs/` (not in git).

## Scheduled

| Unit | Script |
|------|--------|
| `torrent-stall-recovery.timer` (5 min) | `download-maintenance.sh` |
| `lg-tv-optimize.timer` (30 s idle kick) | `lg-tv-process-queue.sh` — drain queue back-to-back (no gap), max **3** retries |
| `lg-tv-new-media-watch.service` | `lg-tv-watch-new-media.sh` |
| `download-activity-log.timer` | `download-activity-log.sh` |

## One-shot / maintenance

| Script | Purpose |
|--------|---------|
| `register-stack-hooks.sh` | **Run after stack reset** — import hook + Bazarr webhooks + OpenSubtitles API key |
| `apply-bazarr-subtitle-defaults.sh` | English profile + backfill missing subs |
| `apply-tv-direct-play-formats.py` | Sonarr/Radarr x264/AAC quality prefs |
| `media-lang-policy.py` | **Global langs:** subtitles `eng` only; audio `eng/jpn/kor/hin/ben` |
| `apply-english-audio-preference.sh` | Applies `media-lang-policy.py` to Bazarr + Jellyfin |
| `apply-media-language-policy.sh` | **`--full`**: globals + existing library (Bazarr search, queue missing `.eng.srt`). **`--settings-only`**: globals only (used on stack reset) |
| `jellyfin-db-trim.sh` | Jellyfin DB cleanup (legacy `/media`, phantom movies, orphans); alias: `jellyfin-fix-phantom-movies.sh` |

## Import pipeline (per file)

1. **Bazarr webhook** — online subs (NapiProjekt, subf2m, Animetosho, OpenSubtitles.com)
2. **`arr-import-postprocess.sh`** — torrent `Subs/` → `.eng.srt`, then LG TV queue
3. **`lg-tv-optimize-media.sh`** — extract embedded subs, remux for LG direct play; HEVC→H.264 tries **QSV → VAAPI → libx264**

Removed / do not use: `arr-queue-dedupe.py` (duplicate of `queue-dedupe.py`).
