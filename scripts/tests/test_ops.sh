#!/usr/bin/env bash
set -u

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
OPS_SCRIPT="$PROJECT_ROOT/ops.sh"
PASS_COUNT=0
FAIL_COUNT=0

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf 'PASS: %s\n' "$1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf 'FAIL: %s\n' "$1" >&2
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    pass "$message"
  else
    fail "$message (missing: $needle)"
  fi
}

assert_file_exists() {
  local path="$1"
  local message="$2"
  if [[ -f "$path" ]]; then
    pass "$message"
  else
    fail "$message (missing: $path)"
  fi
}

assert_file_missing() {
  local path="$1"
  local message="$2"
  if [[ ! -e "$path" ]]; then
    pass "$message"
  else
    fail "$message (still exists: $path)"
  fi
}

assert_not_equal() {
  local actual="$1"
  local unexpected="$2"
  local message="$3"
  if [[ "$actual" != "$unexpected" ]]; then
    pass "$message"
  else
    fail "$message (unexpected: $unexpected)"
  fi
}

assert_equal() {
  local actual="$1"
  local expected="$2"
  local message="$3"
  if [[ "$actual" == "$expected" ]]; then
    pass "$message"
  else
    fail "$message (expected: $expected, actual: $actual)"
  fi
}

new_fixture() {
  FIXTURE="$(mktemp -d)"
  mkdir -p "$FIXTURE/.ops/pids" "$FIXTURE/.ops/logs"
  printf 'POSTGRES_USER=opsagent\n' >"$FIXTURE/.env.example"
}

remove_fixture() {
  rm -rf "$FIXTURE"
}

source_ops() {
  OPS_SH_SOURCE_ONLY=1 OPS_ROOT_DIR="$FIXTURE" OPS_DIR="$FIXTURE/.ops" \
    source "$OPS_SCRIPT"
}

test_usage_lists_primary_commands() {
  new_fixture
  source_ops
  local output
  output="$(usage)"
  assert_contains "$output" './ops.sh bootstrap' 'help lists bootstrap'
  assert_contains "$output" './ops.sh status' 'help lists status'
  assert_contains "$output" './ops.sh stop' 'help lists stop'
  remove_fixture
}

test_init_env_file_copies_example() {
  new_fixture
  source_ops
  init_env_file
  assert_file_exists "$FIXTURE/.env" 'init_env_file creates .env'
  assert_contains "$(cat "$FIXTURE/.env")" 'POSTGRES_USER=opsagent' '.env comes from example'
  remove_fixture
}

test_stale_pid_is_removed() {
  new_fixture
  source_ops
  printf '999999\n' >"$PID_DIR/stale.pid"
  if is_managed_process_running stale; then
    fail 'stale PID is not considered running'
  else
    pass 'stale PID is not considered running'
  fi
  assert_file_missing "$PID_DIR/stale.pid" 'stale PID file is removed'
  remove_fixture
}

test_unknown_command_prints_usage() {
  new_fixture
  source_ops
  local output
  local exit_code
  set +e
  output="$(main unknown 2>&1)"
  exit_code=$?
  set -e
  if [[ "$exit_code" -ne 0 ]]; then
    pass 'unknown command exits non-zero'
  else
    fail 'unknown command exits non-zero'
  fi
  assert_contains "$output" 'Unknown command: unknown' 'unknown command is named'
  assert_contains "$output" './ops.sh help' 'unknown command prints usage'
  remove_fixture
}

test_managed_process_lifecycle() {
  new_fixture
  source_ops
  start_managed_process sleeper "" sleep 30
  assert_file_exists "$PID_DIR/sleeper.pid" 'managed process writes PID file'
  if is_managed_process_running sleeper; then
    pass 'managed process reports running'
  else
    fail 'managed process reports running'
  fi
  stop_managed_process sleeper
  assert_file_missing "$PID_DIR/sleeper.pid" 'managed stop removes PID file'
  remove_fixture
}

test_stale_pid_is_replaced_when_starting() {
  new_fixture
  source_ops
  printf '999999\n' >"$PID_DIR/sleeper.pid"
  start_managed_process sleeper "" sleep 30
  assert_not_equal "$(cat "$PID_DIR/sleeper.pid")" '999999' 'managed start replaces stale PID'
  stop_managed_process sleeper
  remove_fixture
}

test_unmanaged_port_is_rejected() {
  new_fixture
  source_ops
  port_in_use() {
    return 0
  }
  local output
  local exit_code
  set +e
  output="$(start_managed_process occupied 45678 sleep 30 2>&1)"
  exit_code=$?
  set -e
  if [[ "$exit_code" -ne 0 ]]; then
    pass 'unmanaged occupied port exits non-zero'
  else
    fail 'unmanaged occupied port exits non-zero'
  fi
  assert_contains "$output" 'Port 45678 is already in use' 'occupied port error is actionable'
  assert_file_missing "$PID_DIR/occupied.pid" 'occupied port does not create PID file'
  remove_fixture
}

