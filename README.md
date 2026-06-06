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
# MCP_SSE_PORT=9092                   # optional, default shown
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
    ports:
      - "9091:9091"  # health
      - "9092:9092"  # MCP SSE
      - "9093:9093"  # REST API
    volumes:
      - /path/to/ha/config:/config:ro  # Replace with your HA config path (e.g., /config, ~/.homeassistant)
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:9091/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s
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

## Available Tools (136 with dev tools, 124 without)

Tools are organized by category (53 shown in table below). All are **read-only** — no state changes, no service calls, no modifications.

| Category | Key tools |
|----------|-----------|
| **States** | `get_entity_state`, `get_states_grouped`, `search_entities`, `get_domains_summary`, `get_system_overview` |
| **Automations** | `list_automations`, `get_automation_code`, `get_automation_file_location`, `diagnose_automation`, `search_automations_by_entity`, `get_automation_conflicts`, `get_automation_entity_id` |
| **Scripts & Scenes** | `list_scripts`, `get_script_code`, `list_scenes`, `get_scene_code` |
| **Blueprints** | `list_blueprints`, `get_blueprint_code`, `get_blueprint_instances`, `get_blueprint_usage_summary` |
| **Devices & Areas** | `get_device_details`, `search_devices`, `get_devices_by_area`, `get_area_devices_summary` |
| **Config entries** | `get_config_entry_details`, `search_config_entries`, `diagnose_config_entry`, `list_config_entry_domains` |
| **Integrations** | `get_integration_entities`, `get_integration_summary` |
| **Diagnostics** | `diagnose_system_health`, `get_unavailable_entities_grouped`, `get_integration_health` |
| **Logs** | `get_log_insights`, `analyze_log_errors`, `get_startup_errors`, `get_log_timeline`, `search_logs` |
| **History** | `get_entity_state_history_summary`, `get_recent_state_changes` |
| **Context** | `entity_get_context_tree`, `get_entity_dependencies`, `get_entity_consumers` |
| **Config** | `get_main_configuration`, `search_in_config`, `validate_yaml_syntax`, `read_config_file` |
| **Storage** | `search_registries_batch`, `get_entity_registry`, `get_device_registry`, `get_area_registry`, `get_template_entity_code` |
| **Lovelace** | `get_lovelace_dashboards`, `get_lovelace_config`, `get_lovelace_resources`, `search_lovelace_config`, `get_lovelace_config_summary`, `diagnose_lovelace_setup` |
| **Batch** | `bulk_search_entities`, `compare_entities_state`, `validate_yaml_batch`, `get_automation_codes_batch` |
| **Composite** | `investigate_entity`, `get_area_diagnostic`, `get_entity_with_automations`, `diagnose_person_tracking` |
| **Dev tools** | `test_template`, `compare_templates`, `diagnose_entity`, `check_entity_exists`, `validate_automation_trigger`, `diagnose_template` |

## What's New in v1.6.0

- **2 new tools**: `get_automation_entity_id` (Automations) and `compare_templates` (Dev tools)
- **Parameter patterns**: `compact`, `include_state`, `include_entities`, `include_options`, and `detail_level` added across tools for flexible response control
- **Batch improvements**: `get_entity_details` supports multiple entity IDs; compact mode for states
- **Enhanced automation traces**: logbook context and `include_entity_id` in search

### Parameter Patterns

Several tools now support these cross-cutting parameter patterns for fine-grained response control:

| Pattern | Description | Example tools |
|---------|-------------|---------------|
| `compact` | Strips attributes, context, and last_reported; keeps state, timestamps, and friendly_name | `get_entity_state`, `get_states_filtered`, `get_all_states` |
| `detail_level` | Controls response detail: `summary`, `standard`, or `full` | `get_device_details`, `get_system_overview`, `get_overview` |
| `include_*` | Selective inclusion of optional data fields | `include_state`, `include_entities`, `include_options` on various tools |
| `include_entity_id` | When false, omits entity_id from responses for token-efficient summaries | `search_automations` |

