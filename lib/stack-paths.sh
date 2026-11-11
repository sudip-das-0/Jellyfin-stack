# Shared paths (keep in sync with arr_common.py). Source from bash maintenance scripts.
JELLYFIN_MNT="${JELLYFIN_MNT:-/mnt/jellyfin}"
JELLYFIN_UUID="${JELLYFIN_UUID:-0caa73f3-4f57-4549-8e6d-fbf323866d28}"
JELLYFIN_MARKER="${JELLYFIN_MNT}/.jellyfin-external-drive"
MEDIA_ROOT="${JELLYFIN_MNT}/media"
TORRENTS_ROOT="${JELLYFIN_MNT}/torrents"
JELLYFIN_CONFIG_ROOT="${JELLYFIN_MNT}/jellyfin/config"
JELLYFIN_DB="${JELLYFIN_CONFIG_ROOT}/data/jellyfin.db"
JELLYFIN_URL="${JELLYFIN_URL:-http://127.0.0.1:8096}"
LOG_ROOT="${JELLYFIN_MNT}/logs"
STALL_LOG_DIR="${LOG_ROOT}/stall-recovery"
LG_TV_DIR="${LOG_ROOT}/lg-tv"
SCRIPTS_ROOT="${SCRIPTS_ROOT:-/mnt/jellyfin/scripts}"
LIB_DIR="${SCRIPTS_ROOT}/lib"
APPLY_DIR="${SCRIPTS_ROOT}/apply"
LGTV_SCRIPTS="${SCRIPTS_ROOT}/lg-tv"
MAINT_DIR="${SCRIPTS_ROOT}/maintenance"
HOST_DIR="${SCRIPTS_ROOT}/host"
JF_SCRIPTS="${SCRIPTS_ROOT}/jellyfin"
HOOKS_DIR="${SCRIPTS_ROOT}/hooks"
TEMP_DIR="${SCRIPTS_ROOT}/temp"
export PYTHONPATH="${LIB_DIR}${PYTHONPATH:+:$PYTHONPATH}"
CONFIG_ROOT="${CONFIG_ROOT:-${HOME}/arr}"
SONARR_CONFIG="${CONFIG_ROOT}/config/sonarr/config.xml"
RADARR_CONFIG="${CONFIG_ROOT}/config/radarr/config.xml"
BAZARR_CONFIG="${CONFIG_ROOT}/config/bazarr/config/config.yaml"
BAZARR_DB="${CONFIG_ROOT}/config/bazarr/db/bazarr.db"
QBIT_USER="${QBIT_USER:-Dimpu}"
QBIT_PASS="${QBIT_PASS:-adminadmin}"
SONARR_URL="${SONARR_URL:-http://127.0.0.1:8989}"
RADARR_URL="${RADARR_URL:-http://127.0.0.1:7878}"
QBIT_URL="${QBIT_URL:-http://127.0.0.1:8080}"
STALL_SECONDS_DEFAULT="${STALL_SECONDS_DEFAULT:-43200}"

read_arr_api_key() {
  grep -oP '(?<=<ApiKey>)[^<]+' "${1:?}"
}

read_bazarr_api_key() {
  grep -A2 '^auth:' "${BAZARR_CONFIG:?}" | grep apikey | awk '{print $2}'
}
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
# Transcode cap (~15 Mbps) for H.264 output when shrinking heavy files.
export LG_TV_MAXRATE="${LG_TV_MAXRATE:-15M}"
export LG_TV_BUFSIZE="${LG_TV_BUFSIZE:-30M}"
export LG_TV_TARGET_VBITRATE_BPS="${LG_TV_TARGET_VBITRATE_BPS:-15000000}"
# Skip optimize when file is LG-friendly and at/below this storage rate (GiB per hour of runtime).
export LG_TV_MAX_GIB_PER_HOUR="${LG_TV_MAX_GIB_PER_HOUR:-5}"
# H.264 above LG_TV_MAX_GIB_PER_HOUR uses HW transcode (not -c:v copy) when LG_TV_H264_SHRINK=1.
export LG_TV_H264_SHRINK="${LG_TV_H264_SHRINK:-1}"
# Never fall back to libx264 (CPU) unless explicitly enabled (QSV/VAAPI only by default).
export LG_TV_ALLOW_CPU_FALLBACK="${LG_TV_ALLOW_CPU_FALLBACK:-0}"
# Tolerate minor stream corruption during remux (-c:v copy).
export LG_TV_REMUX_INPUT_FLAGS="${LG_TV_REMUX_INPUT_FLAGS:--fflags +genpts+igndts -err_detect ignore_err}"
# Never replace Blu-ray REMUX / large lossless-audio sources (Radarr quality releases).
export LG_TV_SKIP_REMUX="${LG_TV_SKIP_REMUX:-1}"
export LG_TV_SKIP_LOSSLESS_AUDIO="${LG_TV_SKIP_LOSSLESS_AUDIO:-1}"
export LG_TV_SKIP_LOSSLESS_MIN_BYTES="${LG_TV_SKIP_LOSSLESS_MIN_BYTES:-8589934592}"
