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

run_all() {
  test_usage_lists_primary_commands
  test_init_env_file_copies_example
  test_stale_pid_is_removed
  test_unknown_command_prints_usage
  test_managed_process_lifecycle
  test_stale_pid_is_replaced_when_starting
  test_unmanaged_port_is_rejected

  printf '\nTests: %s passed, %s failed\n' "$PASS_COUNT" "$FAIL_COUNT"
  [[ "$FAIL_COUNT" -eq 0 ]]
}

run_all
