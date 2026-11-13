#!/usr/bin/env bash
# Prepare this Linux user + Docker host to run the Jellyfin SSD stack.
# Plug in the jellyfin SSD first. Copy ~/arr/config from the old PC after this
# if you want indexers/hooks (those live on NVMe, not the SSD).
#
#   ./setup-new-host.sh           # packages, mount, dirs, systemd, compose
#   ./setup-new-host.sh --verify  # stack-verify only
set -euo pipefail

ROOT=$(dirname "$(readlink -f "$0")")
# shellcheck source=lib/stack-paths.sh
source "$ROOT/lib/stack-paths.sh"

USER_NAME="${SUDO_USER:-$USER}"
USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
ARR_CONFIG="${ARR_CONFIG_ROOT:-$USER_HOME/arr/config}"
PACKAGES=(
  git curl rsync sqlite3 ffmpeg inotify-tools python3 python3-venv python3-pip
  docker.io docker-compose-v2 vainfo intel-media-va-driver-non-free
)

need_sudo() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

install_packages() {
  echo "=== packages ==="
  need_sudo apt-get update -qq
  need_sudo apt-get install -y "${PACKAGES[@]}"
  need_sudo usermod -aG docker,video,render "$USER_NAME"
  echo "User $USER_NAME in docker, video, render (log out/in if docker still needs sudo)."
}

mount_ssd() {
  echo "=== mount $JELLYFIN_MNT (UUID=$JELLYFIN_UUID) ==="
  need_sudo mkdir -p "$JELLYFIN_MNT"
  if ! grep -q "$JELLYFIN_UUID" /etc/fstab; then
    echo "UUID=$JELLYFIN_UUID $JELLYFIN_MNT ext4 defaults,nofail,x-systemd.device-timeout=10,x-gvfs-show 0 2" \
      | need_sudo tee -a /etc/fstab >/dev/null
  fi
  if grep "$JELLYFIN_UUID" /etc/fstab | grep -q 'user,exec'; then
    need_sudo sed -i "/$JELLYFIN_UUID/s/,user,exec//g" /etc/fstab
  fi
  # Drop GNOME /run/media duplicate even when /mnt/jellyfin is already up.
  findmnt -S "UUID=$JELLYFIN_UUID" -no TARGET 2>/dev/null | while read -r t; do
    [[ "$t" == "$JELLYFIN_MNT" ]] && continue
    need_sudo umount "$t" || true
  done
  if mountpoint -q "$JELLYFIN_MNT"; then
    echo "already mounted"
  else
    need_sudo mount "$JELLYFIN_MNT"
  fi
  "$HOST_DIR/ensure-jellyfin-media-mount.sh"
}

host_layout() {
  echo "=== dirs, .env, CLI ==="
  mkdir -p "$ARR_CONFIG"/{prowlarr,qbittorrent,radarr,sonarr,bazarr,jellyseerr} \
    "$USER_HOME/arr/jellyfin-drive-guard" \
    "$USER_HOME/.config/systemd/user" \
    "$USER_HOME/.local/bin"
  ln -sfn "$SCRIPTS_ROOT" "$USER_HOME/arr/scripts"
  ln -sfn "$JELLYFIN_MNT" "$USER_HOME/jellyfin-drive"
  if [[ ! -f "$SCRIPTS_ROOT/.env" ]]; then
    if [[ -f "$SCRIPTS_ROOT/.env.example" ]]; then
      sed "s|REPLACE_ME|${USER_NAME}|g" "$SCRIPTS_ROOT/.env.example" >"$SCRIPTS_ROOT/.env"
    else
      printf 'ARR_CONFIG_ROOT=%s\nLG_TV_OPTIMIZE_ENABLED=0\n' "$ARR_CONFIG" >"$SCRIPTS_ROOT/.env"
    fi
  fi
  install -m 755 "$HOST_DIR/jellyfin-cli.sh" "$USER_HOME/.local/bin/jellyfin"
  grep -q '.local/bin' "$USER_HOME/.bashrc" 2>/dev/null \
    || echo 'export PATH="$HOME/.local/bin:$PATH"' >>"$USER_HOME/.bashrc"
}

install_units() {
  echo "=== systemd user units + linger ==="
  cp "$SCRIPTS_ROOT"/systemd/user/*.service "$SCRIPTS_ROOT"/systemd/user/*.timer \
    "$USER_HOME/.config/systemd/user/"
  need_sudo loginctl enable-linger "$USER_NAME"
  systemctl --user daemon-reload
  systemctl --user enable media-stack.service
  systemctl --user enable --now \
    torrent-stall-recovery.timer \
    download-activity-log.timer \
    queue-missing-downloads.timer \
    torrent-library-dedupe.timer \
    qbit-network-profile.timer \
    jellyfin-drive-guard.timer
  # shellcheck source=lib/stack-paths.sh
  source "$SCRIPTS_ROOT/lib/stack-paths.sh"
  if lg_tv_optimize_enabled; then
    systemctl --user enable --now lg-tv-optimize.service lg-tv-new-media-watch.service
  else
    systemctl --user disable --now lg-tv-optimize.service lg-tv-new-media-watch.service 2>/dev/null || true
    echo "LG TV optimize disabled (LG_TV_OPTIMIZE_ENABLED=0)"
  fi
  if [[ -f "$HOST_DIR/install-jellyfin-udev-guard.sh" ]]; then
    need_sudo "$HOST_DIR/install-jellyfin-udev-guard.sh" || true
  fi
}

start_stack() {
  echo "=== docker compose ==="
  local compose=(docker compose --env-file "$SCRIPTS_ROOT/.env" -f "$SCRIPTS_ROOT/docker-compose.yml")
  if docker info &>/dev/null; then
    (cd "$SCRIPTS_ROOT" && "${compose[@]}" pull && "${compose[@]}" up -d)
  else
    need_sudo docker compose --env-file "$SCRIPTS_ROOT/.env" -f "$SCRIPTS_ROOT/docker-compose.yml" pull
    need_sudo docker compose --env-file "$SCRIPTS_ROOT/.env" -f "$SCRIPTS_ROOT/docker-compose.yml" up -d
  fi
}

print_next() {
  local ip
  ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  cat <<EOF

Done. Jellyfin: http://${ip:-127.0.0.1}:8096

*arr configs are on this PC at:
  $ARR_CONFIG
They are NOT on the SSD. From the old machine:

  rsync -av ~/arr/config/ ${USER_NAME}@${ip:-NEW_HOST}:$ARR_CONFIG/

Then on this PC:

  jellyfin restart
  $SCRIPTS_ROOT/register-stack-hooks.sh

Log out and back in once so 'docker' works without sudo.
Pipeline / skipping videos: $SCRIPTS_ROOT/docs/from-download-to-tv.md
EOF
}

case "${1:-}" in
  --verify)
    "$ROOT/stack-verify.sh"
    ;;
  *)
    install_packages
    mount_ssd
    host_layout
    install_units
    start_stack
    print_next
    ;;
esac
