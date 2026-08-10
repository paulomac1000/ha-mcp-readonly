# HA-MCP-Readonly

[![CI](https://github.com/paulomac1000/ha-mcp-readonly/actions/workflows/ci.yml/badge.svg)](https://github.com/paulomac1000/ha-mcp-readonly/actions/workflows/ci.yml)
[![Docker](https://github.com/paulomac1000/ha-mcp-readonly/actions/workflows/publish.yml/badge.svg)](https://github.com/paulomac1000/ha-mcp-readonly/actions/workflows/publish.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Read-only MCP (Model Context Protocol) server for Home Assistant. Gives AI assistants (Claude Desktop, LibreChat, Cline) full observability into your smart home — entity states, automations, scripts, devices, logs, diagnostics — without any write access. Also generates static AI context snapshots for RAG systems, ChatGPT Projects, Qwen, and other tools that accept custom knowledge files. Built in Python, runs anywhere — locally, in Docker, or as an MCP integration.

## Requirements

- Python 3.11+ (for local use) or Docker
- A Home Assistant instance with a [long-lived access token](https://www.home-assistant.io/docs/configuration/secrets/#long-lived-access-token)
  - Create one in your HA profile: **Settings → Security → Long-Lived Access Tokens**
- Access to your Home Assistant config directory (for filesystem tools)

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```
HA_URL=http://your-ha-ip:8123
HA_TOKEN=your_long_lived_access_token_here
# HA_CONFIG_PATH=/config                # optional, default shown
# MCP_DEV_TOOLS_ENABLED=1               # optional, default shown
# HEALTH_CHECK_PORT=9091             # optional, default shown
# MCP_PORT=9092                       # Streamable HTTP port when enabled
# REST_API_PORT=9093                 # optional, default shown
# RUN_TESTS_ON_STARTUP=0             # optional, default shown
# OUTPUT_PATH=/app/output/ha-ai-context.md  # optional, default shown
```

**IMPORTANT:** The `.env` file contains your access token. It is gitignored and must never be committed.

### 2. Run with Docker

First, configure your credentials. Either use a `.env` file (recommended) or pass variables directly.

**Option A — with `.env` file and docker compose:**

```bash
cp .env.example .env
# edit .env with your HA_URL and HA_TOKEN
docker compose up -d
```

The included `docker-compose.yml` pulls the image from GitHub Container Registry and mounts your HA config read-only:

```yaml
services:
  ha-mcp-readonly:
    image: ghcr.io/paulomac1000/ha-mcp-readonly:latest
    container_name: ha-mcp-readonly
    env_file: .env
    environment:
      MCP_TRANSPORT: http
      MCP_BIND_HOST: 0.0.0.0
      MCP_AUTH_TOKEN: ${MCP_AUTH_TOKEN:?Set a strong MCP_AUTH_TOKEN}
    ports:
      - "127.0.0.1:9091:9091"  # health
      - "127.0.0.1:9092:9092"  # authenticated Streamable HTTP MCP
    volumes:
      - /path/to/ha/config:/config:ro  # Replace with your HA config path (e.g., /config, ~/.homeassistant)
    tmpfs:
      - /app/output:size=256m,mode=0750,uid=10001,gid=10001
    restart: unless-stopped
    read_only: true
    cap_drop: ["ALL"]
    security_opt: ["no-new-privileges:true"]
```

**Option B — with plain `docker run`:**

```bash
docker run -d \
  --name ha-mcp-readonly \
  -p 127.0.0.1:9091:9091 \
  -p 127.0.0.1:9092:9092 \
  -e HA_URL=http://your-ha-ip:8123 \
  -e HA_TOKEN=your_token \
  -e MCP_TRANSPORT=http \
  -e MCP_BIND_HOST=0.0.0.0 \
  -e MCP_AUTH_TOKEN=replace-with-a-high-entropy-caller-token \
  -v /path/to/ha/config:/config:ro \
  ghcr.io/paulomac1000/ha-mcp-readonly:latest
```

**Building locally:**

```bash
docker build -t ha-mcp-readonly .
docker compose -f docker-compose.build.yml up -d
```

### 3. Run locally (Python 3.11+)

```bash
pip install -r requirements.txt
HA_URL=http://localhost:8123 HA_TOKEN=your_token python server.py
```

## Ports

| Port | Protocol | Purpose | Endpoint |
|------|----------|---------|----------|
| 9091 | HTTP | Health check | `GET /health` |
| 9092 | HTTP | Authenticated Streamable HTTP MCP | `/mcp` |
| 9093 | HTTP | Optional authenticated REST API + Context Generator | `/api/*` |

### Verify

```bash
# Health check
curl http://localhost:9091/health

# Readiness
curl http://localhost:9091/ready

# MCP uses an official Streamable HTTP client at http://127.0.0.1:9092/mcp.
# REST/context routes on 9093 exist only when REST_API_ENABLED=1 and require a bearer token.
```

## Available Tools (158 with dev tools, 145 without)

Tools are organized by category (75 shown in table below). All are **read-only** — no state changes, no service calls, no modifications.

| Category | Key tools |
|----------|-----------|
| **States** | `get_entity_state`, `get_states_grouped`, `search_entities`, `get_domains_summary`, `get_system_overview` |
| **Automations** | `list_automations`, `get_automation_code`, `get_automation_file_location`, `diagnose_automation`, `search_automations_by_entity`, `get_automation_conflicts`, `get_automation_entity_id` |
| **Scripts & Scenes** | `list_scripts`, `get_script_code`, `list_scenes`, `get_scene_code` |
| **Blueprints** | `list_blueprints`, `get_blueprint_code`, `get_blueprint_instances`, `get_blueprint_usage_summary`, `resolve_blueprint_automation` |
| **Devices & Areas** | `get_device_details`, `search_devices`, `get_devices_by_area`, `get_area_devices_summary` |
| **Config entries** | `get_config_entry_details`, `search_config_entries`, `diagnose_config_entry`, `list_config_entry_domains` |
| **Integrations** | `get_integration_entities`, `get_integration_summary` |
| **Diagnostics** | `diagnose_system_health`, `get_unavailable_entities_grouped`, `get_integration_health`, `diagnose_person_tracking` |
| **Logs** | `get_log_insights`, `analyze_log_errors`, `get_startup_errors`, `get_log_timeline`, `search_logs` |
| **History** | `get_entity_state_history_summary`, `get_recent_state_changes` |
| **Context** | `entity_get_context_tree`, `get_entity_dependencies`, `get_entity_consumers`, `get_context_chain` |
| **Config** | `get_main_configuration`, `search_in_config`, `validate_yaml_syntax`, `read_config_file` |
| **Storage** | `search_registries_batch`, `get_entity_registry`, `get_device_registry`, `get_area_registry`, `get_template_entity_code`, `get_cache_stats` |
| **Lovelace** | `get_lovelace_dashboards`, `get_lovelace_config`, `get_lovelace_resources`, `search_lovelace_config`, `get_lovelace_config_summary`, `diagnose_lovelace_setup` |
| **Batch** | `bulk_search_entities`, `compare_entities_state`, `validate_yaml_batch`, `get_automation_codes_batch` |
| **Composite** | `investigate_entity`, `get_area_diagnostic`, `get_entity_with_automations`, `audit_config_orphans` |
| **Graph** | `graph_build_index`, `graph_find_references`, `graph_entity_impact`, `graph_get_neighbors`, `graph_detect_ghost_references`, `graph_detect_orphans`, `graph_export_mermaid` |
| **Dev tools** | `test_template`, `compare_templates`, `diagnose_entity`, `check_entity_exists`, `validate_automation_trigger`, `diagnose_template` |

> Full tool catalog with schemas available at GET /api/tools

## What's New in v1.6.0

- **5 new tools**: `get_context_chain` (Context), `resolve_blueprint_automation` (Blueprints), `get_cache_stats` (Storage), `compare_templates` (Dev tools), `get_automation_entity_id` (Automations)
- **`choose_analysis` in `diagnose_automation`**: When `detail_level="full"`, returns conditional branch analysis for automations using `choose` actions
- **Registry pagination**: `limit` and `offset` parameters added to `get_entity_registry`, `get_device_registry`, `get_area_registry`, and `get_config_entries` for efficient scanning of large registries
- **`data_quality` field**: Composite diagnostic tools (`investigate_entity`, `get_area_diagnostic`, `get_entity_with_automations`) now include a `data_quality` assessment flagging stale sensors, missing entities, and unavailable devices
- **New pre-commit hooks**: `mypy strict`, `Bandit`, `Semgrep`, and AFDS documentation validation added to the pre-commit pipeline
- **Test infrastructure**: 272 integration tests, 174 E2E tool-smoke cases, and 86 smoke tests for expanded real-HA and end-to-end coverage

## Client configuration

### Local stdio

Install the wheel and configure the client to start the server as a subprocess. This is the default transport and does not expose an MCP network port.

```json
{
  "mcpServers": {
    "ha-mcp-readonly": {
      "command": "ha-mcp-readonly",
      "env": {
        "HA_URL": "http://homeassistant.local:8123",
        "HA_TOKEN": "replace-with-a-long-lived-access-token",
        "HA_CONFIG_PATH": "/path/to/home-assistant/config"
      }
    }
  }
}
```

### Authenticated Streamable HTTP

Set `MCP_TRANSPORT=http`, `MCP_AUTH_TOKEN`, and a controlled bind address. The endpoint is `/mcp`. Legacy `/sse` support has been removed and `MCP_TRANSPORT=sse` is rejected.

```json
{
  "mcpServers": {
    "ha-mcp-readonly": {
      "url": "http://127.0.0.1:9092/mcp",
      "headers": {
        "Authorization": "Bearer replace-with-a-high-entropy-caller-token"
      }
    }
  }
}
```

The default catalog contains 145 read-only tools. Developer-only tools remain disabled unless `MCP_DEV_TOOLS_ENABLED=1` is set.

## Context Generator

The context generator creates a bounded Markdown snapshot for offline analysis, retrieval systems, AI project knowledge, audits, and troubleshooting.

| Mode | Data access |
|------|-------------|
| `offline` | Local Home Assistant configuration and safe storage records only. Network access is disabled by construction. |
| `online` | Home Assistant REST and WebSocket APIs. Missing required network access fails the run. |
| `hybrid` | Local sources plus every supported API source available to the configured token. |

The artifact includes the normal analysis sections and a **Source Provenance and Completeness** matrix. Every attempted source records its method, status, record count, byte count, redaction count, requested window, and failure or omission reason. The **Comprehensive Safe Data Snapshot** includes all supported, accessible data within configured bounds:

- states, services, components, events, configuration, history, logbook, error log, calendars, and calendar events;
- entity, device, area, floor, label, category, configuration-entry, energy, panel, Lovelace-resource, repair, system-health, and Assist-pipeline data exposed by Home Assistant;
- to-do items and every advertised weather forecast type discovered dynamically from entity states;
- all discoverable safe `.storage` records plus YAML, JSON, and file inventory data under the configured root;
- automation, script, scene, blueprint, template, helper, person, zone, energy, HACS, cache, dependency, dashboard, and diagnostic analysis.

Credential stores are excluded. Sensitive fields, bearer tokens, JWTs, secret query parameters, and `!secret` values are redacted. Binary media, camera streams, backup contents, databases, and credential-bearing records are not copied. Sources unavailable because of permissions, missing integrations, unsupported commands, configured windows, or size limits remain visible in provenance rather than being silently omitted.

Relevant limits are `HA_CONTEXT_HISTORY_HOURS`, `HA_CONTEXT_LOG_HOURS`, `HA_CONTEXT_CALENDAR_DAYS`, `HA_CONTEXT_MAX_SOURCE_BYTES`, and `HA_CONTEXT_MAX_OUTPUT_BYTES`.

```bash
curl -X POST http://127.0.0.1:9093/api/context/generate \
  -H "Authorization: Bearer $REST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"hybrid"}'

curl -H "Authorization: Bearer $REST_API_TOKEN" \
  http://127.0.0.1:9093/api/context/status

curl -H "Authorization: Bearer $REST_API_TOKEN" \
  http://127.0.0.1:9093/api/context/download > ha-ai-context.md
```

## REST API

The optional REST compatibility adapter is disabled by default. When enabled, every route except health requires a bearer token and uses the same manifest, capability, deadline, concurrency, response-size, and error policy as MCP.

```bash
curl -H "Authorization: Bearer $REST_API_TOKEN" \
  'http://127.0.0.1:9093/api/tools?detail=full'

curl -X POST http://127.0.0.1:9093/api/tools/get_entity_state \
  -H "Authorization: Bearer $REST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id":"sun.sun"}'

curl -H "Authorization: Bearer $REST_API_TOKEN" \
  http://127.0.0.1:9093/api/openapi.json
```

## Development

### Setup

```bash
git clone https://github.com/paulomac1000/ha-mcp-readonly.git
cd ha-mcp-readonly
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Run tests

```bash
# Deterministic local gates
pytest tests/unit/ -q
pytest tests/protocol/ -q

# Backend-dependent suites; require an isolated Home Assistant and credentials
export HA_URL=http://your-ha:8123
export HA_TOKEN=your_token
pytest tests/smoke/ tests/integration/ tests/e2e/ -q
```

Backend-dependent tests report skips when the required Home Assistant environment is absent. CI installs the wheel in a clean environment, executes a real stdio subprocess, and verifies health, REST metadata, the offline context lifecycle, and authenticated Streamable HTTP against built release containers. The release workflow separately builds one multi-platform candidate into quarantine, smoke-tests its exact digest on amd64 and arm64, and promotes only that digest from the protected publisher.

### Lint & format

```bash
ruff check .
ruff format --check .
```

## Architecture

```
server.py                  # Main entry point — FastMCP + REST API + health check
context_generator/
├── config.py              # Immutable per-run configuration
├── constants.py           # Legacy/static analyzer defaults and HA YAML loader
├── runtime.py             # Context-local runtime and provenance scope
├── provenance.py          # Completeness matrix and redaction
├── snapshot.py            # Safe filesystem, REST, and WebSocket collectors
├── storage_policy.py      # Positive allowlist for model-visible .storage data
├── core.py                # Isolated generation entry points
├── analyzers.py           # Domain analyzers
├── formatters.py          # Atomic bounded Markdown output
└── utils.py               # Runtime-aware registry and API adapters

ha_graph/
└── graph_builder.py       # HA Semantic Graph: build, query, and export

tools/
├── automations.py         # Automation analysis (17 tools)
├── batch_operations.py    # Bulk entity operations (5 tools)
├── blueprints.py          # Blueprint management (4 tools)
├── capabilities.py        # Zero-I/O MCP introspection tool catalog (1 tool)
├── categories.py          # Category management (automation, script, scene, helpers) (1 tool)
├── composite.py           # Composite diagnostic tools (4 tools)
├── config.py              # Configuration file tools (10 tools)
├── config_entries.py      # Config entry diagnostics (4 tools)
├── devices.py, areas.py   # Device and area tools (6+1 tools)
├── dev_tools.py           # Template testing, validation (13 tools)
├── diagnostics.py         # System health, energy dashboard (18 tools)
├── entity_context.py      # Entity context tree (2 tools)
├── entity_dependencies.py # Entity dependency graph (2 tools)
├── filesystem_explorer.py # Secured filesystem browsing (3 tools)
├── graph_tools.py           # HA entity graph tools (7 tools)
├── health_reporter.py     # Health score and metrics (1 tool)
├── helpers_health.py      # Helper entity health diagnostics (1 tool)
├── history.py             # State history and recent changes (2 tools)
├── integrations.py        # Integration entity analysis (2 tools)
├── logs.py                # Log analysis and insights (8 tools)
├── manifests.py           # TOOL_MANIFESTS, risk prefix injection
├── observability.py       # request_id, invocation counters
├── scripts.py, scenes.py  # Script and scene inspection (2+2 tools)
├── states.py              # Entity state queries (12 tools)
├── storage.py             # Registry dump and search tools (30 tools)
├── utils.py               # Shared: HA API client, registry loader, log sanitizer
└── yaml_utils.py          # HomeAssistantLoader for HA-specific YAML tags

tests/
├── unit/                  # 39 test files, 1181 tests, fully mocked
├── integration/           # Real HA tests (requires HA_URL + HA_TOKEN)
├── smoke/                 # REST API smoke tests (requires local server)
└── e2e/                   # End-to-end pipeline tests (requires real HA)
```

## Security

- **Read-only by design** — no write operations to Home Assistant. Cannot modify states, execute services, or trigger automations.
- **Filesystem restrictions** — access limited to `/config` directory. Path traversal (`..`, `~`) blocked. Max file size 10MB. Max directory depth 20.
- **Auth data blocked** — `auth`, `auth_provider.*`, `onboarding` registries are never returned.
- **Credential redaction** — `HA_TOKEN` is never logged or exposed in outputs. JWTs, passwords, API keys, and IP addresses are sanitized from log output.

## Notes

- The server may expose 9091 (health), 9092 (authenticated Streamable HTTP MCP), and 9093 (optional authenticated REST/context adapter). Stdio remains the default MCP transport.
- `MCP_DEV_TOOLS_ENABLED=0` disables template execution and debugging tools for production use.
- **Security note**: Ports 9091-9093 should not be exposed publicly. Use firewall rules or reverse proxy with authentication if needed.
- Registry files (areas, devices, entities, config entries) are cached for 5 minutes to reduce filesystem I/O.
- All tool responses return JSON with a `success` field — always check this before reading `data`.

## Troubleshooting

For common issues and solutions, see [docs/documentation.md#troubleshooting](docs/documentation.md#troubleshooting).

## License

MIT — see [LICENSE](LICENSE) for details.
