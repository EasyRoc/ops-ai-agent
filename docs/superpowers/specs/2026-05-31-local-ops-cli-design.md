# Local Ops CLI Design

## Goal

Provide one cross-platform shell entrypoint for new contributors and daily local
development:

```bash
./ops.sh bootstrap
```

The first-run command prepares the complete local environment: configuration,
Python virtual environment, Docker Compose data services, Kind cluster,
observability stack, demo services, background port-forwards, Kubernetes proxy,
and the Agent.

The script supports macOS and Linux. It detects missing system dependencies and
prints platform-appropriate installation guidance, but does not install system
packages or invoke `sudo`.

## Entry Point And Runtime Files

Add one repository-root script:

```text
ops.sh
```

Store generated runtime state under an ignored directory:

```text
.ops/
├── pids/
└── logs/
```

Each managed background process receives one PID file and one log file:

| Component | PID name | Port |
|---|---|---:|
| Agent | `agent` | 8000 |
| Kubernetes API proxy | `kubectl-proxy` | 8001 |
| Prometheus | `prometheus` | 9090 |
| Alertmanager | `alertmanager` | 9093 |
| Loki | `loki` | 3100 |
| Grafana | `grafana` | 30030 |
| Order demo service | `order-service` | 8081 |

PID files allow the script to stop only processes it owns. It must not use broad
`pkill` patterns.

## Commands

```bash
./ops.sh bootstrap      # First clone: prepare and start the full environment
./ops.sh start          # Start an existing environment without rebuilding demo images
./ops.sh restart        # Restart managed background processes
./ops.sh stop           # Stop managed processes and Docker Compose services
./ops.sh status         # Show local component health and configuration warnings
./ops.sh logs [name]    # Tail one managed process log; defaults to agent
./ops.sh demo start     # Build, load, and deploy demo services
./ops.sh demo stop      # Delete the demo namespace
./ops.sh demo restart   # Rebuild and redeploy demo services
./ops.sh test           # Run the real CPU fault injection E2E test
./ops.sh clean          # Stop services and delete the Kind cluster
./ops.sh clean --all    # Also delete Docker Compose volumes
./ops.sh help
```

`clean` and `clean --all` must print the resources they will delete and ask for
confirmation unless an explicit non-interactive confirmation option is passed.

## Bootstrap Flow

`bootstrap` performs these steps in order:

1. Detect macOS or Linux and validate required commands:
   `docker`, `kubectl`, `kind`, `helm`, `mvn`, `python3`, and `curl`.
2. Verify that the Docker daemon is reachable.
3. Copy `.env.example` to `.env` when `.env` is absent.
4. Create `.venv` when absent and install `requirements.txt`.
5. Start PostgreSQL and Redis with `docker compose up -d`.
6. Wait until PostgreSQL and Redis report healthy.
7. Create the `ops-agent` Kind cluster when it does not exist.
8. Add or update Helm repositories.
9. Deploy or update Prometheus, Alertmanager, Grafana, Loki, and Promtail.
10. Wait for the monitoring workloads to become ready.
11. Build the Java demo JARs and Docker images.
12. Load each demo image into Kind.
13. Apply the demo namespace, wait until it is Active, apply the remaining
    manifests, and wait for deployments.
14. Start managed port-forwards and the Kubernetes API proxy.
15. Start the Agent from the repository root.
16. Print status, access URLs, and actionable configuration warnings.

DeepSeek configuration is optional for infrastructure startup. When the API key
is absent or remains a placeholder, the script prints a warning that
LLM-assisted diagnosis may be unavailable.

Feishu application credentials are optional. When `agent/tools/cmdb.py` still
contains `oc_chat_*` placeholders, the script warns that cards cannot be
delivered until real group `chat_id` values are configured.

## Daily Commands

`start` validates dependencies, starts Docker Compose services, verifies that
the existing Kind cluster and monitoring stack are present, starts managed
background processes, and starts the Agent. It does not rebuild Java artifacts
or redeploy monitoring components.

`restart` stops and restarts managed background processes only. It does not
restart Docker Compose, rebuild demo images, or recreate the Kind cluster.

`stop` stops managed background processes and runs `docker compose down`. It
preserves the Kind cluster, Kubernetes workloads, local Docker images, and
PostgreSQL volume.

`demo start` packages the Java modules, builds four images, loads them into the
Kind cluster, and applies the Kubernetes manifests. `demo restart` deletes the
demo namespace before invoking the same flow. `demo stop` deletes only the demo
namespace.

## Idempotency And Error Handling

- Helm deployments use `helm upgrade --install`.
- Kubernetes deployments use `kubectl apply`.
- Kind cluster creation runs only when the expected context is absent.
- A managed process is considered running only when its PID file exists and
  the recorded process is alive.
- Before launching a managed process, the script rejects a port already held by
  an unmanaged process and prints a diagnostic command.
- Stale PID files are removed automatically.
- Readiness waits have explicit timeouts and actionable failure messages.
- Missing commands print macOS Homebrew guidance or common Linux package
  manager guidance. The script exits before mutating project state.
- The script resolves the repository root from its own path and works when
  invoked from another directory.

## Status And Logs

`status` prints grouped information:

1. Docker daemon and Docker Compose services.
2. Kind context and node readiness.
3. Monitoring endpoints: Prometheus, Alertmanager, Loki, and Grafana.
4. Demo deployment readiness and Prometheus target count.
5. Managed background PID state.
6. Agent health.
7. DeepSeek and Feishu configuration warnings.

`logs [name]` accepts:

```text
agent
kubectl-proxy
prometheus
alertmanager
loki
grafana
order-service
```

The default is `agent`. Unknown names produce a usage error.

## Documentation

Update `README.md` so the primary onboarding flow is:

```bash
cp .env.example .env
# Add optional API credentials
./ops.sh bootstrap
```

Update `docs/deployment.md` to use the script for the happy path while keeping
the manual commands as a troubleshooting reference.

## Verification

Add `scripts/tests/test_ops.sh`. Tests use temporary directories and stubbed
commands to verify:

1. Help output and unknown command handling.
2. `.env` initialization.
3. PID lifecycle helpers, including stale PID cleanup.
4. Rejection of a port owned by an unmanaged process.
5. Command dispatch for `bootstrap`, `start`, `restart`, `stop`, `status`,
   `logs`, `demo`, `test`, and `clean`.
6. Dependency error messages.

Run:

```bash
bash -n ops.sh
bash scripts/tests/test_ops.sh
./ops.sh status
./ops.sh restart
./ops.sh test
```

The final real-environment verification must confirm that the injected CPU
fault resets, the `HighCPUUsage` alert clears, and the Agent stores a diagnosed
incident.
