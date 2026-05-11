# Agent Instructions — HA-MCP-Readonly

> **Read before writing any tool, test, or documentation.**

## Language & Naming

### Mandatory English
- ALL code, comments, docstrings, commit messages, and tool descriptions MUST be in English.
- No Polish, no mixed-language fragments (`np.` → `e.g.`, `Zamiast` → `Instead of`).
- No Polish characters (ą, ę, ś, ć, ń, ó, ł, ż, ź) in source files.

### Generic Names Only
- Use generic, non-culture-specific names in examples and test fixtures:
  - `light.living_room` not `light.salon`
  - `area_id="office"` not `area_id="biuro"`
  - `person.test_user` not `person.pawel`
  - `zone.home`, `zone.work` not culture-specific zone names
- Mock data in `tests/fixtures.py` uses: `living_room`, `office`, `bedroom`

### Tool Descriptions
- First line of `@mcp.tool()` docstring MUST be a complete sentence describing what the tool does.
- NO emoji in tool description first lines.
- NO emoji in API response strings (status labels, messages).
- Every docstring must include `Args` and `Returns` sections.
- Use plain text status labels: `"OK"` not `"✅ OK"`, `"FAILED"` not `"❌ FAILED"`.

### Parameter Descriptions
- Use `e.g.` not `np.` for examples.
- Examples must use generic entity IDs: `light.living_room`, `sensor.temperature`, `person.test_user`.

---

## Test Standards

### Test Hierarchy

| Suite | Location | Runtime | Requires | Run with |
|-------|----------|---------|----------|----------|
| **Unit** | `tests/unit/` | <20s | Nothing | `pytest tests/unit/ -q` |
| **Smoke** | `tests/smoke/` | <5s | REST API (ports 9092/9093) + HA_TOKEN | `pytest tests/smoke/ -q` |
| **Integration** | `tests/integration/` | ~2min | Real HA + HA_TOKEN | `pytest tests/integration/ -q` |
| **E2E** | `tests/e2e/` | ~30s | Real HA + REST API + HA_TOKEN | `pytest tests/e2e/ -q` |

### Test Rules

1. **Unit tests:** Zero I/O, all dependencies mocked via `unittest.mock.patch`. Run without credentials.
2. **Smoke tests:** Direct REST API calls (`requests` library), no MCP wrapper needed. Skip if no `HA_TOKEN`.
3. **Integration tests:** Real HA via MCP wrapper (`MCPWrapper` from `tests/integration/conftest.py`). Skip if no `HA_TOKEN`.
4. **E2E tests:** Full pipeline (context generator) + REST API endpoints. Skip if no `HA_TOKEN`.
5. **Zero hardcoded names** in any test data — use mock fixture values.
6. **Test isolation:** Each test must be independent. Post-rely on shared state or test order.
7. **Skip, don't fail:** All non-unit tests use `pytest.mark.skipif(not HA_TOKEN, ...)`.

### Test Environment

1. Copy `.env.example` to `.env`
2. Fill in `HA_URL` and `HA_TOKEN`
3. `.env` is gitignored — never committed

### Writing Tests for a New Tool

Before writing any tool that calls the HA REST API:

