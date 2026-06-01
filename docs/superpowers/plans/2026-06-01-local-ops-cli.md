# Local Ops CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single `./ops.sh` command that bootstraps, starts, restarts, stops, inspects, tests, and cleans the complete local Ops AI Agent environment on macOS and Linux.

**Architecture:** Keep one repository-root Bash entrypoint with small functions grouped by concern: dependency validation, local state, managed processes, infrastructure deployment, demo deployment, status, and command dispatch. Store runtime PID and log files in ignored `.ops/` paths so daily commands affect only processes owned by this repository. Use a shell test harness that sources the script in a temporary fixture and stubs external commands for deterministic behavior.

**Tech Stack:** Bash, Docker Compose, Kind, kubectl, Helm, Maven, Python virtualenv, curl.

---

## File Structure

| File | Responsibility |
|---|---|
| `ops.sh` | User-facing command entrypoint and local environment orchestration |
| `scripts/tests/test_ops.sh` | Bash regression tests for helpers and command dispatch |
| `.gitignore` | Ignore `.ops/` generated runtime state |
| `README.md` | Make `./ops.sh bootstrap` the primary onboarding path |
| `docs/deployment.md` | Document script commands while retaining manual troubleshooting steps |

### Task 1: Add Shell Test Harness And Core Helpers

**Files:**
- Create: `scripts/tests/test_ops.sh`
- Create: `ops.sh`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing tests for help, environment initialization, stale PID cleanup, and unknown commands**

Create `scripts/tests/test_ops.sh` with a temporary fixture, source-only mode, assertion helpers, and tests that invoke:

```bash
OPS_SH_SOURCE_ONLY=1 source "$PROJECT_ROOT/ops.sh"
usage
init_env_file
is_managed_process_running "stale"
main unknown
```

Assert that help lists `bootstrap`, `.env.example` is copied to `.env`, stale PID files are removed, and an unknown command exits non-zero with usage text.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
bash scripts/tests/test_ops.sh
```

Expected: FAIL because `ops.sh` does not exist.

- [ ] **Step 3: Implement minimal sourceable script helpers**

Create `ops.sh` with:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
OPS_DIR="${OPS_DIR:-"$ROOT_DIR/.ops"}"
PID_DIR="$OPS_DIR/pids"
LOG_DIR="$OPS_DIR/logs"

usage() { ...; }
ensure_runtime_dirs() { mkdir -p "$PID_DIR" "$LOG_DIR"; }
init_env_file() { ...; }
is_managed_process_running() { ...; }
main() { ...; }

if [[ "${OPS_SH_SOURCE_ONLY:-0}" != "1" ]]; then
  main "$@"
fi
```

Add `.ops/` to `.gitignore`.

- [ ] **Step 4: Run tests and syntax check**

Run:

```bash
bash -n ops.sh
bash scripts/tests/test_ops.sh
```

Expected: PASS.

### Task 2: Add Managed Process Lifecycle

**Files:**
- Modify: `ops.sh`
- Modify: `scripts/tests/test_ops.sh`

- [ ] **Step 1: Write failing tests for managed process start, stop, stale PID replacement, and unmanaged port rejection**

Use a temporary PID/log directory and lightweight commands such as:

```bash
start_managed_process "sleeper" "" sleep 30
stop_managed_process "sleeper"
start_managed_process "occupied" "$TEST_PORT" sleep 30
```

Stub `port_in_use` where needed. Assert PID files are created, owned processes stop, stale files are replaced, and occupied unmanaged ports produce an actionable error.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
bash scripts/tests/test_ops.sh
```

Expected: FAIL because lifecycle helpers are absent.

- [ ] **Step 3: Implement lifecycle helpers and component launchers**

Add:

```bash
port_in_use() { ...; }
start_managed_process() { ...; }
stop_managed_process() { ...; }
stop_runtime_processes() { ...; }
start_runtime_processes() { ...; }
```

`start_runtime_processes` launches `kubectl proxy`, Prometheus, Alertmanager,
Loki, Grafana, order-service port-forwards, and the Agent. Each process writes
to `.ops/logs/<name>.log` and `.ops/pids/<name>.pid`.

- [ ] **Step 4: Run tests and syntax check**

Run:

```bash
bash -n ops.sh
bash scripts/tests/test_ops.sh
```

Expected: PASS.

### Task 3: Add Bootstrap, Daily Commands, Demo Commands, And Status

**Files:**
- Modify: `ops.sh`
- Modify: `scripts/tests/test_ops.sh`

- [ ] **Step 1: Write failing dispatch and dependency tests**

Stub external commands and assert dispatch for:

```bash
main bootstrap
main start
main restart
main stop
main status
main logs agent
main demo start
main demo stop
main demo restart
main test
main clean --yes
main clean --all --yes
```

Add a missing-command fixture and assert the error names the missing dependency
and prints macOS Homebrew or Linux package-manager guidance.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
bash scripts/tests/test_ops.sh
```

