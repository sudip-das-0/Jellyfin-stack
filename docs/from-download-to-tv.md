# From download to TV — what actually changes the file

Jellyfin does **not** rewrite library files when you press play (unless the TV cannot direct-play and it transcodes live). Almost everything else either copies metadata/subs or **replaces the video**. Skipping / “corrupt” playback almost always comes from the LG optimize step.

## Pipeline (in order)

```
Seerr request
    → Sonarr/Radarr grab (Prowlarr)
    → qBittorrent download  (/mnt/jellyfin/torrents)     [file unchanged]
    → *arr import hardlink  (/mnt/jellyfin/media)        [same bytes, new name]
    → import hook: copy .srt next to the video           [sidecar only]
    → Bazarr English sub search                          [sidecar only]
    → LAPSE autosync sidecar timing                      [sidecar only]
    → lg-tv-optimize-media.sh                            [REPLACES the video]
    → Jellyfin scan → LG app play
```

| Step | Script | Touches the video? |
|------|--------|--------------------|
| Download | qBittorrent | No (writes the original) |
| Import | Sonarr/Radarr hardlink | No (same inode) |
| Sidecar subs | `hooks/import-sidecar-subs.sh`, Bazarr, LAPSE plugin | **No** — only `.srt` |
| Stall / dedupe / delete-sync | `download-maintenance.sh` | No (queue/DB only; cleanup may **delete** files, not rewrite them) |
| **LG TV optimize** | `lg-tv/lg-tv-optimize-media.sh` | **Yes — overwrites the file in `media/`** |
| Live transcode | Jellyfin ffmpeg while watching | Temporary only; original on disk stays |

After import, the torrent is **removed from qBittorrent** (no seeding). The library copy is the one that later gets optimized.

## Which processing causes skipping / corruption

**1. H.264 remux (`-c:v copy`) — most likely**

For files that are already H.264 and not huge, optimize does **not** re-encode video. It remuxes with:

- `-fflags +genpts+igndts -err_detect ignore_err` (rebuild timestamps, **ignore stream errors**)
- stereo AAC (`-c:a aac -ac 2`) or, if that fails, **audio copy**
- strips embedded subs / chapters (`-sn`)

The original is then **replaced**. ffmpeg is even allowed to **exit failed** if duration looks OK and the first **8 seconds** decode (`encode tier recovered`). That check does **not** scan the whole file, so a bad middle/end can still land in the library.

Typical TV symptom: stutter, freeze, skip ahead, audio drift, “this file is corrupt”.

Look in `/mnt/jellyfin/logs/stall-recovery/lg-tv-optimize.log` for:

- `encode tier ok (h264-remux` / `h264-remux-audiocopy` / `h264-remux-hw`
- `encode tier recovered` ← ffmpeg failed, file still replaced

**2. HEVC / AV1 / VP9 → H.264 transcode (VAAPI first, QSV fallback)**

Full re-encode (`h264_vaapi`, then `h264_qsv`, ~15 Mbps cap). Can look softer; rare encoder glitches. Less often “skip” than remux, but it **does** replace the original (except names with `REMUX` or large lossless audio, which are skipped).

Log: `encode tier ok (vaapi-hw` / `hevc-qsv` / `av1-vaapi-hw` / `h264 HW shrink`.

**3. `.m2ts` / `.ts` → `.mkv`**

Container rewrite. Timestamp-sensitive; can skip on WebOS if the source TS was messy.

**4. Incomplete download imported**

If *arr imported before the torrent was whole, the file was already broken. Optimize can then remux garbage. Check qBit/Sonarr history for that title.

**5. Live Jellyfin transcode on the TV**

If the LG app cannot direct-play (wrong codec/container), Jellyfin transcodes **while you watch**. That can skip even when the file on disk is fine. Dashboard → Activity: **Direct Play** vs **Transcode**.

Live transcode files go to **`/mnt/jellyfin/jellyfin/cache/transcodes`** (container `/cache/transcodes`). LG optimize writes temps to **`/mnt/jellyfin/jellyfin/cache/lgopt`** (container `/cache/lgopt`), then replaces the library file.

## What does *not* rewrite video

- Subtitle sidecars / LAPSE / ffsubsync
- Episode image refresh
- Jellyfin delete-guard / *arr unmonitor
- qBit speed profiles, stall recovery, missing-episode search

## How to confirm a title was rewritten

```bash
grep -F "Your.File.Name" /mnt/jellyfin/logs/stall-recovery/lg-tv-optimize.log
```

- `done:` → library file was replaced  
- `skipped` / `optimize.skipped` → original left alone  
- no hit → never optimized (still the torrent bytes)

There is **no backup** of the pre-optimize file once the torrent copy is deleted (`LG_TV_DELETE_TORRENT_ORIGINAL=1`). Re-download if you need the original.