test_managed_process_detaches_from_launcher_group() {
  new_fixture
  source_ops
  start_managed_process detached "" sleep 30
  local pid
  local launcher_group
  local managed_group
  pid="$(cat "$PID_DIR/detached.pid")"
  launcher_group="$(ps -p "$$" -o pgid= | tr -d ' ')"
  managed_group="$(ps -p "$pid" -o pgid= | tr -d ' ')"
  assert_not_equal "$managed_group" "$launcher_group" 'managed process detaches from launcher process group'
  stop_managed_process detached
  remove_fixture
}

test_primary_command_dispatch() {
  new_fixture
  source_ops
  local trace="$FIXTURE/trace"
  bootstrap() { printf 'bootstrap\n' >>"$trace"; }
  start_existing() { printf 'start\n' >>"$trace"; }
  restart_runtime() { printf 'restart\n' >>"$trace"; }
  stop_environment() { printf 'stop\n' >>"$trace"; }
  show_status() { printf 'status\n' >>"$trace"; }
  show_logs() { printf 'logs:%s\n' "$1" >>"$trace"; }
  run_e2e() { printf 'test\n' >>"$trace"; }
  clean_environment() { printf 'clean:%s\n' "$*" >>"$trace"; }

  main bootstrap
  main start
  main restart
  main stop
  main status
  main logs
  main logs loki
  main test
  main clean --yes
  main clean --all --yes

  assert_equal "$(cat "$trace")" "$(cat <<'EOF'
bootstrap
start
restart
stop
status
logs:agent
logs:loki
test
clean:--yes
clean:--all --yes
EOF
)" 'primary commands dispatch to orchestration functions'
  remove_fixture
}

test_demo_command_dispatch() {
  new_fixture
  source_ops
  local trace="$FIXTURE/trace"
  deploy_demo_services() { printf 'demo:start\n' >>"$trace"; }
  remove_demo_services() { printf 'demo:stop\n' >>"$trace"; }
  restart_demo_services() { printf 'demo:restart\n' >>"$trace"; }

  main demo start
  main demo stop
  main demo restart

  assert_equal "$(cat "$trace")" "$(cat <<'EOF'
demo:start
demo:stop
demo:restart
EOF
)" 'demo subcommands dispatch to demo functions'
  remove_fixture
}

test_missing_dependency_prints_platform_hint() {
  new_fixture
  source_ops
  command_exists() {
    [[ "$1" != helm ]]
  }
  detect_os() {
    printf 'macos\n'
  }
  local output
  local exit_code
  set +e
  output="$(check_dependencies 2>&1)"
  exit_code=$?
  set -e
  if [[ "$exit_code" -ne 0 ]]; then
    pass 'missing dependency exits non-zero'
  else
    fail 'missing dependency exits non-zero'
  fi
  assert_contains "$output" 'Missing required command: helm' 'missing dependency is named'
  assert_contains "$output" 'brew install' 'macOS dependency hint uses Homebrew'
  remove_fixture
}

test_documentation_recommends_ops_cli() {
  local readme
  local deployment
  readme="$(cat "$PROJECT_ROOT/README.md")"
  deployment="$(cat "$PROJECT_ROOT/docs/deployment.md")"

  assert_contains "$readme" './ops.sh bootstrap' 'README recommends bootstrap command'
  assert_contains "$readme" './ops.sh status' 'README documents status command'
  assert_contains "$readme" './ops.sh stop' 'README documents stop command'
  assert_contains "$deployment" './ops.sh help' 'deployment guide links CLI help'
  if [[ "$deployment" != *'scripts/start-all.sh'* ]]; then
    pass 'deployment guide removes outdated start-all script'
  else
    fail 'deployment guide removes outdated start-all script'
  fi
}

test_e2e_refuses_unhealthy_environment() {
  new_fixture
  source_ops
  http_healthy() {
    return 1
  }
  local output
  local exit_code
  set +e
  output="$(run_e2e 2>&1)"
  exit_code=$?
  set -e
  if [[ "$exit_code" -ne 0 ]]; then
    pass 'E2E preflight exits non-zero for unhealthy environment'
  else
    fail 'E2E preflight exits non-zero for unhealthy environment'
  fi
  assert_contains "$output" 'Agent is unavailable' 'E2E preflight names unavailable component'
  remove_fixture
}

