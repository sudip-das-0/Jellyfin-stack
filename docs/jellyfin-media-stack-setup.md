# Jellyfin + *arr media stack — setup reference

Last updated: 2026-11-11  
LAN IP: **192.168.0.175** (pinned on Wi‑Fi `Sonai 1`; DHCP may also show `192.168.0.229`)

---

## What was installed

| Component | How it runs |
|-----------|-------------|
| **Docker** + **Docker Compose** | All media services in containers |
| **Jellyfin** | Media server (host network, **Intel QSV**) — **v12.1** (`jellyfin/jellyfin:latest`) |
| **Seerr** (formerly Jellyseerr) | Request movies/TV/anime — **v3.5.0** (`seerr/seerr:v3.5.0`) |
| **Prowlarr** | Indexers → Radarr/Sonarr |
| **Radarr** | Movies |
| **Sonarr** | TV + anime (two root folders) |
| **qBittorrent** | Downloads (Docker only; desktop `qbittorrent` APT package removed) |
| **Bazarr** | Subtitles |
| **TLP** | Lenovo battery conservation mode (~80% cap; not exact 60%) |

Compose file: **`/mnt/jellyfin/scripts/docker-compose.yml`**

Seerr config: **`~/arr/config/jellyseerr/`** (folder name unchanged; app title in UI may still say “Jellyseerr”).

---

## Storage layout

| Location | Purpose |
|----------|---------|
| **`/mnt/jellyfin`** | 2 TB external drive (ext4, label `jellyfin`) — **all media & Jellyfin data** |
| `/mnt/jellyfin/media/movies` | Movies (Radarr) |
| `/mnt/jellyfin/media/series` | TV (Sonarr default) |
| `/mnt/jellyfin/media/anime` | Anime (Sonarr via Seerr `activeAnimeDirectory`) |
| `/mnt/jellyfin/media/other` | Misc (Jellyfin “Other” library) |
| `/mnt/jellyfin/torrents` | qBittorrent downloads + incomplete |
| `/mnt/jellyfin/jellyfin/config` | Jellyfin config & database |
| `/mnt/jellyfin/jellyfin/cache` | Jellyfin transcode cache |
| **`~/arr/config`** | Docker configs only (~tens of MB on **internal SSD**) |
| **`~/jellyfin-drive`** | Symlink → `/mnt/jellyfin` |
| Files bookmark | GNOME Files → **jellyfin** → `/mnt/jellyfin` |

**Downloads do not fill the internal SSD** for video files; large data goes to `/mnt/jellyfin`. Internal disk mainly holds OS + Docker images (~6 GB).

`/etc/fstab` entry (external drive):

```
UUID=a76272dd-9421-425f-9d49-cb2f18715698 /mnt/jellyfin ext4 defaults,nofail,x-systemd.device-timeout=10 0 2
```

Optional (show drive in Files sidebar — requires sudo once):

```bash
sudo sed -i 's|/mnt/jellyfin ext4 defaults,nofail,x-systemd.device-timeout=10|/mnt/jellyfin ext4 defaults,nofail,x-systemd.device-timeout=10,x-gvfs-show,user,exec|' /etc/fstab
sudo mount -o remount /mnt/jellyfin
```

---

## Web URLs (same Wi‑Fi / LAN)

| Service | URL |
|---------|-----|
| Jellyfin | http://192.168.0.175:8096 |
| Seerr (requests) | http://192.168.0.175:5055 |
| qBittorrent | http://192.168.0.175:8080 |
| Radarr | http://192.168.0.175:7878 |
| Sonarr | http://192.168.0.175:8989 |
| Prowlarr | http://192.168.0.175:9696 |
| Bazarr | http://192.168.0.175:6767 |

### Logins (home setup — change if you care about LAN security)

| Service | Notes |
|---------|--------|
| Jellyfin | User **Dimpu** / password **admin** |
| Seerr | **Sign in with Jellyfin** → same **Dimpu** / **admin** (do not use the email box with username “Dimpu”) |
| qBittorrent WebUI | **Dimpu** / **adminadmin** |

### Jellyfin 12 + Seerr sign-in (important)

