#!/usr/bin/env bash
# Install udev rule: run drive guard when Samsung jellyfin SSD disconnects (requires sudo once).
set -euo pipefail
RULE=/etc/udev/rules.d/99-jellyfin-ssd-guard.rules
UUID="${JELLYFIN_UUID:-0caa73f3-4f57-4549-8e6d-fbf323866d28}"
DESKTOP_USER="${SUDO_USER:-${USER:-}}"
if [[ -z "$DESKTOP_USER" || "$DESKTOP_USER" == root ]]; then
  echo "cannot determine desktop user (set SUDO_USER)" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo: sudo $0" >&2
  exit 1
fi

cat >"$RULE" <<EOF
# Samsung T5 EVO jellyfin SSD — pause torrents on unplug
ACTION=="remove", SUBSYSTEM=="block", ENV{ID_FS_UUID}=="${UUID}", \\
  RUN+="/usr/bin/systemd-run --collect --user --uid=${DESKTOP_USER} -M ${DESKTOP_USER}@.host --quiet /mnt/jellyfin/scripts/host/jellyfin-drive-guard.sh"
# One Nautilus icon only: fstab mounts /mnt/jellyfin; do not also automount /run/media
SUBSYSTEM=="block", ENV{ID_FS_UUID}=="${UUID}", ENV{UDISKS_AUTOMOUNT_HINT}="never"
EOF

udevadm control --reload-rules
udevadm trigger -c add -s block
echo "Installed $RULE"
