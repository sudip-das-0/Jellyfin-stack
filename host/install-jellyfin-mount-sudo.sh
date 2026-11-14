#!/usr/bin/env bash
# One-time: passwordless mount/remount for jellyfin SSD guard (run with sudo).
set -euo pipefail
TARGET_USER="${SUDO_USER:-${USER:-}}"
if [[ -z "$TARGET_USER" || "$TARGET_USER" == root ]]; then
  echo "Run: sudo $0" >&2
  exit 1
fi
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo: sudo $0" >&2
  exit 1
fi

DEST=/etc/sudoers.d/sonai-jellyfin-mount
cat >"$DEST" <<EOF
# Jellyfin T5 SSD — jellyfin-drive-lib.sh mount/remount (install-jellyfin-mount-sudo.sh)
${TARGET_USER} ALL=(ALL) NOPASSWD: /usr/bin/mount, /usr/bin/umount, /usr/bin/systemctl start mnt-jellyfin.mount, /usr/bin/systemctl restart mnt-jellyfin.mount
EOF
chmod 440 "$DEST"
visudo -c -f "$DEST"
echo "Installed $DEST for user ${TARGET_USER}"