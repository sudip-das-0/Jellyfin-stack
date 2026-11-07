# Script conventions (`/mnt/jellyfin/scripts`)

## Single source of truth

| Concern | Python | Bash |
|---------|--------|------|
| Paths, UUID, stall default | `arr_common.py` | `stack-paths.sh` |
| Hooks in containers | — | `hooks/` only (root wrappers may `exec` hooks) |

Source `stack-paths.sh` in every maintenance/bash ops script. Python maintenance scripts `import arr_common`.

## Header block (every entrypoint)

```bash
#!/usr/bin/env bash
# Purpose: …
# Runs: timer | hook | manual
# Depends: stack-paths.sh
```

Use section comments: `# --- Locking ---`, `# --- Sonarr ---`.

## qBittorrent delete policy

| Script | `deleteFiles` |
|--------|----------------|
| `torrent-stall-recovery.py` | false (`qbit_remove_client_only`) |
| `queue-dedupe.py` | true (duplicate swarms) |
| `cleanup-qbit-missing.py` | true |
| `cleanup-qbit-completed.py` | false |

## External SSD

Downloads only when `ensure-jellyfin-media-mount.sh` passes (marker `.jellyfin-external-drive`). Guard: `jellyfin-drive-guard.timer`.

## Registration

`*arr` CustomScript connections: `register_arr_notifications.py` (called from thin `register-arr-*.sh` wrappers).

Verify: `./stack-verify.sh`

## Jellyfin database helpers

| Module | Role |
|--------|------|
| `jellyfin_db_lib.py` | SQLite purge/sanitize (`JELLYFIN_DB` from `arr_common`) |
| `jellyfin-delete-guard.py` | CLI + maintenance steps |
| `jellyfin-delete-webhook.py` | ItemDeleted / PlaybackStart listener |

Paths: `JELLYFIN_DB`, `JELLYFIN_CONFIG_ROOT` in `arr_common.py` / `stack-paths.sh`.
