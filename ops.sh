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

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

detect_os() {
  case "$(uname -s)" in
    Darwin)
      printf 'macos\n'
      ;;
    Linux)
      printf 'linux\n'
      ;;
    *)
      printf 'unsupported\n'
      ;;
  esac
}

dependency_hint() {
  local os_name="$1"
  local command_name="$2"

  if [[ "$os_name" == "macos" ]]; then
    printf 'Install missing tools with Homebrew, for example: brew install %s\n' "$command_name"
  else
    printf 'Install missing tools with your package manager, for example: apt-get install %s or brew install %s\n' \
      "$command_name" "$command_name"
  fi
}

check_dependencies() {
  local os_name
  local command_name
  local missing=0
  local required=(docker kubectl kind helm mvn python3 curl)

  os_name="$(detect_os)"
  if [[ "$os_name" == "unsupported" ]]; then
    die "Unsupported operating system: $(uname -s). Supported systems: macOS and Linux."
    return 1
  fi

  for command_name in "${required[@]}"; do
    if ! command_exists "$command_name"; then
      printf '[ERROR] Missing required command: %s\n' "$command_name" >&2
      dependency_hint "$os_name" "$command_name" >&2
      missing=1
    fi
  done
  if [[ "$missing" -ne 0 ]]; then
    return 1
  fi

  if ! docker compose version >/dev/null 2>&1; then
    die "Docker Compose v2 is required. Verify with: docker compose version"
    return 1
  fi
}

check_docker_daemon() {
  if ! docker info >/dev/null 2>&1; then
    die "Docker daemon is not reachable. Start Docker Desktop or your Docker service, then retry."
    return 1
  fi
}

compose() {
  (
    cd "$ROOT_DIR"
    docker compose "$@"
  )
}

wait_for_condition() {
  local description="$1"
  local timeout_seconds="$2"
  shift 2
  local elapsed=0

  while ((elapsed < timeout_seconds)); do
    if "$@" >/dev/null 2>&1; then
      return
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done

  die "Timed out waiting for $description after ${timeout_seconds}s"
}

data_services_healthy() {
  compose exec -T postgres pg_isready -U "$(env_value POSTGRES_USER opsagent)" -d "$(env_value POSTGRES_DB ops_agent)" &&
    compose exec -T redis redis-cli ping | grep -q PONG
}

start_data_services() {
  info "Starting PostgreSQL and Redis"
  compose up -d
}

wait_for_data_services() {
  info "Waiting for PostgreSQL and Redis"
  wait_for_condition "PostgreSQL and Redis" "${OPS_DATA_TIMEOUT:-90}" data_services_healthy
}

ensure_python_env() {
  if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
    info "Creating Python virtual environment"
    python3 -m venv "$ROOT_DIR/.venv"
  fi
  info "Installing Python dependencies"
  "$ROOT_DIR/.venv/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"
}

kind_cluster_exists() {
  kubectl config get-contexts -o name | grep -Fxq "$KUBE_CONTEXT"
}

ensure_kind_cluster() {
  if kind_cluster_exists; then
    info "Kind cluster $CLUSTER_NAME already exists"
    return
  fi
  info "Creating Kind cluster $CLUSTER_NAME"
  kind create cluster --name "$CLUSTER_NAME" --config "$ROOT_DIR/kind-config.yaml"
}

require_kind_cluster() {
  if ! kind_cluster_exists; then
    die "Kind cluster $CLUSTER_NAME does not exist. Run: ./ops.sh bootstrap"
    return 1
  fi
}

deploy_monitoring() {
  info "Configuring Helm repositories"
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
  helm repo add grafana https://grafana.github.io/helm-charts --force-update
  helm repo update

  info "Deploying Prometheus, Alertmanager, and Grafana"
  helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
    --namespace monitoring --create-namespace \
    -f "$ROOT_DIR/k8s/monitoring/prometheus-values.yaml" \
    -f "$ROOT_DIR/k8s/monitoring/grafana-values.yaml" \
    --wait --timeout "${OPS_HELM_TIMEOUT:-10m}"

  info "Deploying Loki"
  kubectl apply -f "$ROOT_DIR/k8s/monitoring/loki.yaml" --context "$KUBE_CONTEXT"

  info "Deploying Promtail"
  helm upgrade --install promtail grafana/promtail \
    --namespace monitoring --create-namespace \
    -f "$ROOT_DIR/k8s/monitoring/promtail-values.yaml" \
    --wait --timeout "${OPS_HELM_TIMEOUT:-10m}"
}

