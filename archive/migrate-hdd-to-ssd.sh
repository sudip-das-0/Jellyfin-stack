#!/usr/bin/env bash
# Migrate live stack from HDD to SSD (jellyfin → jellyfin ssd labels).
# - Copies from HDD; never deletes or moves source files on the HDD.
# - finish: incremental sync to SSD staging, swap mounts (SSD → /mnt/jellyfin), start stack.
# - format-stage: destructive fresh SSD (only for empty / broken staging).
# - After cutover, background rsync copies any remaining /media from /mnt/jellyfin-hdd.
set -euo pipefail

HDD_UUID="a76272dd-9421-425f-9d49-cb2f18715698"
SSD_UUID="0caa73f3-4f57-4549-8e6d-fbf323866d28"
HDD_DEV="/dev/sda1"
SSD_DEV="/dev/sdb1"
OWNER="devdas:devdas"
LOG="/tmp/jellyfin-ssd-migrate-$(date +%Y%m%d-%H%M%S).log"
RSYNC_BG_LOG="/mnt/jellyfin/logs/ssd-migrate-media-rsync.log"

exec > >(tee -a "$LOG") 2>&1
echo "=== Jellyfin HDD → SSD migration started $(date -Is) ==="
echo "Log: $LOG"
echo "HDD: $HDD_DEV ($HDD_UUID)  SSD: $SSD_DEV ($SSD_UUID)"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo $0"
  exit 1
fi

phase="${1:-finish}"

stop_stack() {
  echo "--- Stopping user services and containers ---"
  sudo -u devdas systemctl --user stop jellyfin-delete-webhook.service 2>/dev/null || true
  sudo -u devdas systemctl --user stop lg-tv-optimize.service lg-tv-new-media-watch.service 2>/dev/null || true
  sudo -u devdas systemctl --user stop download-activity-log.timer torrent-stall-recovery.timer \
    torrent-library-dedupe.timer qbit-network-profile.timer queue-missing-downloads.timer 2>/dev/null || true
  if [[ -d /mnt/jellyfin/scripts ]]; then
    sudo -u devdas docker compose -f /mnt/jellyfin/scripts/docker-compose.yml stop 2>/dev/null || true
  fi
  sleep 3
}

rsync_staging_trees() {
  local src=/mnt/jellyfin
  local dst=/mnt/jellyfin-ssd
  if [[ ! -d "$dst" ]]; then
    echo "SSD staging mount missing: $dst"
    exit 1
  fi

  echo "--- rsync: scripts, jellyfin config, logs, torrents, media ($src → $dst) ---"
  rsync -aHAX --info=stats2 "$src/scripts/" "$dst/scripts/"
  rsync -aHAX --info=stats2 "$src/jellyfin/config/" "$dst/jellyfin/config/"
  mkdir -p "$dst/jellyfin/cache"
  chown -R "$OWNER" "$dst/jellyfin"
  rsync -aHAX --info=stats2 "$src/logs/" "$dst/logs/"
  rsync -aHAX --info=stats2 "$src/torrents/" "$dst/torrents/"
  mkdir -p "$dst/media"
  rsync -aHAX --info=stats2 "$src/media/" "$dst/media/"
  chown -R "$OWNER" "$dst/jellyfin" "$dst/logs" "$dst/media" "$dst/scripts" "$dst/torrents" 2>/dev/null || true
}

sync_to_ssd_staging() {
  echo "--- Incremental sync to SSD staging (/mnt/jellyfin-ssd) ---"
  rsync_staging_trees
}

format_ssd_and_stage() {
  echo "--- Format SSD and mount at /mnt/jellyfin-ssd (staging) ---"
  umount /mnt/jellyfin-ssd 2>/dev/null || true
  mkfs.ext4 -F -L "jellyfin ssd" "$SSD_DEV"
  mkdir -p /mnt/jellyfin-ssd
  mount "$SSD_DEV" /mnt/jellyfin-ssd
  chown "$OWNER" /mnt/jellyfin-ssd
  rsync_staging_trees
}

