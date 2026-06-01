#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${OPS_ROOT_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"}"
OPS_DIR="${OPS_DIR:-"$ROOT_DIR/.ops"}"
PID_DIR="$OPS_DIR/pids"
LOG_DIR="$OPS_DIR/logs"

usage() {
  cat <<'EOF'
Ops AI Agent local environment manager

Usage:
  ./ops.sh bootstrap      Prepare and start the full local environment
  ./ops.sh start          Start an existing local environment
  ./ops.sh restart        Restart managed background processes
  ./ops.sh stop           Stop managed processes and Docker Compose services
  ./ops.sh status         Show local component health
  ./ops.sh logs [name]    Tail a managed process log (default: agent)
  ./ops.sh demo start     Build, load, and deploy demo services
  ./ops.sh demo stop      Delete demo services
  ./ops.sh demo restart   Rebuild and redeploy demo services
  ./ops.sh test           Run the real CPU fault injection E2E test
  ./ops.sh clean          Stop services and delete the Kind cluster
  ./ops.sh clean --all    Also delete Docker Compose volumes
  ./ops.sh help
EOF
}

ensure_runtime_dirs() {
  mkdir -p "$PID_DIR" "$LOG_DIR"
}

init_env_file() {
  if [[ -f "$ROOT_DIR/.env" ]]; then
    return
  fi
  if [[ ! -f "$ROOT_DIR/.env.example" ]]; then
    printf 'Missing environment template: %s\n' "$ROOT_DIR/.env.example" >&2
    return 1
  fi
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  printf 'Created %s from .env.example\n' "$ROOT_DIR/.env"
}

is_managed_process_running() {
  local name="$1"
  local pid_file="$PID_DIR/$name.pid"
  local pid

  ensure_runtime_dirs
  [[ -f "$pid_file" ]] || return 1

  pid="$(cat "$pid_file")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    return 0
  fi

  rm -f "$pid_file"
  return 1
}

main() {
  local command="${1:-help}"

  case "$command" in
    help|-h|--help)
      usage
      ;;
    *)
      printf 'Unknown command: %s\n\n' "$command" >&2
      usage >&2
      return 1
      ;;
  esac
}

if [[ "${OPS_SH_SOURCE_ONLY:-0}" != "1" ]]; then
  main "$@"
fi