test_retry_command_recovers_from_transient_failure() {
  new_fixture
  source_ops
  local attempts=0
  flaky_command() {
    attempts=$((attempts + 1))
    [[ "$attempts" -ge 3 ]]
  }

  OPS_RETRY_DELAY=0 retry_command 'flaky command' 3 flaky_command
  assert_equal "$attempts" '3' 'retry command recovers after transient failures'
  remove_fixture
}

test_existing_helm_repo_does_not_require_network_refresh() {
  new_fixture
  source_ops
  local trace="$FIXTURE/trace"
  helm_repo_exists() {
    return 0
  }
  retry_command() {
    printf 'network\n' >>"$trace"
  }

  ensure_helm_repo prometheus-community https://example.invalid/charts
  assert_file_missing "$trace" 'existing Helm repository skips network refresh'
  remove_fixture
}

test_cached_helm_chart_is_preferred() {
  new_fixture
  source_ops
  local cache="$FIXTURE/helm-cache"
  mkdir -p "$cache"
  touch "$cache/kube-prometheus-stack-86.1.0.tgz"
  helm() {
    if [[ "$1 $2" == 'env HELM_REPOSITORY_CACHE' ]]; then
      printf '"%s"\n' "$cache"
    fi
  }

  assert_equal "$(resolve_helm_chart prometheus-community/kube-prometheus-stack kube-prometheus-stack)" \
    "$cache/kube-prometheus-stack-86.1.0.tgz" 'cached Helm chart archive is preferred'
  remove_fixture
}

test_demo_deploy_restarts_existing_workloads() {
  new_fixture
  mkdir -p "$FIXTURE/demo-services" "$FIXTURE/k8s/demo-services"
  source_ops
  local trace="$FIXTURE/trace"
  require_kind_cluster() {
    return 0
  }
  ensure_demo_base_image() {
    return 0
  }
  retry_command() {
    shift 2
    "$@"
  }
  mvn() {
    return 0
  }
  docker() {
    return 0
  }
  kind() {
    return 0
  }
  kubectl() {
    printf '%s\n' "$*" >>"$trace"
  }
  stop_managed_process() {
    printf 'stop:%s\n' "$1" >>"$trace"
  }
  start_order_service_forward() {
    printf 'start:order-service\n' >>"$trace"
  }
  printf '12345\n' >"$PID_DIR/order-service.pid"

  deploy_demo_services
  assert_contains "$(cat "$trace")" 'rollout restart deployment/frontend-service -n demo' \
    'demo deploy restarts existing frontend workload'
  assert_contains "$(cat "$trace")" 'rollout restart deployment/order-service -n demo' \
    'demo deploy restarts existing order workload'
  assert_contains "$(cat "$trace")" 'rollout restart deployment/payment-service -n demo' \
    'demo deploy restarts existing payment workload'
  assert_contains "$(cat "$trace")" 'rollout restart deployment/inventory-service -n demo' \
    'demo deploy restarts existing inventory workload'
  assert_contains "$(cat "$trace")" 'stop:order-service' \
    'demo deploy stops stale order forward after rollout'
  assert_contains "$(cat "$trace")" 'start:order-service' \
    'demo deploy recreates order forward after rollout'
  remove_fixture
}

test_demo_restart_restores_order_forward() {
  new_fixture
  source_ops
  local trace="$FIXTURE/trace"
  remove_demo_services() {
    rm -f "$PID_DIR/order-service.pid"
    printf 'remove-demo\n' >>"$trace"
  }
  deploy_demo_services() {
    printf 'deploy-demo\n' >>"$trace"
  }
  start_order_service_forward() {
    printf 'start:order-service\n' >>"$trace"
  }
  printf '12345\n' >"$PID_DIR/order-service.pid"

  restart_demo_services
  assert_contains "$(cat "$trace")" 'start:order-service' \
    'demo restart restores order forward'
  remove_fixture
}

run_all() {
  test_usage_lists_primary_commands
  test_init_env_file_copies_example
  test_stale_pid_is_removed
  test_unknown_command_prints_usage
  test_managed_process_lifecycle
  test_stale_pid_is_replaced_when_starting
  test_unmanaged_port_is_rejected
  test_managed_process_detaches_from_launcher_group
  test_primary_command_dispatch
  test_demo_command_dispatch
  test_missing_dependency_prints_platform_hint
  test_documentation_recommends_ops_cli
  test_e2e_refuses_unhealthy_environment
  test_retry_command_recovers_from_transient_failure
  test_existing_helm_repo_does_not_require_network_refresh
  test_cached_helm_chart_is_preferred
  test_demo_deploy_restarts_existing_workloads
  test_demo_restart_restores_order_forward

  printf '\nTests: %s passed, %s failed\n' "$PASS_COUNT" "$FAIL_COUNT"
  [[ "$FAIL_COUNT" -eq 0 ]]
}

run_all
