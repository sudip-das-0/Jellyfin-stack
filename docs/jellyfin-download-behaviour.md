# Jellyfin stack — download & torrent behaviour rules

This document describes **how downloads are supposed to behave** on your home server (Seerr → Sonarr/Radarr → qBittorrent → Jellyfin).  
Technical setup reference: [`jellyfin-media-stack-setup.md`](jellyfin-media-stack-setup.md)

---

## End-to-end flow

1. **Seerr** — request a movie, TV season, or anime.
2. **Sonarr / Radarr** — search indexers (via Prowlarr), pick one release, send to qBittorrent.
3. **qBittorrent** — download under `/mnt/jellyfin/torrents` (category `sonarr` or `radarr`).
4. **Sonarr / Radarr** — import into `/mnt/jellyfin/media/...` using **hardlinks** when possible.
5. **Jellyfin** — library scan → watch on TV.
6. **LG TV optimize** (automatic) — queue → H.264 + stereo AAC, replace file in place → direct play on WebOS.

After import, Sonarr/Radarr **remove** the torrent from qBittorrent (**no seeding**). Media stays on disk via **hardlinks** in `/mnt/jellyfin/media`.

**Orphan reconcile (every 30 min):** `reconcile-torrents-library.py` runs before qBit completed cleanup — removes torrent-side **duplicates**, **imports** completed orphans (anime staging renames), queues **LG optimize**. Completed torrents stay in qBit up to **`QBIT_COMPLETED_PROTECT_SEC`** (default 2 h) so import can finish first.

**Missing library (every 24 h):** `queue-missing-downloads.timer` → `MissingEpisodeSearch` + `MissingMoviesSearch` (Wanted → Missing). Log: `stall-recovery/queue-missing-downloads.log`. Manual: `python3 /mnt/jellyfin/scripts/queue-missing-downloads.py` (add `--tv-only` or `--movies-only` to run one app).

---

## qBittorrent limits

Applied by `apply-qbit-network-profile.py` from `config/qbit-network-profiles.json` (SSID → profile).

| Rule | Home Wi‑Fi (`home`) | Hotspot (`oneplus_hotspot`) |
|------|---------------------|-----------------------------|
| Max **download** speed | **~50 Mbps** (`6249472` B/s) | Unlimited (`0`) |
| Max **active downloads** | **50** | **100** |
| Max **active torrents** | **70** | **120** |
| **Seeding** | **Off** | **Off** (`max_ratio=0`; *arr removes after import) |

Stall recovery only counts the **12 h no-progress** window while a torrent is in an **active download slot** (not queued for one of the 50 home-Wi‑Fi slots).

Change in Web UI: **http://192.168.0.175:8080** (or your LAN IP) → Tools → Options → Speed, or edit `config/qbit-network-profiles.json`.

---

## One torrent per episode/movie (dedupe)

**Goal:** Never run multiple swarms for the same Sonarr episode or Radarr movie unless stall recovery explicitly adds alternates.

Every **30 minutes** (maintenance timer), `queue-dedupe.py` runs first:

| Situation | Action |
|-----------|--------|
| Two+ queue rows for the same episode/movie | Keep the **best** torrent (progress → active state → download speed → seeders). Remove other queue rows and delete extra torrents in qBittorrent. |
| Queue row but **no** torrent in qBittorrent for **≥ 6 hours** | Remove stale queue row (no auto-redownload). |

**Manual run:** `/mnt/jellyfin/scripts/queue-dedupe.py`  
**Log prefix:** `[dedupe]` in `/mnt/jellyfin/logs/stall-recovery/recovery-YYYY-MM-DD.log`

---

## Stall recovery (only after dedupe)

Runs immediately after dedupe in `download-maintenance.sh`.

**Precondition:** Exactly **one** qBittorrent torrent / queue row for that episode or movie.

| Step | Rule |
|------|------|
| 1 | If that torrent has **no progress for 12 hours while it holds an active download slot** (downloading / metaDL / forcedDL / stalledDL / checkingDL — i.e. one of qBit’s max-active downloads, not **queued** waiting for a slot), Sonarr/Radarr search for **two** alternate releases (highest **seeders** first). The stalled torrent is removed from qBittorrent. |
| 2 | While two alternates are active: if **one** is healthy (downloading / progressing), **remove all other** torrents for that item and keep the winner. |
| 3 | If **both** alternates have no progress for **12 hours in active slots**, delete both, **blocklist** the grabs, and mark the queue item **failed** (`skipRedownload` — no endless retry loop). |

