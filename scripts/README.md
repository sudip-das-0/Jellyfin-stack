# Scripts layout

| Location | Purpose |
|----------|---------|
| `/home/devdas/arr/scripts/` | Host maintenance, LG worker, policy tools (SSD) |
| `/home/devdas/arr/scripts/hooks/` | Sonarr/Radarr import hooks only (mounted as `/data/scripts` in containers) |
| `/mnt/jellyfin/scripts` | Symlink → `hooks/` (same files) |

Paths for bash: `stack-paths.sh` (mirrors `arr_common.py`).

## Logs (`/mnt/jellyfin/logs/`)

| Path | Purpose |
|------|---------|
| `downloads/` | qBit + *arr queue snapshots (timer) |
| `lg-tv/` | LG worker queue, lock, retries, deadletter, watcher baseline |
| `stall-recovery/` | Maintenance + LG encode log, state, recovery logs |
| `sonarr/`, `radarr/`, `qbittorrent/` | App logs from Docker |

Operator docs: [`../README.md`](../README.md).