1. **Verify the endpoint** in [official HA REST API docs](https://developers.home-assistant.io/docs/api/rest/)
2. **Test with `curl`** + LLAT on a real HA instance:
   ```bash
   curl -s -H "Authorization: Bearer $HA_TOKEN" "http://HA_IP:8123/the/endpoint"
   ```
   If it returns `404` or `401`, the endpoint is NOT accessible via LLAT.
3. **Write unit tests** (mocked) in `tests/unit/` — minimum 80% coverage for new code
4. **Add a smoke test** in `tests/smoke/` for basic functional verification
5. **Add an integration test** in `tests/integration/` for real HA validation

### The `get_automation_traces` Incident (v1.1.0)

- Tool was written assuming `/api/trace/context/` was a public REST endpoint
- All 6 unit tests used `patch("make_ha_request")` returning mocked `success: true`
- No curl verification against real HA
- Tool never worked in production; was removed in v1.1.1

**Lesson:** Mock-based unit tests are insufficient for API tools. Always verify with curl.

---

## File Organization

### Test Infrastructure

```
tests/
├── fixtures.py              # All mock data constants
│
├── unit/
│   ├── conftest.py          # Unit fixtures (mock_mcp, config_path, mock_registry_data, MCPWrapper)
│   └── test_*.py            # One file per tool domain
│
├── integration/
│   ├── conftest.py          # Integration fixtures (MCPWrapper, real_mcp, sample_entities)
│   └── test_*.py            # One file per tool domain
│
├── smoke/
│   ├── conftest.py          # Minimal: env loading + REST_API_URL
│   ├── test_connectivity.py # HA API, config dir, ports
│   └── test_critical_tools.py  # Per-category tool smoke tests
│
└── e2e/
    ├── conftest.py          # Env loading + temp output dir
    ├── test_context_generator.py  # Full pipeline generator tests
    └── test_server_api.py   # REST API endpoint tests
```

### Source Code

```
tools/
├── utils.py                 # Shared: make_ha_request(), load_registry(), sanitize_log_line()
├── yaml_utils.py            # HomeAssistantLoader for HA-specific YAML tags
├── states.py                # Entity state queries
├── automations.py           # Automation analysis and diagnostics
├── storage.py               # Registry dump, Lovelace, helpers
├── diagnostics.py           # System health, energy, person tracking
├── config.py                # Configuration file tools
├── ...
└── composite.py             # Composite diagnostic tools

context_generator/
├── constants.py             # Configuration, patterns, YAML loader
├── analyzers.py             # Data collectors (RegistryCollector, AutomationAnalyzer, etc.)
├── formatters.py            # ReportGenerator — markdown output
├── core.py                  # main() and generate_context_file() entry points
└── utils.py                 # Registry cache, HA API client, YAML helpers
```

---

## Code Quality

### Tool Response Format
- All tools return JSON strings with `{"success": True/False, ...}` structure
- Never raise unhandled exceptions — catch and return `{"success": False, "error": str(e)}`

### Input Validation
- Validate required parameters early — never pass `None` to string operations
- Check for empty strings, wrong types, missing keys before use

### Logging
- Use `logging` module instead of `print()` in production code (`tools/`, `server.py`)
- `context_generator/` CLI progress output may use `print()` (it's a CLI tool, not a server)
- Never log `HA_TOKEN`, passwords, or API keys

### Security
- `.env` is gitignored — never commit credentials
- `BLOCKED_REGISTRIES` prevents loading `auth`, `auth_provider.*`, `onboarding` registries
- `sanitize_log_line()` redacts JWTs, tokens, passwords, IPs from log output
- Path traversal blocked in `filesystem_explorer.py` — `..` and `~` rejected

### [READ] Risk Prefix

- Every `@mcp.tool()` docstring MUST begin with `[READ]` as the first text.
- Tools without the prefix will fail MCP standards compliance checks.
- Reference: `ref.mcp-server-standards`, Section "Risk Annotations (L1+)".
- Exception: dev tools gated behind `MCP_DEV_TOOLS_ENABLED` flag.

### Exception Handler Tests [TEST-REG-3]

- Every tool wrapper's `except Exception` block MUST have a corresponding unit test.
- Pattern: patch the internal `_do_*` function with `side_effect=RuntimeError("msg")`,
  call the tool, assert `data["success"] is False` and error text matches.
- Reference: MCP Server Architect standard, Canonical Template 14.
- Example: see `tests/unit/test_automations.py::TestExceptionHandler`.

### AFDS Documentation Standard

- All documentation files in `docs/` conform to AI-First Documentation Standard.
- `afds_config.yaml` — project-specific validator configuration in repository root.
- Validate docs: `python3 /var/apps/ai-skills/skills/afds-doc-writer/docs_validate.py --config afds_config.yaml docs/`
- Reference: `/var/apps/ai-skills/skills/afds-doc-writer/docs_standards.md`

---

## Coverage Requirements

| Requirement | Threshold |
|-------------|-----------|
| Per-tool module minimum | 80% |
| Overall tools/ coverage | >85% |
| New tool unit tests | >80% of new lines |
| New tool smoke test | At least 1 |
| Critical tool (entity state, automations, registries) | Unit + smoke + integration |

---

## Context Generator v1.0

The context generator produces a comprehensive Markdown snapshot of the HA instance.

- **Analyzers:** 12 total (6 original + 6 new in v1.0)
- **Output sections:** 18 (entities, automations, scripts, scenes, templates, dashboards, logs, history, dependencies, conflicts, persons, zones, energy, helpers, services, HACS, blueprint usage, quick reference)
- **Modes:** `offline` (filesystem only), `online` (API only), `hybrid` (both, default)
- **Env vars:** `HA_URL`, `HA_TOKEN`, `HA_CONFIG_PATH` — MUST be set before import or explicitly via `generate_context_file()` params

---

## Common Pitfalls

1. **Module-level imports bind early:** `from .constants import HA_URL` binds the value at import time. Changing `constants.HA_URL` later does NOT affect already-imported modules. Set env vars BEFORE importing context_generator.

2. **`_get_automation_by_id_or_alias` needs strings:** Pass `None` → crash. Always validate `automation_id` before calling internal helpers.

3. **`list_automations` response:** Must include `id` field (unique_id from automations.yaml) so clients can call `get_automation_code`.

4. **Fixture resolution:** Pytest auto-discovers only `conftest.py` files, NOT `__init__.py`. Put test fixtures in `conftest.py`.

5. **Mock MCP pattern:** Unit tests use `MagicMock` with a custom `tool` decorator that stores tools in `mcp._tools[func.__name__]`. Tools are called via `await mock_mcp._tools["tool_name"](args)`.

6. **Response format:** Every tool MUST return `{"success": True/False, ...}`. Some older tools (get_lovelace_dashboards, get_persons, get_zones, get_hacs_data, trigger_health_report) historically returned plain JSON — always verify with curl after writing a new tool.

7. **Parameter naming consistency:** Use snake_case for all parameters. `read_file` uses `file_path` (not `path`), matching `read_config_file(file_path=...)`. Keep parameter names consistent between similar tools.

8. **UI-created automations:** `_load_automations()` only reads `automations.yaml`. UI-created automations exist only in HA state engine. Tools like `get_automation_usage_stats` must fall back to searching `/api/states` for `automation.*` entities.

9. **Smoke test response format check:** `tests/smoke/test_response_format.py` iterates all tools and verifies `success` field. New tools with required parameters must be added to `_REQUIRES_PARAMS` set or the test will fail.

10. **Integration conftest:** New tool modules must be registered in `tests/integration/conftest.py` (import + `register_*_tools()` call) or integration tests won't find them.
