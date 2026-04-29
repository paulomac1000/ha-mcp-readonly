# HA-MCP-Readonly

[![CI](https://github.com/paulomac1000/ha-mcp-readonly/actions/workflows/ci.yml/badge.svg)](https://github.com/paulomac1000/ha-mcp-readonly/actions/workflows/ci.yml)
[![Docker](https://github.com/paulomac1000/ha-mcp-readonly/actions/workflows/publish.yml/badge.svg)](https://github.com/paulomac1000/ha-mcp-readonly/actions/workflows/publish.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Read-only MCP (Model Context Protocol) server for Home Assistant. Gives AI assistants (Claude Desktop, LibreChat, Cline) full observability into your smart home — entity states, automations, scripts, devices, logs, diagnostics — without any write access. Built in Python, runs anywhere — locally, in Docker, or as an MCP integration.

## Requirements

- Python 3.11+ (for local use) or Docker
- A Home Assistant instance with a [long-lived access token](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token)
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
# HEALTH_REPORT_ENABLED=0               # optional, default shown
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
    ports:
      - "9091:9091"  # health
      - "9092:9092"  # MCP SSE
      - "9093:9093"  # REST API
    volumes:
      - /path/to/ha/config:/config:ro
    restart: unless-stopped
```

**Option B — with plain `docker run`:**

```bash
docker run -d \
  --name ha-mcp-readonly \
  -p 9091:9091 \
  -p 9092:9092 \
  -p 9093:9093 \
  -e HA_URL=http://your-ha-ip:8123 \
  -e HA_TOKEN=your_token \
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
| 9092 | SSE | MCP transport | `/sse`, `/messages` |
| 9093 | HTTP | REST API + Context Generator | `/api/*` |

### Verify

```bash
# Health check
curl http://localhost:9091/health

# List all MCP tools
curl http://localhost:9093/api/tools

# Generate a context snapshot
curl -X POST http://localhost:9093/api/context/generate \
  -H "Content-Type: application/json" \
  -d '{"mode": "hybrid"}'
```

## Available Tools (110+)

Tools are organized by category. All are **read-only** — no state changes, no service calls, no modifications.

| Category | Key tools |
|----------|-----------|
| **States** | `get_entity_state`, `get_states_grouped`, `search_entities`, `get_domains_summary`, `get_system_overview` |
| **Automations** | `list_automations`, `get_automation_code`, `diagnose_automation`, `search_automations_by_entity`, `get_automation_conflicts` |
| **Scripts & Scenes** | `list_scripts`, `get_script_code`, `list_scenes`, `get_scene_code` |
| **Blueprints** | `list_blueprints`, `get_blueprint_code`, `get_blueprint_instances`, `get_blueprint_usage_summary` |
| **Devices & Areas** | `get_device_details`, `search_devices`, `get_devices_by_area`, `get_area_devices_summary` |
| **Config entries** | `get_config_entry_details`, `search_config_entries`, `diagnose_config_entry`, `list_config_entry_domains` |
| **Integrations** | `get_integration_entities`, `get_integration_summary` |
| **Diagnostics** | `diagnose_system_health`, `get_unavailable_entities_grouped`, `integration_health` |
| **Logs** | `get_log_insights`, `analyze_log_errors`, `get_startup_errors`, `get_log_timeline`, `search_logs` |
| **History** | `get_history_summary`, `get_recent_state_changes` |
| **Context** | `entity_get_context_tree`, `get_entity_dependencies`, `get_entity_consumers` |
| **Config** | `get_main_configuration`, `search_in_config`, `validate_yaml`, `read_config_file` |
| **Storage** | `search_registries_batch`, `get_entity_registry`, `get_device_registry`, `get_area_registry` |
| **Batch** | `bulk_search_entities`, `compare_entities_state`, `validate_yaml_batch` |
| **Composite** | `investigate_entity`, `get_area_diagnostic`, `get_entity_with_automations` |
| **Dev tools** | `test_template`, `diagnose_entity`, `check_entity_exists`, `validate_automation_trigger` |

## Claude Desktop Configuration

Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ha-mcp-readonly": {
      "url": "http://localhost:9092/sse"
    }
  }
}
```

After restarting Claude Desktop, the 110+ Home Assistant tools will be available.

### LibreChat

```yaml
mcpServers:
  ha-mcp-readonly:
    url: http://ha-mcp-readonly:9092/sse
    timeout: 30000
```

## Context Generator

Creates a comprehensive Markdown file analyzing your entire Home Assistant instance. Available modes:

| Mode | Description |
|------|-------------|
| `offline` | Reads only from local filesystem (`/config`), no API calls |
| `online` | Fetches data from HA REST API (states, history, config) |
| `hybrid` | Combines offline and online data (default) |

The generated file includes entity inventory, automation analysis, script/scene listing, dashboard usage, log error patterns, device topology, config entry health, blueprint usage, and template entity references.

```bash
# Via REST API
curl -X POST http://localhost:9093/api/context/generate \
  -H "Content-Type: application/json" \
  -d '{"mode": "hybrid"}'

curl http://localhost:9093/api/context/download > ha-ai-context.md
```

## REST API

The REST API on port 9093 provides HTTP access to all tools and the context generator with an OpenAPI schema.

```bash
# List tools
curl http://localhost:9093/api/tools

# Call a tool
curl -X POST http://localhost:9093/api/tools/get_entity_state \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "sun.sun"}'

# OpenAPI schema
curl http://localhost:9093/api/openapi.json
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
pytest tests/unit/ -v --tb=short --cov=. --cov-report=term
```

All unit tests use mocked dependencies — no real Home Assistant instance required. 411 tests, no network calls.

### Integration tests (requires real HA)

```bash
export HA_URL=http://your-ha:8123
export HA_TOKEN=your_token
pytest tests/integration/ -v
```

### Lint & format

```bash
ruff check .
ruff format --check .
```

## Architecture

```
server.py                  # Main entry point — FastMCP + REST API + health check
context_generator/
├── core.py                # Entry points: main(), generate_context_file()
├── analyzers.py           # RegistryCollector, AutomationAnalyzer, LogAnalyzer, etc.
├── formatters.py          # ReportGenerator — Markdown output
├── constants.py           # ENTITY_PATTERN, HA URLs, ignorable domains, YAML loader
└── utils.py               # Registry cache, HA API client, YAML helpers

tools/
├── states.py              # Entity state queries (17 tools)
├── automations.py         # Automation analysis (10 tools)
├── scripts.py, scenes.py  # Script and scene inspection
├── blueprints.py          # Blueprint management (7 tools)
├── devices.py, areas.py   # Device and area tools
├── config_entries.py      # Config entry diagnostics
├── integrations.py        # Integration entity analysis
├── diagnostics.py         # System health, energy dashboard
├── logs.py                # Log analysis and insights
├── history.py             # State history and recent changes
├── entity_dependencies.py # Entity dependency graph
├── entity_context.py      # Entity context tree
├── config.py              # Configuration file tools
├── storage.py             # Registry dump and search tools
├── batch_operations.py    # Bulk entity operations
├── composite.py           # Composite diagnostic tools
├── dev_tools.py           # Template testing, validation
├── filesystem_explorer.py # Secured filesystem browsing
├── health_reporter.py     # Health score and metrics
├── utils.py               # Shared: HA API client, registry loader, log sanitizer
└── yaml_utils.py          # HomeAssistantLoader for HA-specific YAML tags

tests/
├── unit/                  # 26 test files, 411 tests, fully mocked
└── integration/           # Real HA tests (requires HA_URL + HA_TOKEN)
```

## Security

- **Read-only by design** — no write operations to Home Assistant. Cannot modify states, execute services, or trigger automations.
- **Filesystem restrictions** — access limited to `/config` directory. Path traversal (`..`, `~`) blocked. Max file size 10MB. Max directory depth 20.
- **Auth data blocked** — `auth`, `auth_provider.*`, `onboarding` registries are never returned.
- **Credential redaction** — `HA_TOKEN` is never logged or exposed in outputs. JWTs, passwords, API keys, and IP addresses are sanitized from log output.

## Notes

- The server exposes three ports: 9091 (health), 9092 (MCP SSE), 9093 (REST API). Ports are configurable via env.
- `MCP_DEV_TOOLS_ENABLED=0` disables template execution and debugging tools for production use.
- `HEALTH_REPORT_ENABLED=1` enables periodic health reports pushed to a Home Assistant sensor (`sensor.system_health_report`).
- Registry files (areas, devices, entities, config entries) are cached for 5 minutes to reduce filesystem I/O.
- All tool responses return JSON with a `success` field — always check this before reading `data`.

## License

MIT — see [LICENSE](LICENSE) for details.
