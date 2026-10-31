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
LG_TV_SKIPPED="${LG_TV_DIR}/optimize.skipped"

# Library videos for LG optimize (movies/series/anime); .m2ts/.ts remux to .mkv in optimize script.
lg_tv_find_videos() {
  find "${1:?}" -type f \( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.m2ts' -o -iname '*.ts' \)
}

# Intel QSV / VAAPI defaults for batch transcode (Jellyfin container ffmpeg).
export LG_TV_QSV_DEVICE="${LG_TV_QSV_DEVICE:-/dev/dri/renderD128}"
export LG_TV_VAAPI_DRIVER="${LG_TV_VAAPI_DRIVER:-iHD}"
export LG_TV_HW_DECODE="${LG_TV_HW_DECODE:-1}"
export LG_TV_AV1_QSV="${LG_TV_AV1_QSV:-1}"
# AV1: try HW tiers LG_TV_AV1_HW_ATTEMPTS times (re-queue on fail), then allow libx264 on next run.
export LG_TV_AV1_HW_ATTEMPTS="${LG_TV_AV1_HW_ATTEMPTS:-3}"