wait_for_monitoring() {
  info "Waiting for Loki rollout"
  kubectl rollout status deployment/loki -n monitoring --context "$KUBE_CONTEXT" \
    --timeout="${OPS_KUBECTL_TIMEOUT:-180s}"
  info "Waiting for Promtail rollout"
  kubectl rollout status daemonset/promtail -n monitoring --context "$KUBE_CONTEXT" \
    --timeout="${OPS_KUBECTL_TIMEOUT:-180s}"
}

monitoring_stack_exists() {
  kubectl get svc -n monitoring prometheus-operated --context "$KUBE_CONTEXT" >/dev/null 2>&1 &&
    kubectl get svc -n monitoring prometheus-kube-prometheus-alertmanager --context "$KUBE_CONTEXT" >/dev/null 2>&1 &&
    kubectl get svc -n monitoring loki --context "$KUBE_CONTEXT" >/dev/null 2>&1 &&
    kubectl get svc -n monitoring prometheus-grafana --context "$KUBE_CONTEXT" >/dev/null 2>&1
}

require_monitoring_stack() {
  if ! monitoring_stack_exists; then
    die "Monitoring stack is not deployed. Run: ./ops.sh bootstrap"
    return 1
  fi
}

demo_services_exist() {
  kubectl get svc -n demo order-service --context "$KUBE_CONTEXT" >/dev/null 2>&1
}

require_demo_services() {
  if ! demo_services_exist; then
    die "Demo services are not deployed. Run: ./ops.sh demo start"
    return 1
  fi
}

deploy_demo_services() {
  local service
  local services=(frontend order payment inventory)

  require_kind_cluster
  info "Packaging Java demo services"
  (
    cd "$ROOT_DIR/demo-services"
    mvn clean package -DskipTests
  )

  for service in "${services[@]}"; do
    info "Building demo-$service:latest"
    docker build -t "demo-$service:latest" "$ROOT_DIR/demo-services/$service-service"
    info "Loading demo-$service:latest into Kind"
    kind load docker-image "demo-$service:latest" --name "$CLUSTER_NAME"
  done

  info "Applying demo namespace"
  kubectl apply -f "$ROOT_DIR/k8s/demo-services/namespace.yaml" --context "$KUBE_CONTEXT"
  kubectl wait --for=jsonpath='{.status.phase}'=Active namespace/demo \
    --timeout="${OPS_KUBECTL_TIMEOUT:-180s}" --context "$KUBE_CONTEXT"

  info "Deploying demo services"
  kubectl apply -f "$ROOT_DIR/k8s/demo-services/" --context "$KUBE_CONTEXT"
  for service in "${services[@]}"; do
    kubectl rollout status "deployment/$service-service" -n demo --context "$KUBE_CONTEXT" \
      --timeout="${OPS_KUBECTL_TIMEOUT:-180s}"
  done
}

remove_demo_services() {
  stop_managed_process order-service
  if kind_cluster_exists; then
    kubectl delete namespace demo --ignore-not-found --context "$KUBE_CONTEXT"
  fi
}

restart_demo_services() {
  remove_demo_services
  deploy_demo_services
}

print_managed_process_status() {
  local name="$1"
  if is_managed_process_running "$name"; then
    printf '  %-16s running (pid=%s)\n' "$name" "$(cat "$PID_DIR/$name.pid")"
  else
    printf '  %-16s stopped\n' "$name"
  fi
}

print_http_status() {
  local name="$1"
  local url="$2"
  if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
    printf '  %-16s healthy (%s)\n' "$name" "$url"
  else
    printf '  %-16s unavailable (%s)\n' "$name" "$url"
  fi
}

print_configuration_warnings() {
  local deepseek_api_key

  deepseek_api_key="$(env_value DEEPSEEK_API_KEY '')"
  if [[ -z "$deepseek_api_key" || "$deepseek_api_key" == sk-your-* ]]; then
    warn "DEEPSEEK_API_KEY is empty or still a placeholder. LLM-assisted diagnosis may be unavailable."
  fi
  if grep -Rq '"chat_id": "oc_chat_' "$ROOT_DIR/agent/tools/cmdb.py"; then
    warn "Feishu CMDB chat_id values still use oc_chat_* placeholders. Replace them with real group chat_id values to deliver cards."
  fi
}

