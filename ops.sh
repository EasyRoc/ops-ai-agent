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
Ops AI Agent 本地环境管理工具

用法:
  ./ops.sh bootstrap      初始化并启动完整本地环境
  ./ops.sh start          启动已有的本地环境
  ./ops.sh restart        重启所有托管的后台进程
  ./ops.sh stop           停止托管进程和 Docker Compose 服务
  ./ops.sh status         查看各组件健康状态
  ./ops.sh logs [名称]    实时查看托管进程日志（默认: agent）
  ./ops.sh demo start     构建、加载并部署 demo 服务
  ./ops.sh demo stop      删除 demo 服务
  ./ops.sh demo restart   重新构建并部署 demo 服务
  ./ops.sh test           运行 CPU 故障注入端到端测试
  ./ops.sh clean          停止服务并删除 Kind 集群
  ./ops.sh clean --all    同时删除 Docker Compose 数据卷
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
    printf '缺少环境变量模板文件: %s\n' "$ROOT_DIR/.env.example" >&2
    return 1
  fi
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  printf '已从 .env.example 创建 %s\n' "$ROOT_DIR/.env"
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

spawn_detached_process() {
  local pid_file="$1"
  local log_file="$2"
  shift 2

  python3 - "$pid_file" "$log_file" "$ROOT_DIR" "$@" <<'PY'
import os
import sys

pid_file, log_file, root_dir, *command = sys.argv[1:]

first_pid = os.fork()
if first_pid > 0:
    os.waitpid(first_pid, 0)
    raise SystemExit(0)

os.setsid()
second_pid = os.fork()
if second_pid > 0:
    with open(pid_file, "w", encoding="utf-8") as handle:
        handle.write(f"{second_pid}\n")
    os._exit(0)

os.chdir(root_dir)
with open(os.devnull, "rb", buffering=0) as stdin, open(log_file, "ab", buffering=0) as log:
    os.dup2(stdin.fileno(), 0)
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    os.execvp(command[0], command)
PY
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
    info "$name 已在运行 (pid=$(cat "$pid_file"))"
    return
  fi

  if [[ -n "$port" ]] && port_in_use "$port"; then
    die "端口 $port 已被非托管进程占用，请检查: lsof -nP -iTCP:$port -sTCP:LISTEN"
    return 1
  fi

  : >"$log_file"
  spawn_detached_process "$pid_file" "$log_file" "$@"

  sleep "${OPS_PROCESS_START_DELAY:-1}"
  if ! is_managed_process_running "$name"; then
    warn "$name 启动时异常退出，最近日志:"
    tail -n 20 "$log_file" >&2 || true
    return 1
  fi

  pid="$(cat "$pid_file")"
  info "已启动 $name (pid=$pid${port:+, port=$port})"
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
  info "已停止 $name"
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

start_order_service_forward() {
  start_managed_process order-service 8081 \
    kubectl port-forward -n demo svc/order-service 8081:8081 --context "$KUBE_CONTEXT"
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
  start_order_service_forward

  if [[ ! -x "$ROOT_DIR/.venv/bin/uvicorn" ]]; then
    die "缺少 $ROOT_DIR/.venv/bin/uvicorn，请执行: ./ops.sh bootstrap"
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
    printf '请使用 Homebrew 安装缺失工具: brew install %s\n' "$command_name"
  else
    printf '请使用包管理器安装缺失工具，如: apt-get install %s 或 brew install %s\n' \
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
    die "不支持的操作系统: $(uname -s)，仅支持 macOS 和 Linux"
    return 1
  fi

  for command_name in "${required[@]}"; do
    if ! command_exists "$command_name"; then
      printf '[错误] 缺少必需命令: %s\n' "$command_name" >&2
      dependency_hint "$os_name" "$command_name" >&2
      missing=1
    fi
  done
  if [[ "$missing" -ne 0 ]]; then
    return 1
  fi

  if ! docker compose version >/dev/null 2>&1; then
    die "需要 Docker Compose v2，请执行 docker compose version 确认"
    return 1
  fi
}

check_docker_daemon() {
  if ! docker info >/dev/null 2>&1; then
    die "Docker 守护进程不可达，请启动 Docker Desktop 或 Docker 服务后重试"
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

  die "${description} 超时（等待 ${timeout_seconds}s）"
}

data_services_healthy() {
  compose exec -T postgres pg_isready -U "$(env_value POSTGRES_USER opsagent)" -d "$(env_value POSTGRES_DB ops_agent)" &&
    compose exec -T redis redis-cli ping | grep -q PONG
}

start_data_services() {
  info "启动 PostgreSQL 和 Redis"
  compose up -d
}

wait_for_data_services() {
  info "等待 PostgreSQL 和 Redis 就绪"
  wait_for_condition "PostgreSQL 和 Redis 就绪" "${OPS_DATA_TIMEOUT:-90}" data_services_healthy
}

ensure_python_env() {
  if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
    info "创建 Python 虚拟环境"
    python3 -m venv "$ROOT_DIR/.venv"
  fi
  info "安装 Python 依赖"
  "$ROOT_DIR/.venv/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"
}

kind_cluster_exists() {
  kubectl config get-contexts -o name | grep -Fxq "$KUBE_CONTEXT"
}

ensure_kind_cluster() {
  if kind_cluster_exists; then
    info "Kind 集群 $CLUSTER_NAME 已存在"
    return
  fi
  info "创建 Kind 集群 $CLUSTER_NAME"
  kind create cluster --name "$CLUSTER_NAME" --config "$ROOT_DIR/kind-config.yaml"
}

require_kind_cluster() {
  if ! kind_cluster_exists; then
    die "Kind 集群 $CLUSTER_NAME 不存在，请执行: ./ops.sh bootstrap"
    return 1
  fi
}

helm_repo_exists() {
  local name="$1"
  helm repo list 2>/dev/null | awk 'NR > 1 {print $1}' | grep -Fxq "$name"
}

ensure_helm_repo() {
  local name="$1"
  local url="$2"

  if helm_repo_exists "$name"; then
    info "使用已有 Helm 仓库 $name"
    return
  fi

  info "添加 Helm 仓库 $name"
  retry_command "Add Helm repository $name" "${OPS_HELM_REPO_ATTEMPTS:-3}" \
    helm repo add "$name" "$url"
}

resolve_helm_chart() {
  local remote_chart="$1"
  local archive_prefix="$2"
  local cache_dir
  local archive=""

  cache_dir="$(helm env HELM_REPOSITORY_CACHE | tr -d '"')"
  if [[ -d "$cache_dir" ]]; then
    archive="$(find "$cache_dir" -maxdepth 1 -type f -name "$archive_prefix-*.tgz" -print | sort | tail -n 1)"
  fi
  printf '%s\n' "${archive:-$remote_chart}"
}

deploy_monitoring() {
  local prometheus_chart
  local promtail_chart

  info "配置 Helm 仓库"
  ensure_helm_repo prometheus-community https://prometheus-community.github.io/helm-charts
  ensure_helm_repo grafana https://grafana.github.io/helm-charts
  prometheus_chart="$(resolve_helm_chart prometheus-community/kube-prometheus-stack kube-prometheus-stack)"
  promtail_chart="$(resolve_helm_chart grafana/promtail promtail)"

  info "部署 Prometheus、Alertmanager、Grafana (chart: $prometheus_chart)"
  helm upgrade --install prometheus "$prometheus_chart" \
    --namespace monitoring --create-namespace \
    -f "$ROOT_DIR/k8s/monitoring/prometheus-values.yaml" \
    -f "$ROOT_DIR/k8s/monitoring/grafana-values.yaml" \
    --wait --timeout "${OPS_HELM_TIMEOUT:-10m}"

  info "部署 Loki"
  kubectl apply -f "$ROOT_DIR/k8s/monitoring/loki.yaml" --context "$KUBE_CONTEXT"

  info "部署 Promtail (chart: $promtail_chart)"
  helm upgrade --install promtail "$promtail_chart" \
    --namespace monitoring --create-namespace \
    -f "$ROOT_DIR/k8s/monitoring/promtail-values.yaml" \
    --wait --timeout "${OPS_HELM_TIMEOUT:-10m}"
}

wait_for_monitoring() {
  info "等待 Loki 部署完成"
  kubectl rollout status deployment/loki -n monitoring --context "$KUBE_CONTEXT" \
    --timeout="${OPS_KUBECTL_TIMEOUT:-180s}"
  info "等待 Promtail 部署完成"
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
    die "监控组件未部署，请执行: ./ops.sh bootstrap"
    return 1
  fi
}

demo_services_exist() {
  kubectl get svc -n demo order-service --context "$KUBE_CONTEXT" >/dev/null 2>&1
}

require_demo_services() {
  if ! demo_services_exist; then
    die "Demo 服务未部署，请执行: ./ops.sh demo start"
    return 1
  fi
}

retry_command() {
  local description="$1"
  local max_attempts="$2"
  shift 2
  local attempt

  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    if "$@"; then
      return
    fi
    if ((attempt < max_attempts)); then
      warn "$description 失败 (第 $attempt/$max_attempts 次)，重试中..."
      sleep "${OPS_RETRY_DELAY:-5}"
    fi
  done

  die "$description 失败，已重试 $max_attempts 次"
}

ensure_demo_base_image() {
  local image="${OPS_DEMO_BASE_IMAGE:-eclipse-temurin:17-jre}"

  if docker image inspect "$image" >/dev/null 2>&1; then
    info "使用缓存的基础镜像 $image"
    return
  fi
  info "拉取基础镜像 $image"
  retry_command "Pull demo base image $image" "${OPS_DOCKER_PULL_ATTEMPTS:-3}" \
    docker pull "$image"
}

deploy_demo_services() {
  local service
  local restart_order_forward=false
  local services=(frontend order payment inventory)

  if [[ -f "$PID_DIR/order-service.pid" ]]; then
    restart_order_forward=true
  fi

  require_kind_cluster
  info "打包 Java demo 服务"
  (
    cd "$ROOT_DIR/demo-services"
    mvn clean package -DskipTests
  )

  ensure_demo_base_image
  for service in "${services[@]}"; do
    info "构建镜像 demo-$service:latest"
    retry_command "构建 demo-$service:latest" "${OPS_DOCKER_BUILD_ATTEMPTS:-3}" \
      docker build -t "demo-$service:latest" "$ROOT_DIR/demo-services/$service-service"
    info "加载镜像 demo-$service:latest 到 Kind"
    kind load docker-image "demo-$service:latest" --name "$CLUSTER_NAME"
  done

  info "创建 demo 命名空间"
  kubectl apply -f "$ROOT_DIR/k8s/demo-services/namespace.yaml" --context "$KUBE_CONTEXT"
  kubectl wait --for=jsonpath='{.status.phase}'=Active namespace/demo \
    --timeout="${OPS_KUBECTL_TIMEOUT:-180s}" --context "$KUBE_CONTEXT"

  info "部署 demo 服务"
  kubectl apply -f "$ROOT_DIR/k8s/demo-services/" --context "$KUBE_CONTEXT"
  for service in "${services[@]}"; do
    kubectl rollout restart "deployment/$service-service" -n demo --context "$KUBE_CONTEXT"
  done
  for service in "${services[@]}"; do
    kubectl rollout status "deployment/$service-service" -n demo --context "$KUBE_CONTEXT" \
      --timeout="${OPS_KUBECTL_TIMEOUT:-180s}"
  done
  if [[ "$restart_order_forward" == true ]]; then
    stop_managed_process order-service
    start_order_service_forward
  fi
}

remove_demo_services() {
  stop_managed_process order-service
  if kind_cluster_exists; then
    kubectl delete namespace demo --ignore-not-found --context "$KUBE_CONTEXT"
  fi
}

restart_demo_services() {
  local restart_order_forward=false

  if [[ -f "$PID_DIR/order-service.pid" ]]; then
    restart_order_forward=true
  fi
  remove_demo_services
  deploy_demo_services
  if [[ "$restart_order_forward" == true ]]; then
    start_order_service_forward
  fi
}

print_managed_process_status() {
  local name="$1"
  if is_managed_process_running "$name"; then
    printf '  %-16s 运行中 (pid=%s)\n' "$name" "$(cat "$PID_DIR/$name.pid")"
  else
    printf '  %-16s 已停止\n' "$name"
  fi
}

http_healthy() {
  local url="$1"
  curl -fsS --max-time 2 "$url" >/dev/null 2>&1
}

print_http_status() {
  local name="$1"
  local url="$2"
  if http_healthy "$url"; then
    printf '  %-16s 正常 (%s)\n' "$name" "$url"
  else
    printf '  %-16s 不可用 (%s)\n' "$name" "$url"
  fi
}

require_http_health() {
  local name="$1"
  local url="$2"

  if ! http_healthy "$url"; then
    die "$name 不可达 ($url)，请执行: ./ops.sh restart"
    return 1
  fi
}

print_demo_target_status() {
  local result

  if ! http_healthy http://localhost:9090/-/healthy; then
    printf '  prometheus       不可用\n'
    return
  fi

  if ! result="$(curl -fsSG --max-time 2 http://localhost:9090/api/v1/query \
    --data-urlencode 'query=up{namespace="demo"}' | python3 -c '
import json
import sys

targets = json.load(sys.stdin)["data"]["result"]
up = sum(sample["value"][1] == "1" for sample in targets)
print(f"{up}/{len(targets)} targets UP")
')"; then
    printf '  prometheus       指标查询失败\n'
    return
  fi
  printf '  prometheus       %s\n' "$result"
}

print_configuration_warnings() {
  local deepseek_api_key

  deepseek_api_key="$(env_value DEEPSEEK_API_KEY '')"
  if [[ -z "$deepseek_api_key" || "$deepseek_api_key" == sk-your-* ]]; then
    warn "DEEPSEEK_API_KEY 为空或仍是占位符，LLM 辅助诊断可能不可用"
  fi
  if grep -Rq '"chat_id": "oc_chat_' "$ROOT_DIR/agent/tools/cmdb.py"; then
    warn "CMDB 中仍有 oc_chat_* 占位符 chat_id，建议在 .env 中配置 SERVICE_CHAT_IDS 以发送飞书卡片通知"
  fi
}

show_status() {
  local name

  printf '=== Ops AI Agent 状态 ===\n'
  printf '\n[Docker]\n'
  if docker info >/dev/null 2>&1; then
    printf '  守护进程          可达\n'
    compose ps
  else
    printf '  守护进程          不可达\n'
  fi

  printf '\n[Kind]\n'
  if kind_cluster_exists; then
    kubectl get nodes --context "$KUBE_CONTEXT"
  else
    printf '  集群              未找到 (%s)\n' "$KUBE_CONTEXT"
  fi

  printf '\n[托管进程]\n'
  for name in kubectl-proxy prometheus alertmanager loki grafana order-service agent; do
    print_managed_process_status "$name"
  done

  printf '\n[服务端点]\n'
  print_http_status agent http://localhost:8000/health
  print_http_status prometheus http://localhost:9090/-/healthy
  print_http_status alertmanager http://localhost:9093/-/healthy
  print_http_status loki http://localhost:3100/ready
  print_http_status grafana http://localhost:30030/login
  print_http_status order-service http://localhost:8081/actuator/health

  printf '\n[Demo 服务]\n'
  if demo_services_exist; then
    kubectl get deployments -n demo --context "$KUBE_CONTEXT"
    print_demo_target_status
  else
    printf '  命名空间          未部署\n'
  fi

  printf '\n[配置]\n'
  print_configuration_warnings
}

show_logs() {
  local name="${1:-agent}"
  local log_file="$LOG_DIR/$name.log"

  case "$name" in
    agent|kubectl-proxy|prometheus|alertmanager|loki|grafana|order-service)
      ;;
    *)
      die "未知日志名称: $name，可选: agent, kubectl-proxy, prometheus, alertmanager, loki, grafana, order-service"
      return 1
      ;;
  esac

  if [[ ! -f "$log_file" ]]; then
    die "日志文件不存在: $log_file"
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
    info "停止 PostgreSQL 和 Redis"
    compose down
  fi
}

run_e2e() {
  require_http_health Agent http://localhost:8000/health
  require_http_health Prometheus http://localhost:9090/-/healthy
  require_http_health order-service http://localhost:8081/actuator/health
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
        die "未知 clean 选项: $arg"
        return 1
        ;;
    esac
  done

  printf '将停止本地服务并删除 Kind 集群 %s。\n' "$CLUSTER_NAME"
  if [[ "$remove_volumes" -eq 1 ]]; then
    printf '同时会删除 PostgreSQL 和 Redis 的 Docker 数据卷。\n'
  fi
  if [[ "$assume_yes" -ne 1 ]]; then
    local reply
    read -r -p '确认操作? [y/N] ' reply
    [[ "$reply" =~ ^[Yy]$ ]] || {
      info "清理已取消"
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
  info "本地环境已清理"
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
          die "用法: ./ops.sh demo {start|stop|restart}"
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
      printf '未知命令: %s\n\n' "$command" >&2
      usage >&2
      return 1
      ;;
  esac
}

if [[ "${OPS_SH_SOURCE_ONLY:-0}" != "1" ]]; then
  main "$@"
fi