- **Jellyfin 12.1** requires modern auth headers. Old **Jellyseerr 2.7.x** could not sign in (“Something went wrong”). The stack uses **Seerr 3.5.0**, which is compatible.
- In **`settings.json`**, `main.mediaServerType` must be **`2`** (Jellyfin). Value **`4`** means “not configured” in Seerr v3 and breaks first-time Jellyfin login.
- Seerr reaches Jellyfin from Docker via **`jellyfin.ip`: `host.docker.internal`** (port **8096**). Do not leave `ip` empty after setup.
- Config directory must be writable by the container user (`node`): if Seerr fails to start with `EACCES` on logs, run:  
  `sudo chown -R "$USER:$USER" ~/arr/config/jellyseerr/`

---

## Commands: `jellyfin` helper

Script: **`~/.local/bin/jellyfin`** (on `PATH`) — controls the **entire** stack (Jellyfin, Seerr, Radarr, Sonarr, qBittorrent, Prowlarr, Bazarr).

```bash
jellyfin start          # start all stack containers (compose up -d)
jellyfin stop           # stop all stack containers
jellyfin restart        # restart all
jellyfin status         # docker compose ps
jellyfin logs           # follow Jellyfin logs only (Ctrl+C)
jellyfin logs all       # follow all services

jellyfin media start    # same as jellyfin start (legacy alias)
jellyfin media stop     # same as jellyfin stop
jellyfin media status   # same as jellyfin status
jellyfin help
```

**On boot:** `media-stack.service` (user systemd) runs `docker compose up -d` after Docker is up. Requires user linger (`loginctl enable-linger "$USER"`). Each container also has `restart: unless-stopped` as a backup if Docker starts before the user session.

---

## Docker Compose (manual)

```bash
cd /mnt/jellyfin/scripts

docker compose up -d              # start all services
docker compose stop               # stop all
docker compose ps
docker compose restart jellyfin
docker compose pull jellyseerr    # Seerr image (service name still jellyseerr)
docker compose up -d jellyseerr
docker compose logs -f qbittorrent
```

---

## Boot auto-start

All stack containers start at login/boot via:

- User systemd: **`~/.config/systemd/user/media-stack.service`** → `docker compose up -d` in `/mnt/jellyfin/scripts`
- User linger: **`loginctl enable-linger "$USER"`** (so the user service runs without an active desktop session)
- Docker **`restart: unless-stopped`** on each service (containers come back if Docker restarts)

```bash
systemctl --user status media-stack.service
systemctl --user start media-stack.service    # same as jellyfin start
systemctl --user stop media-stack.service     # stops via compose stop
systemctl --user enable media-stack.service   # start on boot
```

---

## Indexers (Prowlarr)

Enabled: **Knaben**, **LimeTorrents**, **Nyaa.si**, **The Pirate Bay**, **YTS**  
(Synced to Radarr/Sonarr; 1337x/EZTV failed Cloudflare checks.)

Manage: Prowlarr → **Indexers** → add more (free public or account-based indexers).

**Note:** Some shows (e.g. region-locked or very new series) may have **no matching releases** on public indexers. Seerr will still accept the request; Sonarr/Radarr search can return **0 grabs** until a valid release appears or you add better indexers / import manually.

---

## Requests & downloads (how it works)

End-to-end flow:

1. **Seerr** — search → **Request** (movies: usually one confirm; TV: pick **season(s)**).
2. **Auto-approve** — Admin user (**Dimpu**) requests are approved automatically (`MEDIA_AUTO_APPROVED` in logs); no separate approve step.
3. **Radarr** (movies) / **Sonarr** (TV/anime) — search indexers via Prowlarr.
4. **qBittorrent** — download to `/mnt/jellyfin/torrents`, then import to `/mnt/jellyfin/media/...`.
5. **Jellyfin** — scan libraries → play on TV.

### Download client (qBittorrent, no seeding)

| Setting | Value |
|---------|--------|
| Max **download** speed | **50 Mbps** (~6 250 000 B/s global limit in qBittorrent) |
| Max active downloads | **30** |
| Max active torrents | **50** |
| Global upload cap | **100 KiB/s** (~102400 B/s in qBittorrent) |
| Sonarr/Radarr after import | **Remove** torrent from client (`removeCompletedDownloads`); **no seeding** |
| Import | **Hardlinks** enabled (media in `/mnt/jellyfin/media` without duplicating disk) |
| qBittorrent safety net | **max_ratio 0**, 1 min seed time → drop torrent (keep files) |

