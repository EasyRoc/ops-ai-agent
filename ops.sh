#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${OPS_ROOT_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"}"
OPS_DIR="${OPS_DIR:-"$ROOT_DIR/.ops"}"
PID_DIR="$OPS_DIR/pids"
LOG_DIR="$OPS_DIR/logs"
CLUSTER_NAME="${OPS_CLUSTER_NAME:-ops-agent}"
KUBE_CONTEXT="kind-$CLUSTER_NAME"

info() {
  printf '[INFO] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*" >&2
}

die() {
  printf '[ERROR] %s\n' "$*" >&2
  return 1
}

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

port_in_use() {
  local port="$1"

  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  elif command -v ss >/dev/null 2>&1; then
    ss -ltn | awk '{print $4}' | grep -Eq "(^|:)$port$"
  elif command -v netstat >/dev/null 2>&1; then
    netstat -an | grep -E "[.:]$port[[:space:]].*LISTEN" >/dev/null 2>&1
  else
    return 1
  fi
}

start_managed_process() {
  local name="$1"
  local port="$2"
  shift 2
  local pid_file="$PID_DIR/$name.pid"
  local log_file="$LOG_DIR/$name.log"
  local pid

  ensure_runtime_dirs
  if is_managed_process_running "$name"; then
    info "$name is already running (pid=$(cat "$pid_file"))"
    return
  fi

  if [[ -n "$port" ]] && port_in_use "$port"; then
    die "Port $port is already in use by an unmanaged process. Inspect it with: lsof -nP -iTCP:$port -sTCP:LISTEN"
    return 1
  fi

  : >"$log_file"
  (
    cd "$ROOT_DIR"
    nohup "$@" >>"$log_file" 2>&1 </dev/null &
    printf '%s\n' "$!" >"$pid_file"
  )

  sleep "${OPS_PROCESS_START_DELAY:-1}"
  if ! is_managed_process_running "$name"; then
    warn "$name exited during startup. Recent log output:"
    tail -n 20 "$log_file" >&2 || true
    return 1
  fi

  pid="$(cat "$pid_file")"
  info "Started $name (pid=$pid${port:+, port=$port})"
}

stop_managed_process() {
  local name="$1"
  local pid_file="$PID_DIR/$name.pid"
  local pid
  local attempt

  ensure_runtime_dirs
  if ! is_managed_process_running "$name"; then
    rm -f "$pid_file"
    return
  fi

  pid="$(cat "$pid_file")"
  kill "$pid" 2>/dev/null || true
  for attempt in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pid_file"
  info "Stopped $name"
}

env_value() {
  local key="$1"
  local default_value="$2"
  local configured_value="${!key:-}"

  if [[ -n "$configured_value" ]]; then
    printf '%s\n' "$configured_value"
    return
  fi

  if [[ -f "$ROOT_DIR/.env" ]]; then
    configured_value="$(awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ROOT_DIR/.env")"
  fi
  printf '%s\n' "${configured_value:-$default_value}"
}

stop_runtime_processes() {
  local name
  for name in agent order-service grafana loki alertmanager prometheus kubectl-proxy; do
    stop_managed_process "$name"
  done
}

start_runtime_processes() {
  local agent_host
  local agent_port

  agent_host="$(env_value AGENT_HOST 0.0.0.0)"
  agent_port="$(env_value AGENT_PORT 8000)"

  start_managed_process kubectl-proxy 8001 \
    kubectl proxy --port=8001 --context "$KUBE_CONTEXT"
  start_managed_process prometheus 9090 \
    kubectl port-forward -n monitoring svc/prometheus-operated 9090:9090 --context "$KUBE_CONTEXT"
  start_managed_process alertmanager 9093 \
    kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-alertmanager 9093:9093 --context "$KUBE_CONTEXT"
  start_managed_process loki 3100 \
    kubectl port-forward -n monitoring svc/loki 3100:3100 --context "$KUBE_CONTEXT"
  start_managed_process grafana 30030 \
    kubectl port-forward -n monitoring svc/prometheus-grafana 30030:80 --context "$KUBE_CONTEXT"
  start_managed_process order-service 8081 \
    kubectl port-forward -n demo svc/order-service 8081:8081 --context "$KUBE_CONTEXT"

  if [[ ! -x "$ROOT_DIR/.venv/bin/uvicorn" ]]; then
    die "Missing $ROOT_DIR/.venv/bin/uvicorn. Run: ./ops.sh bootstrap"
    return 1
  fi
  start_managed_process agent "$agent_port" \
    "$ROOT_DIR/.venv/bin/uvicorn" agent.main:app --host "$agent_host" --port "$agent_port"
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
