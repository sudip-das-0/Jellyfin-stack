#!/usr/bin/env bash
# NetworkManager dispatcher: re-apply qBittorrent profile when Wi‑Fi comes up.
# Install (once, root):
#   sudo cp /mnt/jellyfin/scripts/hooks/qbit-network-profile-dispatcher.sh \
#     /etc/NetworkManager/dispatcher.d/99-qbit-network-profile
#   sudo chmod 755 /etc/NetworkManager/dispatcher.d/99-qbit-network-profile

set -euo pipefail

IFACE="${1:-}"
STATUS="${2:-}"

if [[ "$STATUS" != "up" ]]; then
  exit 0
fi

if ! nmcli -t -f DEVICE,TYPE dev status 2>/dev/null | grep -qx "${IFACE}:wifi"; then
  exit 0
fi

exec /usr/bin/python3 /mnt/jellyfin/scripts/apply/apply-qbit-network-profile.py
