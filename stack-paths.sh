# Shared paths (keep in sync with arr_common.py). Source from bash maintenance scripts.
LOG_ROOT=/mnt/jellyfin/logs
STALL_LOG_DIR="${LOG_ROOT}/stall-recovery"
LG_TV_DIR="${LOG_ROOT}/lg-tv"
SCRIPTS_ROOT=/mnt/jellyfin/scripts
HOOKS_DIR="${SCRIPTS_ROOT}/hooks"
CONFIG_ROOT=/home/devdas/arr
LG_TV_QUEUE="${LG_TV_DIR}/optimize.queue"
LG_TV_LOCK="${LG_TV_DIR}/optimize.lock"
LG_TV_RETRIES="${LG_TV_DIR}/optimize.retries.tsv"
LG_TV_DEAD="${LG_TV_DIR}/optimize.deadletter.log"
LG_TV_LOG="${STALL_LOG_DIR}/lg-tv-optimize.log"
LG_TV_WATCHER_BASELINE="${LG_TV_DIR}/watcher.baseline"