Sonarr/Radarr pick the best release by quality profile; when scores tie, the release with **more seeders** wins. Indexers require **≥1 seeder** (`minimumSeeders`).

**Behaviour rules (dedupe, stall recovery, speed tips):** [`jellyfin-download-behaviour.md`](jellyfin-download-behaviour.md)

### Dated download logs (on 2 TB drive, outside containers)

| Path | Contents |
|------|----------|
| `/mnt/jellyfin/logs/downloads/download-YYYY-MM-DD.log` | Snapshot every **5 min** (qBittorrent + Sonarr/Radarr queues) |
| `/mnt/jellyfin/logs/sonarr/` | Sonarr service logs |
| `/mnt/jellyfin/logs/radarr/` | Radarr service logs |
| `/mnt/jellyfin/logs/qbittorrent/` | qBittorrent file log |

Manual snapshot: `/mnt/jellyfin/scripts/maintenance/download-activity-log.sh`  
Timer: `systemctl --user status download-activity-log.timer`

### Download maintenance (dedupe + stall recovery)

Every **30 minutes**, `torrent-stall-recovery.timer` → `download-maintenance.sh` runs **in order** (LG TV worker is separate — `lg-tv-optimize.service`):

1. **`queue-dedupe.py`** — at most **one** qBittorrent torrent + **one** Sonarr/Radarr queue row per episode/movie (keeps best progress/speed/seeds). Drops orphan queue rows with no torrent for **≥6 h**.
2. **`torrent-stall-recovery.py`** — only when exactly **one** torrent is active for that item (`STALL_SECONDS=43200`, **12 h**, active download slot only):
   - No **download** progress (`progress` + `downloaded` bytes unchanged) → grab **two** alternates; drop stalled job from qBit **without** deleting files.
   - One alternate healthy → remove other torrent jobs from qBit (files stay).
   - Both alternates stall **12 h** → remove both from qBit (files stay), mark **failed**; run **`retry-failed-downloads.py`** when you want another search pass.

**Sonarr/Radarr tuning (applied):**

| Setting | Value |
|---------|--------|
| Delay profile | **Torrent** only, **3 min** torrent delay (brief wait for better releases) |
| Interactive failed re-grab | **Off** (avoids extra client-side duplicates) |
| Log level | **info** (was debug) |

| Item | Path |
|------|------|
| Wrapper | `/mnt/jellyfin/scripts/download-maintenance.sh` |
| Dedupe | `/mnt/jellyfin/scripts/maintenance/queue-dedupe.py` |
| Stall recovery | `/mnt/jellyfin/scripts/maintenance/torrent-stall-recovery.py` |
| State | `/mnt/jellyfin/logs/stall-recovery/state.json` |
| Log | `/mnt/jellyfin/logs/stall-recovery/recovery-YYYY-MM-DD.log` |
| Timer | `systemctl --user status torrent-stall-recovery.timer` |

Override stall window: `STALL_SECONDS=43200` (12 h) in `torrent-stall-recovery.service` if needed.

### Docker Compose backup

| Item | Path |
|------|------|
| **Live** (used by `jellyfin start`) | `/mnt/jellyfin/scripts/docker-compose.yml` |

### Check if something is downloading

| Where | What to look for |
|--------|------------------|
| Seerr → **Requests** | Status of each request |
| qBittorrent → **http://192.168.0.175:8080** | Active torrents, progress |
| Radarr / Sonarr → **Activity** or **Queue** | Search/grab/import history |
| Laptop | `tail -f /mnt/jellyfin/logs/downloads/download-$(date +%F).log` |
| Laptop | `docker logs jellyseerr --tail 50` |

If Radarr added a movie but qBittorrent stayed empty, open Radarr → movie → **Interactive Search** or **Search** (automatic search sometimes needs a nudge on first add).

### “One-click” request on LG G4

