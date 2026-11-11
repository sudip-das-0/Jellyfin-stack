# Stack scripts cleanup plan (living checklist)

**Conventions:** [`SCRIPT-CONVENTIONS.md`](SCRIPT-CONVENTIONS.md) · **Catalog:** [`jellyfin-stack-scripts.md`](jellyfin-stack-scripts.md)

## Phase status

| Phase | Goal | Status |
|-------|------|--------|
| 0 | Inventory + conventions doc | Done |
| 1 | `stack-paths.sh` + `arr_common.py` shared paths | Done (`JELLYFIN_DB`, `JELLYFIN_CONFIG_ROOT`, `JELLYFIN_URL`) |
| 2 | `register_arr_notifications.py`, LG `queue-lib`, qBit safety in no-seed | Done |
| 3 | Maintenance section headers, stall 12h, external SSD guards | Done |
| 4 | **Your edits:** `jellyfin_db_lib`, delete guard in maintenance + recover | Done |
| 5 | `apply-*.sh` + `bazarr-*` use `stack-paths.sh` | Done (qsv policy uses `JELLYFIN_CONFIG_ROOT`) |
| 6 | Bazarr webhooks → Python | Done (`register_bazarr_webhooks.py`) |
| 7 | Split `lg-tv-optimize-media.sh` / `sync-manual-deletes.py` | Deferred (comments only if touched) |

## Second pass (after Jellyfin delete / DB edits)

**Aligned**

- `stack-paths.sh` / `arr_common.py` — Jellyfin DB + config root + API URL.
- `jellyfin_db_lib.py`, `jellyfin-delete-guard.py`, `jellyfin-delete-webhook.py` — shared paths, `STALL_LOG_DIR` for guard logs.
- `download-maintenance.sh` — `jellyfin-delete-guard` sanitize + ghost purge after `prune-stall-logs`.
- `recover-jellyfin-drive.sh` — optional Jellyfin skip; post-recovery delete-guard when Jellyfin restarts.
- `jellyfin-db-trim.sh` — sources `stack-paths`; Python blocks use `$DB` / `$SCRIPTS_ROOT`.
- `apply-*` (bazarr, jellyfin opensubtitles/ui-delete/english-audio), `bazarr-*` — source `stack-paths.sh`.
- Docs: README maintenance chain, SCRIPT-CONVENTIONS, jellyfin-stack-scripts catalog.

**Optional follow-ups**

- Remaining intentional literals: Jellyfin library `options.xml` paths under `$JELLYFIN_MNT`, subtitle helpers (`normalize-subtitle-sidecars.py`), `stack-verify` symlink checks, large `lg-tv-optimize-media.sh`.
- `lg-tv-queue-min-height.sh` → uses `hooks/lg-tv-queue-lib.sh` (done).
- Python paths: core maintenance + reconcile use `arr_common` `MEDIA_ROOT` / `TORRENTS_ROOT` (done); sidecar scripts may still hardcode mounts.
- `jellyfin_db_lib`: shared `collect_missing_baseitem_ids` for online/offline ghost purge (done).

## Do not merge

- `queue-dedupe` ↔ `torrent-stall-recovery` (shared `state.json`, different phases)
- `queue-missing-downloads` ↔ `retry-failed-downloads` ↔ `sonarr-queue-missing-if-due`
- `cleanup-qbit-*` (different `deleteFiles` rules)

## Maintenance chain (authoritative)

`download-maintenance.sh`:

1. Log hygiene + library sync: prune stall logs → **delete-guard sanitize + ghosts** → sync-manual-deletes → unmonitor-on-import
2. qBit queue health: dedupe → stall recovery → sonarr command prune
3. Import orphans + qBit hygiene
4. LG TV + Jellyfin metadata

## Verify after each pass

```bash
cd /mnt/jellyfin/scripts
./stack-verify.sh
python3 -m py_compile arr_common.py register_arr_notifications.py jellyfin-delete-guard.py jellyfin_db_lib.py jellyfin-delete-webhook.py
```

After commit: sync user units if changed — `cp systemd/user/*.timer ~/.config/systemd/user/` && `systemctl --user daemon-reload`.
