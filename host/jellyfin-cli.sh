#!/usr/bin/env bash
# Control the Jellyfin + *arr Docker stack (compose in /mnt/jellyfin/scripts).
set -euo pipefail
COMPOSE_DIR=/mnt/jellyfin/scripts
cmd="${1:-help}"
shift || true

docker_cmd() {
  if docker info &>/dev/null; then
    docker compose "$@"
  else
    sudo docker compose "$@"
  fi
}

run_compose() {
  if ! mountpoint -q /mnt/jellyfin; then
    echo "jellyfin: /mnt/jellyfin is not mounted — plug in the jellyfin SSD" >&2
    exit 1
  fi
  (cd "$COMPOSE_DIR" && docker_cmd "$@")
}

case "$cmd" in
  start|up) run_compose up -d ;;
  stop) run_compose stop ;;
  restart) run_compose restart "$@" ;;
  status) run_compose ps ;;
  logs)
    if [[ "${1:-}" == "all" ]]; then
      run_compose logs -f
    else
      run_compose logs -f "${1:-jellyfin}"
    fi
    ;;
  pull) run_compose pull "$@" ;;
  help|-h|--help)
    cat <<'EOF'
Usage: jellyfin <command>
  start              docker compose up -d
  stop               docker compose stop
  restart [service]  compose restart
  status             compose ps
  logs [service]     follow logs (default jellyfin; use 'all')
  pull [service]     compose pull
EOF
    ;;
  media)
    case "${1:-}" in
      start) run_compose up -d ;;
      stop) run_compose stop ;;
      status) run_compose ps ;;
      *) echo "Usage: jellyfin media {start|stop|status}" >&2; exit 1 ;;
    esac
    ;;
  *)
    echo "Unknown command: $cmd (try: jellyfin help)" >&2
    exit 1
    ;;
esac