- There is **no Seerr app** in the LG Content Store. Use the **TV web browser** → bookmark **`http://192.168.0.175:5055`** (e.g. name it “Requests”).
- **Sign in with Jellyfin** (not the local email/password field with “Dimpu”).
- **Closest to one-click:** Search → title → **Request**. Admin requests skip manual approval. TV shows still need **season selection** once.
- The **Jellyfin LG app** is watch-only; it does **not** include a request button. One app for both watch + request without Developer Mode = browser Seerr + Jellyfin app, or sideload **Moonfin** (not set up here).

---

## TV workflow (LG G4)

| App / tool | Role |
|------------|------|
| **Jellyfin** (LG store) | Watch library |
| **Web browser** → Seerr :5055 | Request new movies/TV/anime |
| Laptop (optional) | Radarr, Sonarr, qBittorrent, Prowlarr admin |

Daily use on TV: **Requests** bookmark → request → later open **Jellyfin** when the item appears.

---

## Jellyfin plugins installed

Custom Cover Art, HoverTrailer, InPlayerEpisodePreview, Editors Choice, Intro Skipper, Cinema Mode, Continue Watching Dedup, Discontinue Watching, Gelato, **JavaScript Injector**, **File Transformation** (helpers for UI plugins).

Configure: Jellyfin **Dashboard → Plugins**.  
**Intro Skipper** runs on a schedule; needs **multiple episodes** of a show for best skip detection. Manual run: **Dashboard → Scheduled Tasks → Detect and Analyze Media Segments**.

---

## Jellyfin playback (CPU, heat, LG TV)

Configured in **`/mnt/jellyfin/jellyfin/config/config/encoding.xml`** (also visible under **Dashboard → Playback → Transcoding**).

| Setting | Value | Why |
|---------|--------|-----|
| Hardware acceleration | **VAAPI** | First choice for live transcodes; device `/dev/dri/renderD128` (Docker passes `/dev/dri`). QSV remains configured as fallback. |
| Hardware decoding | **h264, hevc, av1, vp9, vc1** | HEVC/VP9/AV1 rips decode on GPU instead of CPU |
| Transcode throttling | **Off** (`EnableThrottling`) | Can turn on in Dashboard if you want client-side buffering during peaks |
| Hardware encoding | On (**H.264 VAAPI**) | When a transcode is unavoidable |
| LG import pipeline | **VAAPI → QSV → libx264** | `lg-tv-optimize-media.sh` for HEVC→H.264; 4K allowed unless `LG_TV_MAX_HEIGHT` is set |
| Live transcode directory | `/mnt/jellyfin/jellyfin/cache/transcodes` | Container path `/cache/transcodes` (temporary segments while watching) |
| LG optimize temp | `/mnt/jellyfin/jellyfin/cache/lgopt` | Container path `/cache/lgopt` (batch remux/transcode before in-place replace) |

### Prefer direct play on the LG G4 (biggest win)

1. While watching, open Jellyfin **Dashboard → Activity** on the laptop.
2. Aim for **Direct Play** or **Direct Stream**, not **Transcode**.
3. On the **LG Jellyfin app**: **Settings → Playback** — avoid forcing a lower max bitrate than needed; disable “Prefer fMP4” only if you have playback issues (try defaults first).
4. **Radarr/Sonarr**: quality profiles that prefer **H.264 + AAC** (or remux) at resolutions the TV handles (often **1080p** for smooth direct play; **4K HEVC** may direct-play on G4 if the file matches TV capabilities — if you see transcodes, pick a 1080p H.264 release in Radarr).

After editing `encoding.xml`, restart Jellyfin: **`jellyfin restart`** (restarts full stack) or `docker compose restart jellyfin` from `~/arr`.

---

## Bazarr / subtitles (free-only online)

**No paid APIs.** Bazarr providers: **NapiProjekt**, **subf2m**, **Animetosho**, **OpenSubtitles.com** (API key in `config.yaml`; also set **username + password** from the same OpenSubtitles account in Bazarr → Settings → Providers). After a Bazarr container recreate, run `/mnt/jellyfin/scripts/apply/bazarr-apply-opensubtitles-apikey.sh` or restart Bazarr (uses `custom-cont-init.d`). On import, `import-sidecar-subs.sh` copies torrent `Subs/`; LG optimize saves embedded subs to `.eng.srt` before remux.