show_status() {
  local name

  printf '=== Ops AI Agent Status ===\n'
  printf '\n[Docker]\n'
  if docker info >/dev/null 2>&1; then
    printf '  daemon           reachable\n'
    compose ps
  else
    printf '  daemon           unavailable\n'
  fi

  printf '\n[Kind]\n'
  if kind_cluster_exists; then
    kubectl get nodes --context "$KUBE_CONTEXT"
  else
    printf '  cluster          missing (%s)\n' "$KUBE_CONTEXT"
  fi

  printf '\n[Managed processes]\n'
  for name in kubectl-proxy prometheus alertmanager loki grafana order-service agent; do
    print_managed_process_status "$name"
  done

  printf '\n[Endpoints]\n'
  print_http_status agent http://localhost:8000/health
  print_http_status prometheus http://localhost:9090/-/healthy
  print_http_status alertmanager http://localhost:9093/-/healthy
  print_http_status loki http://localhost:3100/ready
  print_http_status grafana http://localhost:30030/login
  print_http_status order-service http://localhost:8081/actuator/health

  printf '\n[Demo services]\n'
  if demo_services_exist; then
    kubectl get deployments -n demo --context "$KUBE_CONTEXT"
  else
    printf '  namespace        not deployed\n'
  fi

  printf '\n[Configuration]\n'
  print_configuration_warnings
}

show_logs() {
  local name="${1:-agent}"
  local log_file="$LOG_DIR/$name.log"

  case "$name" in
    agent|kubectl-proxy|prometheus|alertmanager|loki|grafana|order-service)
      ;;
    *)
      die "Unknown log name: $name. Choose agent, kubectl-proxy, prometheus, alertmanager, loki, grafana, or order-service."
      return 1
      ;;
  esac

  if [[ ! -f "$log_file" ]]; then
    die "Log file does not exist yet: $log_file"
    return 1
  fi
  tail -f "$log_file"
}

bootstrap() {
  check_dependencies
  check_docker_daemon
  init_env_file
  ensure_python_env
  start_data_services
  wait_for_data_services
  ensure_kind_cluster
  deploy_monitoring
  wait_for_monitoring
  deploy_demo_services
  start_runtime_processes
  show_status
}

start_existing() {
  check_dependencies
  check_docker_daemon
  init_env_file
  start_data_services
  wait_for_data_services
  require_kind_cluster
  require_monitoring_stack
  require_demo_services
  start_runtime_processes
  show_status
}

restart_runtime() {
  check_dependencies
  check_docker_daemon
  require_kind_cluster
  require_monitoring_stack
  require_demo_services
  stop_runtime_processes
  start_runtime_processes
  show_status
}

stop_environment() {
  stop_runtime_processes
  if docker info >/dev/null 2>&1; then
    info "Stopping PostgreSQL and Redis"
    compose down
  fi
}

run_e2e() {
  print_http_status agent http://localhost:8000/health
  print_http_status prometheus http://localhost:9090/-/healthy
  print_http_status order-service http://localhost:8081/actuator/health
  bash "$ROOT_DIR/tests/e2e_phase1.sh"
}

clean_environment() {
  local remove_volumes=0
  local assume_yes=0
  local arg

  for arg in "$@"; do
    case "$arg" in
      --all)
        remove_volumes=1
        ;;
      --yes)
        assume_yes=1
        ;;
      *)
        die "Unknown clean option: $arg"
        return 1
        ;;
    esac
  done

  printf 'This will stop local services and delete Kind cluster %s.\n' "$CLUSTER_NAME"
  if [[ "$remove_volumes" -eq 1 ]]; then
    printf 'PostgreSQL and Redis Docker volumes will also be deleted.\n'
  fi
  if [[ "$assume_yes" -ne 1 ]]; then
    local reply
    read -r -p 'Continue? [y/N] ' reply
    [[ "$reply" =~ ^[Yy]$ ]] || {
      info "Clean cancelled"
      return
    }
  fi

  stop_runtime_processes
  if docker info >/dev/null 2>&1; then
    if [[ "$remove_volumes" -eq 1 ]]; then
      compose down -v
    else
      compose down
    fi
  fi
  if kind_cluster_exists; then
    kind delete cluster --name "$CLUSTER_NAME"
  fi
  rm -rf "$OPS_DIR"
  info "Local environment cleaned"
}

main() {
  local command="${1:-help}"
  shift || true

  case "$command" in
    help|-h|--help)
      usage
      ;;
    bootstrap)
      bootstrap
      ;;
    start)
      start_existing
      ;;
    restart)
      restart_runtime
      ;;
    stop)
      stop_environment
      ;;
    status)
      show_status
      ;;
    logs)
      show_logs "${1:-agent}"
      ;;
    demo)
      case "${1:-}" in
        start)
          deploy_demo_services
          ;;
        stop)
          remove_demo_services
          ;;
        restart)
          restart_demo_services
          ;;
        *)
          die "Usage: ./ops.sh demo {start|stop|restart}"
          return 1
          ;;
      esac
      ;;
    test)
      run_e2e
      ;;
    clean)
      clean_environment "$@"
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