Expected: FAIL because orchestration and dispatch functions are absent.

- [ ] **Step 3: Implement orchestration functions**

Add functions for:

```bash
check_dependencies
check_docker_daemon
ensure_python_env
start_data_services
wait_for_data_services
ensure_kind_cluster
deploy_monitoring
wait_for_monitoring
deploy_demo_services
remove_demo_services
show_status
show_logs
run_e2e
clean_environment
bootstrap
start_existing
restart_runtime
stop_environment
```

Use:

```bash
helm upgrade --install
kubectl apply
kubectl wait --for=jsonpath='{.status.phase}'=Active namespace/demo
kubectl rollout status
```

`clean` prompts unless `--yes` is provided. `clean --all` also runs
`docker compose down -v`.

- [ ] **Step 4: Run tests and syntax check**

Run:

```bash
bash -n ops.sh
bash scripts/tests/test_ops.sh
```

Expected: PASS.

### Task 4: Update New User Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/deployment.md`

- [ ] **Step 1: Write a failing documentation assertion**

Extend `scripts/tests/test_ops.sh` to assert that README contains:

```text
./ops.sh bootstrap
./ops.sh status
./ops.sh stop
```

and that `docs/deployment.md` references `./ops.sh help` instead of embedding the
outdated `scripts/start-all.sh` example.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
bash scripts/tests/test_ops.sh
```

Expected: FAIL because onboarding still documents manual setup as the primary path.

- [ ] **Step 3: Update documentation**

Replace the README quick-start happy path with:

```bash
cp .env.example .env
# Edit optional DeepSeek and Feishu credentials
./ops.sh bootstrap
./ops.sh status
```

Add a daily command reference and link to `docs/deployment.md`. Replace the
deployment appendix inline script with the `./ops.sh` command table. Keep manual
deployment sections for troubleshooting.

- [ ] **Step 4: Run tests and documentation checks**

Run:

```bash
bash scripts/tests/test_ops.sh
git diff --check
```

Expected: PASS.

### Task 5: Verify Against The Real Local Environment

**Files:**
- Verify only

- [ ] **Step 1: Run shell and Python regression tests**

Run:

```bash
bash -n ops.sh
bash scripts/tests/test_ops.sh
.venv/bin/python -m unittest \
  tests/test_alert_workflow.py \
  tests/test_local_http_clients.py \
  tests/test_prometheus_tool.py \
  tests/test_templates.py \
  tests/test_feishu.py -v
git diff --check
```

Expected: PASS.

- [ ] **Step 2: Transition existing unmanaged local processes**

Stop the previously created local `launchctl` jobs and Loki port-forward so that
`ops.sh` can take ownership through `.ops/pids/`.

- [ ] **Step 3: Exercise real daily commands**

Run:

```bash
./ops.sh status
./ops.sh restart
./ops.sh status
```

Expected: component endpoints are healthy and managed process PID files exist.

- [ ] **Step 4: Run real E2E**

Run:

```bash
./ops.sh test
```

Expected: the CPU alert creates a diagnosed incident and the cleanup trap resets
the injected fault.

- [ ] **Step 5: Verify cleanup state**

Run:

```bash
curl -fsS http://localhost:8081/fault/status
curl -fsSG http://localhost:9090/api/v1/query \
  --data-urlencode 'query=ALERTS{alertname="HighCPUUsage"}'
```

Expected: fault switches are reset and `HighCPUUsage` clears after the scrape
interval.
