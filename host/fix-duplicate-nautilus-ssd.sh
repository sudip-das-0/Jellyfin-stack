#!/usr/bin/env bash
# Keep a single Nautilus shortcut for the jellyfin SSD (fstab → /mnt/jellyfin).
# GNOME/udisks also automounts USB disks at /run/media/$USER/jellyfin\ ssd.
# That second mount is the duplicate icon. Requires sudo once.
set -euo pipefail
UUID="${JELLYFIN_UUID:-0caa73f3-4f57-4549-8e6d-fbf323866d28}"
KEEP="${JELLYFIN_MNT:-/mnt/jellyfin}"
RULE=/etc/udev/rules.d/99-jellyfin-ssd-no-automount.rules

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo: sudo $0" >&2
  exit 1
fi

findmnt -S "UUID=$UUID" -no TARGET 2>/dev/null | while read -r t; do
  [[ "$t" == "$KEEP" ]] && continue
  umount "$t" 2>/dev/null || umount -l "$t" || true
done

if grep -q "$UUID" /etc/fstab; then
  sed -i "/$UUID/s/,user,exec//g" /etc/fstab
fi

cat >"$RULE" <<EOF
# Do not automount this SSD under /run/media — fstab already mounts $KEEP
SUBSYSTEM=="block", ENV{ID_FS_UUID}=="${UUID}", ENV{UDISKS_AUTOMOUNT_HINT}="never"
EOF
udevadm control --reload-rules
udevadm trigger -c add -s block || true

echo "Nautilus: one shortcut → $KEEP"
findmnt -S "UUID=$UUID" -no TARGET || true
