---
description: Full documentation for HA-MCP-Readonly — MCP server giving AI assistants read-only Home Assistant observability
last_verified: 2026-05-10
---

# HA-MCP-Readonly Documentation

> Read-only MCP (Model Context Protocol) server for Home Assistant.
> Enables AI assistants to observe and analyze a Home Assistant instance without any write access.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Quick Start](#quick-start)
4. [Configuration](#configuration)
5. [MCP Tools](#mcp-tools)
6. [REST API](#rest-api)
7. [Context Generator](#context-generator)
8. [Security](#security)
9. [Testing](#testing)
10. [Development](#development)
11. [Docker](#docker)
12. [Troubleshooting](#troubleshooting)

---

## Overview

HA-MCP-Readonly is a read-only Model Context Protocol server that connects to your Home Assistant instance and exposes its data as structured tools for AI assistants (LibreChat, Claude, custom MCP clients).

**Key design decisions:**
- **Read-only by design** — no write operations to Home Assistant
- **Token-optimized** — tools return aggregated data to minimize LLM context usage
- **Security-first** — filesystem access limited to `/config`, auth data blocked
- **Standalone** — single Python process, no external databases

**Use cases:**
- AI-assisted debugging of automations and scripts
- Entity state monitoring and health checks
- Configuration analysis and YAML validation
- Log analysis with pattern recognition
- Context generation for offline AI assistants

---

## Architecture

```
┌─────────────────┐      ┌──────────────────────┐      ┌─────────────────┐
│  MCP Client     │      │  HA-MCP-Readonly     │      │  Home Assistant │
│  (LibreChat,    │◄────►│  Port 9091-9093      │◄────►│  (API + Files)  │
│   Claude, etc.) │ MCP   │  - Health (9091)     │ HTTP │                 │
│                 │ SSE   │  - MCP SSE (9092)    │      │  /config        │
│                 │       │  - REST API (9093)   │      │  /api/states    │
└─────────────────┘       └──────────────────────┘      └─────────────────┘
```

| Port | Protocol | Purpose |
|------|----------|---------|
| 9091 | HTTP | Health check (`GET /health`) |
| 9092 | SSE | MCP transport (`/sse`, `/messages`) |
| 9093 | HTTP | REST API (`/api/*`) + Context Generator |

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Home Assistant instance with a [long-lived access token](https://www.home-assistant.io/docs/configuration/secrets/#long-lived-access-token)
- Home Assistant config directory available on the host

### 1. Clone and Configure

```bash
git clone <repository-url> ha-mcp-readonly
cd ha-mcp-readonly
cp .env.example .env
# Edit .env with your HA_URL and HA_TOKEN
```

### 2. Start the Server

```bash
docker compose up -d
```

### 3. Verify

```bash
# Health check
curl http://localhost:9091/health

# List MCP tools
curl http://localhost:9093/api/tools

# Check context generator status
curl http://localhost:9093/api/context/status
```

---

## Configuration

All configuration is via environment variables. See `.env.example` for a complete template.

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `HA_URL` | Home Assistant URL | `http://homeassistant:8123` |
| `HA_TOKEN` | Long-lived access token | `eyJ0eXAiOiJKV1QiLCJhbG...` |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `HA_CONFIG_PATH` | `/config` | Path to HA configuration directory inside container |
| `HEALTH_CHECK_PORT` | `9091` | Health check HTTP server port |
| `MCP_SSE_PORT` | `9092` | MCP SSE transport port |
| `REST_API_PORT` | `9093` | REST API port |
| `MCP_DEV_TOOLS_ENABLED` | `1` | Enable developer tools (template testing and diagnostics) |
| `RUN_TESTS_ON_STARTUP` | `0` | Run unit tests on server startup |
| `OUTPUT_PATH` | `/app/output/ha-ai-context.md` | Path for generated context file |

### Docker Compose Volume

Mount your Home Assistant config directory as read-only:

```yaml
volumes:
  - /path/to/your/homeassistant/config:/config:ro
```

---

## MCP Tools

The server exposes 118 read-only MCP tools organized by domain.
Call `GET /api/tools` on the REST API for the full, up-to-date list.

### Entity State Tools

| Tool | Description |
|------|-------------|
| `get_all_states` | Get all entities (domain filter, optional attributes) |
| `get_entity_state` | Get detailed state of a single entity. Compact mode keeps state + domain-specific attributes (unit, device_class for sensors; hvac_action, temperature for climate) |
| `get_entity_state_batch` | Batch: get states for multiple entities |
| `get_states_grouped` | Group entities by domain or integration |
| `get_states_filtered` | Server-side filtering (domain, state, device_class, area) |
| `search_entities` | Search entities by name or entity_id |
| `get_domains_summary` | Summary count per domain |
| `get_system_overview` | Complete system overview with problems & recommendations |
| `get_entity_changes` | Detect recently changed entities |
| `get_history_batch` | Fetch history of changes for entity list |
| `verify_recent_implementation` | Verify recent changes (entities, automations, issues) |
| `get_services` | List available services and domains |

### Automation & Script Tools

| Tool | Description |
|------|-------------|
| `search_automations` | Find automations by name/pattern (supports include_entity_id) |
| `list_automations` | List all automations |
| `get_automation_entity_id` | Resolve automation alias to entity_id via entity registry |
| `get_automation_code` | Full automation YAML code |
| `get_automation_dependencies` | Dependency graph (entities, scripts, services) |
| `search_automations_by_entity` | Reverse lookup: automations using an entity |
| `get_automation_conflicts` | Detect race conditions and feedback loops |
| `diagnose_automation` | Comprehensive automation diagnostics |
| `get_automation_usage_stats` | Usage stats (runs, last trigger, history). Supports detail_level: summary (default) or full (adds activity log, state changes, context chain) |
| `automation_validate_triggers` | Validate trigger IDs and handlers |
| `list_scripts` | List all scripts |
| `get_script_code` | Full script YAML code |

### Scene & Blueprint Tools

| Tool | Description |
|------|-------------|
| `list_scenes` | List all scenes |
| `get_scene_code` | Full scene YAML code |
| `list_blueprints` | List blueprint files |
| `get_blueprint_code` | Full blueprint YAML |
| `get_blueprint_instances` | List automations using a blueprint |
| `get_blueprint_usage_summary` | Blueprint adoption summary |

### Device, Area & Registry Tools

| Tool | Description |
|------|-------------|
| `get_device_details` | Full device context with entities |
| `get_device_entities` | List entities belonging to a device |
| `search_devices` | Search/filter devices |
| `get_devices_by_area` | Devices in a given area |
| `device_get_wifi_status` | WiFi status for IoT devices |
| `get_area_devices_summary` | Summary of devices in an area |
| `get_entity_registry` | List all registered entities |
| `get_entity_registry_batch` | Batch fetch entity registry entries (filter by entity IDs and fields) |
| `get_entity_details` | Entity registry details with compact mode. Supports batch (comma-separated entity IDs) |
| `get_device_registry` | List all devices |
| `get_area_registry` | List all areas |
| `get_config_entries` | List all integrations |
| `get_config_entry_details` | Details of a single config entry |
| `search_config_entries` | Search/filter config entries |
| `diagnose_config_entry` | Diagnose a config entry |
| `list_config_entry_domains` | List domains with config entries |

### Configuration & YAML Tools

| Tool | Description |
|------|-------------|
| `read_file` | Read file with offset/limit support |
| `read_config_file` | Read config file with offset/limit |
| `search_in_config` | Search configuration files |
| `search_in_config_batch` | Batch search in config files |
| `search_config_by_params` | Parameterized config search |
| `validate_yaml_syntax` | Validate YAML syntax |
| `get_main_configuration` | Main HA configuration |
| `get_config_structure` | Config directory structure |

### Lovelace / Dashboard Tools

| Tool | Description |
|------|-------------|
| `get_lovelace_dashboards` | List all dashboards |
| `get_lovelace_config` | Full dashboard configuration |
| `get_lovelace_resources` | List custom resources (JS/CSS modules) |
| `get_lovelace_entity_usage` | Locate entity in dashboards |
| `search_lovelace_config` | Search cards by entity, type, or term |
| `get_lovelace_config_summary` | Token-efficient dashboard structure summary |
| `diagnose_lovelace_setup` | Full dashboard diagnostics (missing entities, resources, modes) |

### Diagnostic & Log Tools

| Tool | Description |
|------|-------------|
| `get_log_insights` | Analyzed log summary |
| `analyze_log_errors` | Error analysis from logs |
| `get_recent_logs` | Recent Home Assistant logs |
| `get_previous_logs` | Previous HA log file |
| `search_logs` | Search logs by pattern |
| `get_component_logs` | Logs filtered by component |
| `get_startup_errors` | Startup error summary |
| `get_log_timeline` | Timeline view of log events |
| `diagnose_system_health` | System health diagnostics |
| `get_unavailable_entities_grouped` | Unavailable entities grouped |
| `get_integration_health` | Integration health status |
| `get_notification_history` | Notification history |
| `get_energy_dashboard_data` | Energy dashboard data |
| `trigger_health_report` | Generate system health report (JSON) |

### Composite & Context Tools

| Tool | Description |
|------|-------------|
| `investigate_entity` | Super function: entity + device + area + automations + history |
| `get_entity_with_automations` | Entity context + related automations + conflicts |
| `get_area_diagnostic` | Full area/room diagnostic |
| `diagnose_person_tracking` | Person tracking: trackers, zones, automations, staleness |
| `entity_get_context_tree` | Entity context tree |
| `get_entity_dependencies` | Reverse lookup: where entity is used. detail_level: summary (default) or full (file paths, line numbers). include_context for YAML context lines |
| `get_entity_consumers` | What depends on an entity |

### Developer Tools (Optional)

| Tool | Description |
|------|-------------|
| `test_template` | Test a Jinja2 template |
| `test_templates_batch` | Batch template testing |
| `compare_templates` | Compare two Jinja2 template evaluations (detects stale macro cache) |
| `get_template_performance` | Template performance metrics |
| `validate_automation_trigger` | Validate automation trigger |
| `test_condition` | Test HA condition |
| `check_entity_exists` | Check if entity exists |
| `check_entities_batch` | Batch entity existence check |
| `test_service_call` | Test service call format |
| `diagnose_entity` | Entity diagnostics |
| `diagnose_template` | Template diagnostics (UI + YAML helpers) |
| `diagnose_energy_setup` | Energy setup diagnostics |

---

## Parameter Patterns

Several tools share common parameter conventions for consistent behavior across the API.

### detail_level

Controls how much data a tool returns. The two-tier pattern is used by `get_automation_usage_stats` and `get_entity_dependencies`.

| Value | Behavior |
|-------|----------|
| `"summary"` (default) | Return compact results suitable for quick checks |
| `"full"` | Include additional data: activity logs, state changes, context chains, file paths, and line numbers |

Specifying `"summary"` saves tokens and provides faster responses for routine queries. Use `"full"` when investigating issues that require deeper context.

### compact

A boolean flag that reduces returned fields to essentials. Available on `get_entity_state`, `get_entity_details`, and related tools.

| Value | Behavior |
|-------|----------|
| `false` (default) | Return full entity object with all attributes |
| `true` | Strip verbose fields. Core fields retained: entity_id, state, friendly_name, last_changed, last_updated |

**Domain-specific compact mode** (`get_entity_state` with `compact=true`): Keeps select attributes relevant to each domain.

| Domain | Attributes preserved in compact mode |
|--------|--------------------------------------|
| `climate` | hvac_modes, hvac_action, current_temperature, temperature |
| `sensor` | unit_of_measurement, device_class |
| `light` | brightness, color_mode |
| `cover` | current_position |
| `binary_sensor` | device_class |
| all others | No attributes (entity_id, state, friendly_name only) |

### include_* Boolean Flags

Tools accept boolean flags prefixed with `include_` to optionally expand the response.

| Flag | Tools | Effect |
|------|-------|--------|
| `include_code` | `search_automations` | Append full automation YAML to each match |
| `include_entity_id` | `search_automations` | Resolve and append entity_id from entity registry |
| `include_context` | `get_entity_dependencies` | Add surrounding YAML context lines for each reference |

All flags default to `false` to keep responses compact. Set to `true` only when the extra detail is needed.

### Batch Convention

Tools that accept comma-separated batch inputs use a string parameter (not a list) for transport simplicity:

| Tool | Parameter | Example |
|------|-----------|---------|
| `get_entity_details` | `entity_id` | `"sensor.temp,light.kitchen,switch.garage"` |
| `get_entity_state_batch` | `entity_ids` | `"light.a,light.b,sensor.c"` |
| `get_entity_registry_batch` | `entity_ids` | `"light.a,light.b"` |
| `check_entities_batch` | `entity_ids` | `"sensor.x,sensor.y"` |

Most batch tools have a maximum of 100 entities per call. Check individual tool descriptions for limits.

---

## REST API

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/api/tools` | List all registered tools |
| POST | `/api/tools/{name}` | Call a tool by name |
| GET | `/api/openapi.json` | OpenAPI schema |
| POST | `/api/context/generate` | Start context generation |
| GET | `/api/context/status` | Generation status |
| GET | `/api/context/download` | Download generated file |
| GET | `/api/context/modes` | List generation modes |

### Example: Call a Tool

```bash
curl -X POST http://localhost:9093/api/tools/search_entities \
  -H "Content-Type: application/json" \
  -d '{"search_term": "light.living_room"}'
```

### Example: Generate Context

```bash
# Start generation
curl -X POST http://localhost:9093/api/context/generate

# Check status
curl http://localhost:9093/api/context/status

# Download result
curl -o ha-context.md http://localhost:9093/api/context/download
```

---

## Context Generator

The context generator creates an AI-friendly markdown file from your Home Assistant configuration — a static snapshot of your entire smart home that can be used independently of the live MCP server.

**Use cases:**

- **RAG systems** — use the generated file as a knowledge base for retrieval-augmented generation (e.g., with LangChain, LlamaIndex, or custom RAG pipelines)
- **ChatGPT Projects / Qwen / Claude Projects** — upload the file as custom knowledge to give the AI full awareness of your smart home without any network access to HA
- **Static context for AI coding tools** — provide the file alongside your codebase so AI assistants understand your automations, devices, and entity relationships
- **Documentation snapshots** — freeze configuration state for auditing, debugging, or sharing with other users

### Modes

| Mode | Description |
|------|-------------|
| `offline` | Reads from local filesystem only |
| `online` | Fetches live data from HA API |
| `hybrid` | Combines both (default) |

### Generated Content

The output file (`ha-ai-context.md`) includes:
- System overview (version, location, integrations)
- Entity inventory (grouped by domain)
- Automation summaries (triggers, actions, conditions)
- Script and scene listings
- Device and area mappings
- Configuration file tree
- Recent error patterns from logs

---

## Security

### Read-Only Design

- **No write operations** — All MCP tools are read-only
- **No service calls** — Cannot turn on lights or run scripts
- **No state changes** — Cannot modify entity states

### Filesystem Restrictions

- Access limited to `/config` (HA configuration directory)
- Path traversal blocked (`../etc/passwd` → rejected)
- Max file size: 10MB
- Max directory depth: 20 levels
- Auth registry (`auth`, `auth_provider.*`) explicitly blocked

### Credential Protection

- `HA_TOKEN` never exposed in tool outputs
- Secrets redacted from logs (`password`, `token`, `api_key`)
- Log lines sanitized before returning to AI

---

## Testing

### Unit Tests

No real HA instance required — all dependencies mocked.

```bash
pytest tests/unit/ -v --tb=short
```

### Integration Tests

Requires `HA_URL` and `HA_TOKEN` in environment:

```bash
export HA_URL=http://your-ha:8123
export HA_TOKEN=your_token
pytest tests/integration/ -v
```

### Test Coverage

```bash
pytest tests/unit/ --cov=. --cov-report=html
```

---

## Development

### Project Structure

```
.
├── server.py              # Main server (MCP + REST API)
├── conftest.py            # Test fixtures
├── requirements.txt       # Dependencies
├── Dockerfile             # Container image
├── docker-compose.yml     # Quick start
├── .env.example           # Configuration template
├── tools/                 # MCP tool modules
│   ├── states.py          # Entity states, search, history
│   ├── automations.py     # Automation analysis and diagnostics
│   ├── scripts.py         # Script management
│   ├── scenes.py          # Scene management
│   ├── blueprints.py      # Blueprint management
│   ├── config.py          # YAML/config tools
│   ├── logs.py            # Log analysis
│   ├── storage.py         # Registry access
│   ├── diagnostics.py     # System diagnostics
│   ├── dev_tools.py       # Developer tools
│   ├── health_reporter.py # Health report generation (read-only JSON)
│   ├── composite.py       # Composite diagnostics
│   ├── batch_operations.py# Batch operations
│   ├── areas.py           # Area management
│   ├── devices.py         # Device details and context
│   ├── config_entries.py  # Config entry management
│   ├── entity_dependencies.py # Entity usage tracking
│   ├── entity_context.py  # Entity context tree
│   ├── history.py         # History analysis
│   ├── integrations.py    # Integration entity analysis
│   ├── filesystem_explorer.py # Filesystem access (allowlisted)
│   ├── utils.py           # Shared utilities
│   └── yaml_utils.py      # Custom YAML loader
├── context_generator/     # Context generation engine
│   ├── core.py
│   ├── analyzers.py
│   ├── formatters.py
│   ├── constants.py
│   └── utils.py
├── tests/
│   ├── unit/              # Unit tests
│   └── integration/       # Integration tests
└── docs/
    └── documentation.md   # This documentation
```

### Adding a New Tool

1. Choose the appropriate module in `tools/`
2. Add the tool function with `@mcp.tool()` decorator
3. Update the `register_*_tools()` function
4. Add tests in `tests/unit/test_*.py`

### Tool Pattern

```python
@mcp.tool()
async def my_tool(entity_id: str) -> str:
    """
    Brief description of what the tool does.

    Args:
        entity_id: Description of the parameter

    Returns:
        JSON string with the result
    """
    try:
        result = do_something(entity_id)
        return json.dumps({"success": True, "data": result}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)
```

---

## Docker

### Build

```bash
docker build -t ha-mcp-readonly .
```

### Run

```bash
docker run -d \
  --name ha-mcp-readonly \
  -p 9091:9091 \
  -p 9092:9092 \
  -p 9093:9093 \
  -e HA_URL=http://homeassistant:8123 \
  -e HA_TOKEN=your_token \
  -v /path/to/ha/config:/config:ro \
  ha-mcp-readonly
```

### Compose

See `docker-compose.yml` for a complete example.

---

## Troubleshooting

### Server won't start

1. Check `HA_TOKEN` is set
2. Verify HA is accessible: `curl ${HA_URL}/api/config -H "Authorization: Bearer ${HA_TOKEN}"`
3. Check Docker logs: `docker logs ha-mcp-readonly`

### Tools return errors

1. Verify `/config` volume is mounted correctly
2. Check HA token has necessary permissions
3. Review container filesystem access

### Context generator fails

1. Verify `HA_CONFIG_PATH` points to valid config directory
2. Check output directory is writable
3. Review logs for specific errors

### MCP client can't connect

1. Verify port 9092 is exposed
2. Check SSE endpoint: `curl http://localhost:9092/sse`
3. Ensure CORS settings match your client origin

---

## License

MIT License — see [LICENSE](./LICENSE) for details.

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

For coding standards, see the project docstrings and existing tool patterns.
