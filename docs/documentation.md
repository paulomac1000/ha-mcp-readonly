---
description: Operator and architecture reference for HA-MCP-Readonly.
doc_id: reference.ha-mcp-operator-architecture
type: reference
status: active
rigor: operational
owners: [repository-maintainers]
verification: Run the exact commands in the Verification section; public CI additionally requires the exact-head AI Skills policy, Pre-commit gate, Semgrep Security Scan, Official MCP client, CI, and Migration evidence workflows.
---

# Operator and architecture reference

## Supported architecture

HA-MCP-Readonly exposes a reviewed catalog of observational Home Assistant operations. All transports use one operation catalog and one invocation policy.

```text
FastMCP stdio or authenticated Streamable HTTP
optional authenticated REST compatibility adapter
                    |
             input validation
                    |
         principal and capability check
                    |
 explicit manifest, deadline, concurrency, response limit
                    |
        read-only domain operation and adapter
                    |
          sanitized structured response
```

`HA_TOKEN` authenticates outbound requests to Home Assistant. It is not caller authentication. Network callers authenticate with `MCP_AUTH_TOKEN`; the optional REST adapter uses `REST_API_TOKEN` or the MCP token.

## Transports

### Local stdio

This is the default and recommended local integration:

```bash
export HA_URL=http://homeassistant.local:8123
export HA_TOKEN='replace-with-a-long-lived-token'
export HA_CONFIG_PATH=/path/to/home-assistant/config
ha-mcp-readonly
```

A source installation does not start REST in stdio mode. The distributed container enables its loopback health server so the image health check remains valid while MCP still uses stdio.

### Streamable HTTP

```bash
export MCP_TRANSPORT=http
export MCP_BIND_HOST=127.0.0.1
export MCP_PORT=9092
export MCP_AUTH_TOKEN='replace-with-a-high-entropy-caller-token'
export HEALTH_SERVER_ENABLED=1
ha-mcp-readonly
```

The MCP endpoint is `/mcp`. Non-loopback binding without an MCP token is rejected. Legacy two-endpoint SSE has been removed; `MCP_TRANSPORT=sse` fails configuration validation.

### REST compatibility adapter

REST is not the primary MCP interface. Enable it only for an authenticated compatibility client:

```bash
export REST_API_ENABLED=1
export REST_API_TOKEN='replace-with-a-dedicated-token'
export REST_API_PORT=9093
```

The standalone health listener exposes public `/live`, `/ready`, and `/health` probes. The REST adapter exposes public `/health` and `/api/health`; all other REST routes, including `/api/health/details`, require `Authorization: Bearer ...`. REST tool invocation uses the same wrapped operation functions as MCP and therefore receives the same manifest, capability, deadline, concurrency, response-size, sanitization, and observability behavior.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `HA_URL` | `http://homeassistant:8123` | Home Assistant base URL |
| `HA_TOKEN` | empty | Outbound Home Assistant credential |
| `HA_CONFIG_PATH` | `/config` | Read-only Home Assistant configuration root |
| `MCP_TRANSPORT` | `stdio` | `stdio` or authenticated Streamable HTTP (`http`) |
| `MCP_BIND_HOST` | `127.0.0.1` | Network bind address |
| `MCP_PORT` | `9092` | Streamable HTTP port |
| `MCP_AUTH_TOKEN` | empty | Required caller credential for network MCP |
| `REST_API_ENABLED` | `0` | Enable the compatibility adapter |
| `REST_API_TOKEN` | MCP token | REST caller credential |
| `REST_API_PORT` | `9093` | REST compatibility adapter port |
| `CORS_ALLOWED_ORIGINS` | `http://localhost` | Explicit REST origins; wildcards are rejected |
| `HEALTH_SERVER_ENABLED` | network-dependent | Start `/live`, `/ready`, and `/health` |
| `HEALTH_CHECK_PORT` | `9091` | Health listener port |
| `HA_BACKEND_REQUIRED_FOR_READY` | `0` | Require live Home Assistant connectivity for `/ready`; otherwise expose HA outage as capability degradation |
| `OUTPUT_PATH` | `/app/output/ha-ai-context.md` | Default context artifact path |
| `CONTEXT_OUTPUT_ROOT` | output parent | Artifact containment root |
| `HA_CONTEXT_HISTORY_HOURS` | `1` | Bounded history window included in context |
| `HA_CONTEXT_LOG_HOURS` | `24` | Bounded logbook and log-analysis window |
| `HA_CONTEXT_CALENDAR_DAYS` | `30` | Calendar window before and after generation time |
| `HA_CONTEXT_MAX_SOURCE_BYTES` | `67108864` | Total safe-source payload budget |
| `HA_CONTEXT_MAX_OUTPUT_BYTES` | `100663296` | Final context artifact size limit |
| `MCP_DEV_TOOLS_ENABLED` | `0` | Enable additional developer-only observations |
| `LOG_LEVEL` | `INFO` | Runtime logging level |
| `RUN_TESTS_ON_STARTUP` | `0` | Run bundled unit tests before serving when tests are installed |