Language profile: **English** (`profileId: 1` in Bazarr `config.yaml` — use the numeric id, not the profile name, or Sonarr sync hits FK errors). Re-register hooks: `/mnt/jellyfin/scripts/register-stack-hooks.sh`. After FK/sync issues: `/mnt/jellyfin/scripts/apply/bazarr-resync-from-arr.sh`

---

## Laptop lid closed (keep server running)

Closing the lid **no longer suspends** the machine (needed for Jellyfin/Docker while the IdeaPad is folded).

- **systemd:** `/etc/systemd/logind.conf.d/50-jellyfin-server.conf` → `HandleLidSwitch=ignore` (and external power / docked).
- **GNOME (if used):** lid close → **Do nothing** on AC and battery.

**Tips when using closed-lid:**

- **Power:** leave the laptop **plugged in** for long downloads; conservation mode (~80%) still applies via TLP.
- **Heat:** ensure **vents aren’t blocked** when the lid is down; a stand or side ventilation helps.
- **External display:** not required for headless streaming to the TV.

To **revert** (suspend on lid close again): remove that drop-in file and run `sudo systemctl restart systemd-logind`, or set GNOME lid actions back to **Suspend**.

---

## Battery (Lenovo conservation mode)

Hardware supports **on/off conservation** (~80% charge cap), **not** a precise 60% threshold.

TLP installed; `/etc/tlp.conf`:

- `STOP_CHARGE_THRESH_BAT0=1`
- `START_CHARGE_THRESH_BAT0=0`

```bash
sudo tlp start
sudo tlp-stat -b
cat /sys/devices/pci0000:00/0000:00:1f.0/PNP0C09:00/VPC2004:00/conservation_mode   # 1 = on
```

---

## Optional / remaining (checklist)

**Worth doing when you have time**

| Item | Why |
|------|-----|
| **OpenSubtitles** account in Bazarr | More reliable subtitles (Settings → Providers). |
| **Stronger passwords** | Jellyfin, qBittorrent WebUI, Seerr — especially if friends use your Wi‑Fi. |
| **More Prowlarr indexers** | Niche shows (e.g. some K-drama) may never grab on public indexers only. |
| **Radarr quality profile** | Prefer **1080p H.264 / WEB-DL / remux** if LG often transcodes 4K or HEVC. |
| **Gelato / Editors Choice** | Optional Jellyfin plugins — configure URLs or featured lists if you want them. |
| **fstab `x-gvfs-show`** | External drive in Files sidebar (one-time sudo; see Storage section). |

**Already in good shape**

- Stack starts on boot (`media-stack.service` + Docker restart policies).
- `jellyfin start` / `stop` = full stack.
- Seerr + Jellyfin 12 sign-in, playback tuning (Intel QSV + HW decode for HEVC/VP9).
- Media on 2 TB drive; downloads not filling internal SSD.
- TLP battery conservation ~80%.

**Avoid**

- Port-forwarding **8096, 5055, 8080, 6881** on the router unless you know how to lock them down (VPN/Tailscale is safer for remote access).

---

## Quick checks

```bash
docker ps
df -h / /mnt/jellyfin
mount | grep jellyfin
hostname -I
jellyfin media status
curl -s -u Dimpu:adminadmin http://127.0.0.1:8080/api/v2/torrents/info | python3 -c "import json,sys; print('torrents:', len(json.load(sys.stdin)))"
```

---

## Paths reference

| Item | Path |
|------|------|
| Compose (live) | `/mnt/jellyfin/scripts/docker-compose.yml` |
| *arr configs | `~/arr/config/` |
| Seerr settings / DB | `~/arr/config/jellyseerr/settings.json`, `.../db/db.sqlite3` |
| Jellyfin config | `/mnt/jellyfin/jellyfin/config/` |
| Jellyfin CLI | `~/.local/bin/jellyfin` |
| This doc | `/mnt/jellyfin/scripts/docs/jellyfin-media-stack-setup.md` |
| Download behaviour rules | `/mnt/jellyfin/scripts/docs/jellyfin-download-behaviour.md` |