**Does not run** if more than one torrent is still attached to the same episode/movie (dedupe must run first).

**Timer:** `torrent-stall-recovery.timer` (every 30 min) → `download-maintenance.sh`  
**Stall window:** `STALL_SECONDS=43200` (12 h) on `torrent-stall-recovery.service` (override there if needed).

---

## Sonarr / Radarr grab rules (prevent duplicates)

| Setting | Behaviour |
|---------|-----------|
| Delay profile | **Torrent only**; **3 minute** torrent delay before grab (short window for a better release to appear). |
| After import | **Remove** completed download from client (**`removeCompletedDownloads: true`**). No **`seed`** category. |
| Failed import torrent | Remove failed download from client (Sonarr/Radarr default). |
| Interactive search re-grab on failure | **Off** (`autoRedownloadFailedFromInteractiveSearch: false`) to avoid extra parallel grabs. |
| Automatic failed re-download | **On** for normal failures (handled by *arr, not the stall script’s “both alternates dead” case). |
| Release choice | Quality profile first; tie-break by **more seeders**; indexers require **≥ 1 seeder**. |
| Import | **Hardlinks** from torrent path to media path when possible. |

---

## LG TV direct-play pipeline (automatic)

**Goal:** After import, media is converted for the **LG Jellyfin app** (H.264, stereo AAC, no embedded subs) without manual steps.

| Step | What runs |
|------|-----------|
| Request / grab | **HD-1080p** prefers **English** then **JA/KO/HI/BN** when present; blocks VOSTFR-only; **x264 + AAC** (`apply-english-audio-preference.sh`). |
| Import | Sonarr/Radarr **CustomScript** → `arr-import-postprocess.sh` (episode-matched sidecar subs, then LG queue) → `/mnt/jellyfin/logs/lg-tv/optimize.queue`. |
| New files on disk | `lg-tv-new-media-watch.service` (inotify) queues paths not in baseline. |
| Transcode | **`lg-tv-optimize.service`** runs continuously (daemon): drains queue **back-to-back** (no gap between files), then waits for new queue entries (inotify or ~10 s poll). One encode at a time. Logs **file size before/after**. |
| Retries | Re-queue at **front** on failure. Default **`LG_TV_OPTIMIZE_MAX_RETRIES=4`**: **3** HW-only AV1 attempts (`LG_TV_AV1_HW_ATTEMPTS=3`), then one run may use **`libx264`**; then **dead-letter** if still failing. |
| Replace | `lg-tv-optimize-media.sh` HEVC→H.264 / AV1→H.264: **QSV → VAAPI → libx264 (CPU, AV1 only after HW retries)**; then **replaces** the original. |
| Failures | Failed jobs are **re-queued** (up to 3×). **4K/UHD** allowed (`LG_TV_MAX_HEIGHT=0`). Queue existing 4K: `lg-tv-queue-min-height.sh`. |

**Timers / services (user systemd, `linger` on):**

| Unit | Interval |
|------|----------|
| `torrent-stall-recovery.timer` | 30 min — full `download-maintenance.sh` chain (see README) |
| `queue-missing-downloads.timer` | 24 h — missing episode/movie search |
| `download-activity-log.timer` | 15 min — queue snapshots |
| `qbit-network-profile.timer` | 2 min — SSID → qBit limits |
| `lg-tv-optimize.service` | always on — continuous LG TV queue worker (timer disabled) |
| `lg-tv-new-media-watch.service` | always — detect new files |
| `media-stack.service` | boot — `docker compose up -d` |

**Incomplete seasons:** maintenance runs `series-season-completeness.py`; on partial seasons it triggers `sonarr-search-incomplete-seasons.py` (SeasonSearch, at most once per **12 h** per season — set `SONARR_SEASON_SEARCH_COOLDOWN_HOURS=0` to bypass).