Wildcard CORS origins are rejected. Developer tools are disabled by default.

## Filesystem boundary

Generic `list_directory`, `read_file`, and `search_files` operations:

- resolve paths below explicit roots using path semantics, not string prefixes;
- reject symlink components;
- block `.storage`, `secrets.yaml`, `.env`, authentication records, and credential stores;
- read only allowlisted text suffixes under configured size and depth limits;
- walk directories without following links.

Dedicated registry readers are separate adapters. They may parse selected Home Assistant storage records but must expose only reviewed fields.

## Context artifacts

Context generation is single-worker, owner-bound, process-isolated, and bounded by a deadline. Requested output paths must remain below `CONTEXT_OUTPUT_ROOT` and use `.md` or `.json`. Generation writes to a temporary file in the destination directory and publishes by atomic replacement. A timed-out worker is terminated before a new task can start.

`offline` uses an immutable per-run configuration and has no network client. `online` requires configured Home Assistant network access. `hybrid` combines local and network sources and reports partial results when optional sources are unavailable.

The report contains a provenance matrix and a comprehensive redacted snapshot. It covers every supported safe source exposed by the local configuration, public REST API, and authenticated WebSocket API, including dynamically discovered calendar events, to-do items, and advertised weather forecasts. Each unavailable, partial, policy-excluded, or size-limited source is recorded explicitly. Credential stores, secrets files, database files, binary media, streams, and backup contents are excluded. Generated context is sensitive operational data and requires retention and access controls.

## Health

- `/live` reports process liveness.
- `/ready` reports the combined catalog, transport, filesystem, backend-configuration, and optional REST component state.
- `/health` returns only status and version and deliberately exposes no component detail.
- Authenticated `/api/health/details` reports component state, readiness, and tool counts for operators.

Backend configuration may be reported as degraded when no Home Assistant token is configured. Readiness does not claim that every integration endpoint is reachable; individual backend failures remain controlled tool errors and are visible in context provenance.

## Packaging and container

The package includes `server.py`, `version.py`, `tools`, `context_generator`, `ha_graph`, and the manifest catalog. CI builds one wheel, installs it in a clean environment, verifies a real stdio subprocess, and tests release containers on amd64 and arm64. Release validation builds the multi-platform candidate once into an isolated quarantine registry, records its exact manifest digest, and smoke-tests that digest on every published platform. The protected publisher performs no candidate checkout, build, load, or execution; it promotes only the exact tested digest with `buildx imagetools create`.

The image runs as UID 10001, drops root privileges, and defaults to stdio with network adapters disabled. Its loopback health server remains enabled so the static image health check is meaningful. The provided Compose configuration binds ports to loopback, mounts Home Assistant configuration read-only, provides a bounded writable context-output tmpfs, drops Linux capabilities, and requires an MCP caller token.

## Verification

Run the deterministic source gates from an isolated Python environment:

```bash
python -m pip install -c constraints-ci.txt '.[dev]'
python -m pip install 'pre-commit==4.3.0'
ruff check .
ruff format --check .
mypy server.py tools/ context_generator/core.py context_generator/config.py context_generator/runtime.py context_generator/provenance.py context_generator/snapshot.py scripts/verify_runtime_endpoints.py --strict
bandit -r server.py tools/ context_generator/ ha_graph/ -ll
pre-commit run --all-files
make docs-check
pytest tests/unit -q
pytest tests/protocol -q
python -m build --wheel --no-isolation
```

The exact-head GitHub verification additionally requires these workflows to succeed for the same commit: `AI Skills policy`, `Pre-commit gate`, `Semgrep Security Scan`, `Official MCP client`, `CI`, and `Migration evidence`. `CI` installs the produced wheel in a clean environment, exercises a real stdio subprocess, and verifies the release container on `linux/amd64` and `linux/arm64`.

Backend-dependent verification is:

```bash
HA_URL=http://your-isolated-ha:8123 HA_TOKEN=your_test_token \
  pytest tests/smoke tests/e2e tests/integration -q
```

When an isolated Home Assistant and real `HA_URL`/`HA_TOKEN` are unavailable, that backend-dependent command is **not** reported as passing. Public migration evidence records it as skipped with the reason, and provider-backed migration approval remains blocked until equivalent exact-revision evidence exists.

## Troubleshooting

- If `/ready` is not ready, inspect authenticated `/api/health/details` when the REST adapter is enabled and verify the reported component state.
- If remote MCP or REST returns `401`, configure the caller bearer token separately from the outbound Home Assistant `HA_TOKEN`.
- If filesystem tools return `ACCESS_DENIED`, verify that the requested path is below `HA_CONFIG_PATH`, contains no symlink or traversal component, and is not a blocked credential-bearing path.
- If context generation is partial, inspect the provenance matrix for permission, integration, size-limit, or unsupported-source reasons rather than treating an omitted source as successful collection.