swap_mounts() {
  echo "--- Swap mounts: SSD → /mnt/jellyfin, HDD → /mnt/jellyfin-hdd (ro) ---"
  umount /mnt/jellyfin-ssd || umount -l /mnt/jellyfin-ssd
  umount /mnt/jellyfin || umount -l /mnt/jellyfin

  mkdir -p /mnt/jellyfin /mnt/jellyfin-hdd /mnt/jellyfin-ssd

  FSTAB=/etc/fstab
  cp -a "$FSTAB" "${FSTAB}.bak.$(date +%Y%m%d-%H%M%S)"
  grep -v "$HDD_UUID" "$FSTAB" | grep -v "$SSD_UUID" > "${FSTAB}.new" || true
  {
    echo "UUID=${SSD_UUID} /mnt/jellyfin ext4 defaults,nofail,x-systemd.device-timeout=120 0 2"
    echo "UUID=${HDD_UUID} /mnt/jellyfin-hdd ext4 defaults,ro,nofail,x-systemd.device-timeout=120 0 2"
  } >> "${FSTAB}.new"
  mv "${FSTAB}.new" "$FSTAB"
  systemctl daemon-reload

  mount /mnt/jellyfin
  # HDD may still be RW in-kernel after cutover; mount rw then remount ro for fstab intent.
  if ! mount /mnt/jellyfin-hdd 2>/dev/null; then
    mount -o rw "$HDD_DEV" /mnt/jellyfin-hdd
    mount -o remount,ro /mnt/jellyfin-hdd || true
  fi
  chown -R "$OWNER" /mnt/jellyfin/jellyfin /mnt/jellyfin/logs /mnt/jellyfin/media \
    /mnt/jellyfin/scripts /mnt/jellyfin/torrents 2>/dev/null || true
}

start_stack() {
  echo "--- Starting Docker stack on SSD (/mnt/jellyfin) ---"
  sudo -u devdas docker compose -f /mnt/jellyfin/scripts/docker-compose.yml up -d
  sudo -u devdas systemctl --user start jellyfin-delete-webhook.service 2>/dev/null || true
  sudo -u devdas systemctl --user start lg-tv-optimize.service lg-tv-new-media-watch.service 2>/dev/null || true
  sudo -u devdas systemctl --user start download-activity-log.timer torrent-stall-recovery.timer \
    torrent-library-dedupe.timer qbit-network-profile.timer queue-missing-downloads.timer 2>/dev/null || true
}

start_media_rsync_bg() {
  if [[ ! -d /mnt/jellyfin-hdd/media ]]; then
    echo "--- No /mnt/jellyfin-hdd; skipping background media rsync ---"
    return 0
  fi
  echo "--- Background copy: /mnt/jellyfin-hdd/media → /mnt/jellyfin/media ---"
  mkdir -p /mnt/jellyfin/logs
  chown "$OWNER" /mnt/jellyfin/logs
  if [[ -f /mnt/jellyfin/logs/ssd-migrate-media-rsync.pid ]]; then
    old_pid="$(cat /mnt/jellyfin/logs/ssd-migrate-media-rsync.pid)"
    if kill -0 "$old_pid" 2>/dev/null; then
      echo "Media rsync already running (PID $old_pid); log: $RSYNC_BG_LOG"
      return 0
    fi
  fi
  nohup rsync -aHAX --info=progress2 \
    /mnt/jellyfin-hdd/media/ /mnt/jellyfin/media/ \
    >> "$RSYNC_BG_LOG" 2>&1 &
  echo $! > /mnt/jellyfin/logs/ssd-migrate-media-rsync.pid
  echo "Media rsync PID $(cat /mnt/jellyfin/logs/ssd-migrate-media-rsync.pid); log: $RSYNC_BG_LOG"
}

case "$phase" in
  stop) stop_stack ;;
  sync-staging) sync_to_ssd_staging ;;
  format-stage) stop_stack; format_ssd_and_stage ;;
  swap) swap_mounts ;;
  start) start_stack; start_media_rsync_bg ;;
  finish)
    stop_stack
    sync_to_ssd_staging
    swap_mounts
    start_stack
    start_media_rsync_bg
    ;;
  all)
    stop_stack
    format_ssd_and_stage
    swap_mounts
    start_stack
    start_media_rsync_bg
    ;;
  media-only) start_media_rsync_bg ;;
  *)
    echo "Usage: $0 [finish|stop|sync-staging|format-stage|swap|start|media-only|all]"
    echo "  finish (default): stop stack, sync HDD→SSD staging, swap mounts, start stack"
    exit 1
    ;;
esac

echo "=== Done $(date -Is) ==="
df -hT /mnt/jellyfin /mnt/jellyfin-hdd 2>/dev/null || df -hT /mnt/jellyfin /mnt/jellyfin-ssd 2>/dev/null || true