## Claude Desktop Configuration

Add the following to your Claude Desktop config:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Linux**: `~/.config/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "ha-mcp-readonly": {
      "url": "http://localhost:9092/sse"
    }
  }
}
```

After restarting Claude Desktop, the 136 Home Assistant tools will be available (124 without dev tools enabled).

### LibreChat

```yaml
mcpServers:
  ha-mcp-readonly:
    url: http://ha-mcp-readonly:9092/sse
    timeout: 30000
```

## Context Generator

Generates a comprehensive Markdown snapshot of your entire Home Assistant instance. Designed for scenarios where live MCP access isn't available or desired:

**Use cases:**
- **RAG systems** — use the generated file as a knowledge base for retrieval-augmented generation (e.g., with LangChain, LlamaIndex, or custom RAG pipelines)
- **ChatGPT Projects / Qwen / Claude Projects** — upload the file as custom knowledge to give the AI full awareness of your smart home without network access to HA
- **Static context for AI coding tools** — provide the file alongside your codebase so AI assistants understand your automations, devices, and entity relationships
- **Documentation snapshots** — freeze configuration state for auditing, debugging, or sharing with other users

**Modes:**

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
# Unit tests (no credentials needed, 884 tests, <20s)
pytest tests/unit/ -q

# Smoke tests (requires local MCP server, 84 tests, <5s)
pytest tests/smoke/ -q

# Integration tests (requires real HA, 98 tests, ~2min)
export HA_URL=http://your-ha:8123
export HA_TOKEN=your_token
pytest tests/integration/ -q

# E2E tests (requires real HA + local MCP server, 24 tests, ~30s)
pytest tests/e2e/ -q

# All tests
pytest tests/unit/ tests/smoke/ tests/e2e/ tests/integration/ -q
```

All unit tests use mocked dependencies — no real Home Assistant instance required. 1090 total tests across 4 suites.

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
├── automations.py         # Automation analysis (10 tools)
├── batch_operations.py    # Bulk entity operations
├── blueprints.py          # Blueprint management (4 tools)
├── capabilities.py        # Zero-I/O MCP introspection tool catalog
├── categories.py          # Category management (automation, script, scene, helpers)
├── composite.py           # Composite diagnostic tools
├── config.py              # Configuration file tools
├── config_entries.py      # Config entry diagnostics (4 tools)
├── devices.py, areas.py   # Device and area tools (5+1 tools)
├── dev_tools.py           # Template testing, validation
├── diagnostics.py         # System health, energy dashboard
├── entity_context.py      # Entity context tree
├── entity_dependencies.py # Entity dependency graph
├── filesystem_explorer.py # Secured filesystem browsing
├── health_reporter.py     # Health score and metrics
├── helpers_health.py      # Helper entity health diagnostics
├── history.py             # State history and recent changes
├── integrations.py        # Integration entity analysis (2 tools)
├── logs.py                # Log analysis and insights
├── manifests.py           # TOOL_MANIFESTS, risk prefix injection
├── observability.py       # request_id, invocation counters
├── scripts.py, scenes.py  # Script and scene inspection (2+2 tools)
├── states.py              # Entity state queries (12 tools)
├── storage.py             # Registry dump and search tools
├── utils.py               # Shared: HA API client, registry loader, log sanitizer
├── validators.py          # Input validation and schema checks
└── yaml_utils.py          # HomeAssistantLoader for HA-specific YAML tags

tests/
├── unit/                  # 26 test files, 884 tests, fully mocked
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
- **Security note**: Ports 9091-9093 should not be exposed publicly. Use firewall rules or reverse proxy with authentication if needed.
- Registry files (areas, devices, entities, config entries) are cached for 5 minutes to reduce filesystem I/O.
- All tool responses return JSON with a `success` field — always check this before reading `data`.

## Troubleshooting

For common issues and solutions, see [docs/documentation.md#troubleshooting](docs/documentation.md#troubleshooting).

## License

MIT — see [LICENSE](LICENSE) for details.