**Logs:** `/mnt/jellyfin/logs/stall-recovery/lg-tv-optimize.log`  
**Queue:** `/mnt/jellyfin/logs/lg-tv/optimize.queue`  
**Dead letter (failed 3×):** `/mnt/jellyfin/logs/lg-tv/optimize.deadletter.log`  
**Retry counts:** `/mnt/jellyfin/logs/lg-tv/optimize.retries.tsv`

**Subtitles (server default):** User **Dimpu** → subtitles **Always on** (English). Order: (1) on import, copy the release `.srt` whose **SxxExx** matches the episode (Vyndros-style names OK), (2) extract embedded subs to `.eng.srt` before LG TV remux, (3) **Bazarr** (OpenSubtitles.com + free providers) on webhooks + 6 h wanted search, plus `repair-sidecars-from-torrents.py` for gaps. Jellyfin plays external `.eng.srt`; the **Open Subtitles** plugin (`apply-jellyfin-opensubtitles.sh`) enables manual “Search subtitles” in the player (same OS.com account; shares the free daily quota with Bazarr).

**LG TV app:** Playback → maximum streaming bitrate **Maximum**; enable **Subtitles** (English) if the client does not pick up the server default.

---

## Logging

| Log | Location |
|-----|----------|
| Download activity snapshot (every 15 min) | `/mnt/jellyfin/logs/downloads/download-YYYY-MM-DD.log` |
| Dedupe + stall recovery | `/mnt/jellyfin/logs/stall-recovery/recovery-YYYY-MM-DD.log` |
| Stall state machine | `/mnt/jellyfin/logs/stall-recovery/state.json` |
| Sonarr / Radarr / qBittorrent service logs | `/mnt/jellyfin/logs/sonarr`, `radarr`, `qbittorrent` |

---

## Getting the most download speed (within your rules)

Your global cap is **50 Mbps** in qBittorrent — that is the ceiling no matter what else you tune.

**Already helping you:**

- Dedupe → one swarm per item → bandwidth not split across duplicates.
- Stall recovery → swaps dead torrents for better-seeded alternates.
- Upload capped at **100 KiB/s** → most capacity left for downloads.
- Up to **30** concurrent downloads → good for many episodes; if speeds feel **too split**, try **15–20** so each active torrent gets a larger share of the 50 Mbps.

**Worth checking on the laptop / network:**

| Change | Why |
|--------|-----|
| **Ethernet** instead of Wi‑Fi for the server | More stable throughput than Wi‑Fi on an IdeaPad. |
| **Router port forward** TCP/UDP **6881** to this machine (only on trusted home LAN) | More incoming peers; often faster than firewalled clients. qBittorrent → Options → Connection: port **6881**, disable random port if you forward. |
| **Keep the queue manageable** | 280+ *different* episodes queued is normal after many Seerr requests; only **30** download at once. Pause unmonitor low-priority series in Sonarr to focus bandwidth on what you watch next. |
| **Quality vs speed** | Huge 4K remuxes need more Mbps per file; 1080p WEB releases often saturate 50 Mbps with fewer peers. |
| **Lid closed / thermals** | Vents clear when folded; thermal throttle reduces CPU/disk speed. |

**In qBittorrent Web UI (optional tweaks):**

- **Tools → Options → BitTorrent** — “Do not count slow torrents in the limit” can free slots for faster jobs (if enabled in your build).
- **Tools → Options → Speed** — confirm **Global download rate limit** shows **50 Mbps** and alternative speed limits are off unless you use them on a schedule.

**Do not disable** the 50 Mbps limit unless you intentionally want to use your full ISP line; you asked for a **50 megabit per second** cap.

---

## Quick commands

```bash
# Full maintenance (dedupe + stall rules)
/mnt/jellyfin/scripts/download-maintenance.sh

# Watch behaviour logs
tail -f /mnt/jellyfin/logs/stall-recovery/recovery-$(date +%F).log

# Current qBittorrent limits
curl -s -u Dimpu:adminadmin http://127.0.0.1:8080/api/v2/app/preferences | \
  python3 -c "import json,sys; p=json.load(sys.stdin); print('down Mbps',p['dl_limit']*8/1e6,'up KiB/s',p['up_limit']/1024,'active dl',p['max_active_downloads'])"
```

---

*Last updated: 2026-11-03*
