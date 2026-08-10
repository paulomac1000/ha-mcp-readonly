---
description: Operator and architecture reference for HA-MCP-Readonly.
doc_id: reference.ha-mcp-operator-architecture
type: reference
status: active
rigor: operational
owner: [repository-maintainers]
verification: Run `pytest tests/unit tests/protocol -q`, build and inspect the wheel, and execute the container job in `.github/workflows/ci.yml`.
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

Every route other than health requires `Authorization: Bearer ...`. REST invokes the same wrapped operation functions as MCP and therefore receives the same manifest, capability, deadline, concurrency, response-size, sanitization, and observability behavior.

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
| `HEALTH_SERVER_ENABLED` | network-dependent | Start `/live`, `/ready`, and `/health` |
| `HA_BACKEND_REQUIRED_FOR_READY` | `0` | Require live Home Assistant connectivity for `/ready`; otherwise expose HA outage as capability degradation |
| `CONTEXT_OUTPUT_ROOT` | output parent | Artifact containment root |
| `HA_CONTEXT_HISTORY_HOURS` | `1` | Bounded history window included in context |
| `HA_CONTEXT_LOG_HOURS` | `24` | Bounded logbook and log-analysis window |
| `HA_CONTEXT_CALENDAR_DAYS` | `30` | Calendar window before and after generation time |
| `HA_CONTEXT_MAX_SOURCE_BYTES` | `67108864` | Total safe-source payload budget |
| `HA_CONTEXT_MAX_OUTPUT_BYTES` | `100663296` | Final context artifact size limit |
| `MCP_DEV_TOOLS_ENABLED` | `0` | Enable additional developer-only observations |

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
- `/health` includes version, component details, readiness, and tool count; it deliberately omits per-tool invocation counters from the public endpoint.

Backend configuration may be reported as degraded when no Home Assistant token is configured. Readiness does not claim that every integration endpoint is reachable; individual backend failures remain controlled tool errors and are visible in context provenance.

## Packaging and container

The package includes `server.py`, `version.py`, `tools`, `context_generator`, `ha_graph`, and the manifest catalog. CI builds one wheel, installs it in a clean environment, verifies a real stdio subprocess, and tests release containers on amd64 and arm64. Release validation builds the multi-platform candidate once into an isolated quarantine registry, records its exact manifest digest, and smoke-tests that digest on every published platform. The protected publisher performs no candidate checkout, build, load, or execution; it promotes only the exact tested digest with `buildx imagetools create`.

The image runs as UID 10001, drops root privileges, and defaults to stdio with network adapters disabled. Its loopback health server remains enabled so the static image health check is meaningful. The provided Compose configuration binds ports to loopback, mounts Home Assistant configuration read-only, provides a bounded writable context-output tmpfs, drops Linux capabilities, and requires an MCP caller token.

## Verification

```bash
python -m pip install -c constraints-ci.txt '.[dev]'
ruff check .
ruff format --check .
mypy server.py tools/ context_generator/core.py context_generator/config.py context_generator/runtime.py context_generator/provenance.py context_generator/snapshot.py scripts/verify_runtime_endpoints.py --strict
bandit -r server.py tools/ context_generator/ ha_graph/ -ll
pytest tests/unit tests/protocol -q
python -m build --wheel --no-isolation
```
