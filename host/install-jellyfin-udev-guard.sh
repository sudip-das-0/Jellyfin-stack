#!/usr/bin/env bash
# Install udev rule: run drive guard when Samsung jellyfin SSD disconnects (requires sudo once).
set -euo pipefail
RULE=/etc/udev/rules.d/99-jellyfin-ssd-guard.rules
UNIT_SRC=/mnt/jellyfin/scripts/systemd/user/99-jellyfin-ssd-removed.service
UUID="${JELLYFIN_UUID:-0caa73f3-4f57-4549-8e6d-fbf323866d28}"
DESKTOP_USER="${SUDO_USER:-${USER:-devdas}}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo: sudo $0" >&2
  exit 1
fi

cat >"$RULE" <<EOF
# Samsung T5 EVO jellyfin SSD — pause torrents on unplug
ACTION=="remove", SUBSYSTEM=="block", ENV{ID_FS_UUID}=="${UUID}", \\
  RUN+="/usr/bin/systemd-run --collect --user --uid=${DESKTOP_USER} -M ${DESKTOP_USER}@.host --quiet /mnt/jellyfin/scripts/host/jellyfin-drive-guard.sh"
EOF

udevadm control --reload-rules
udevadm trigger -c add -s block
echo "Installed $RULE"
