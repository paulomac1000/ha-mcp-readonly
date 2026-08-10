---
description: Mandatory engineering and testing instructions for agents contributing to HA-MCP-Readonly.
doc_id: reference.ha-mcp-agent-instructions
type: reference
status: active
rigor: normative
owners: [repository-maintainers]
verification: Run the complete local quality, unit, protocol, package, and runtime gates documented in this file.
---

<!-- agents-md: waive context-budget reason="This file is the repository operating contract for a large read-only MCP server and must keep security, HA API, coverage, and gate rules visible in every session." -->

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

1. **Unit tests:** No network, Home Assistant, operator-filesystem, or external-service I/O. Ephemeral `tmp_path` I/O is allowed only when filesystem/path semantics are the behavior under test; all other dependencies are mocked. Run without credentials.
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
4. **Add a smoke test** in `tests/smoke/test_critical_tools.py` for basic functional verification
5. **Add an integration test** in `tests/integration/test_integration.py` for real HA validation

### The `get_automation_traces` Incident (v1.1.0)

- Tool was written assuming `/api/trace/context/` was a public REST endpoint
- All 6 unit tests used `patch("make_ha_request")` returning mocked `success: true`
- No curl verification against real HA
- Tool never worked in production; was removed in v1.1.1

**Lesson:** Mock-based unit tests are insufficient for API tools. Always verify with curl.

## HA API Authentication

### Critical: LLAT vs Frontend Auth

Home Assistant has **two separate authentication scopes** for its REST API:

| Auth Method | Scope | Use Case |
|-------------|-------|----------|
| `Authorization: Bearer <LLAT>` | Public REST API endpoints | Entity states, services, config check, templates, history, logbook |
| Frontend session cookie | Internal/frontend endpoints | Trace context, some config flows, UI-only endpoints |

### The LLAT vs Frontend Trap

**Long-Lived Access Tokens (LLATs)** do NOT have access to every endpoint that the frontend uses. Some endpoints (like `/api/trace/context/`) require a frontend session cookie and will return `404` or `401` when accessed with an LLAT.

### Before Implementing Any New HA API Tool

1. **Verify the endpoint in official docs** first:
   - [HA REST API docs](https://developers.home-assistant.io/docs/api/rest/) — consult this
     reference before implementing any tool that calls the HA REST API; it decides
     whether an endpoint is part of the documented public REST surface. Documentation establishes API shape, not the privileges of a particular token.
   - If the endpoint is NOT listed there, do not claim public REST support without separate authoritative evidence.

2. **Test the endpoint with curl BEFORE writing any code:**
   ```bash
   curl -s -H "Authorization: Bearer $HA_TOKEN" "http://HA_IP:8123/the/endpoint"
   ```
   Use the same class of LLAT intended for production. Support is documented only after this request succeeds with that credential class; `401`/`403`/`404` means the LLAT-access claim is not verified.

3. **Never assume** an endpoint exists only because you saw it in:
   - WebSocket API docs (different transport)
   - Frontend network tab (uses cookie auth)
   - Other Home Assistant API wrappers (may use different auth)

### Preventing Recurrence

After implementing any new HA API tool:

- [ ] Endpoint verified in [official HA REST API docs](https://developers.home-assistant.io/docs/api/rest/)
- [ ] Endpoint tested with `curl` + LLAT on a real HA instance
- [ ] At least one test uses a recorded VCR cassette (not only a mock)
- [ ] CI smoke test confirms the tool count is correct

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
version.py                    # Single source of truth for version number
tools/
├── utils.py                 # Shared: make_ha_request(), load_registry(), sanitize_log_line()
├── yaml_utils.py            # HomeAssistantLoader for HA-specific YAML tags
├── states.py                # Entity state queries
├── automations.py           # Automation analysis and diagnostics
├── storage.py               # Registry dump, Lovelace, helpers
├── diagnostics.py           # System health, energy, person tracking
├── config.py                # Configuration file tools
├── manifests.py             # TOOL_MANIFESTS, risk prefix injection
├── capabilities.py          # Zero-I/O MCP introspection tool catalog
├── observability.py         # request_id, invocation counters
├── categories.py            # Category management (automation, script, scene, helpers)
├── helpers_health.py        # Helper entity health diagnostics
├── validators.py            # Input validation and schema checks
├── ...
└── composite.py             # Composite diagnostic tools

context_generator/
├── config.py                # Immutable per-run generation configuration
├── runtime.py               # Context-local runtime and provenance binding
├── provenance.py            # Completeness matrix and recursive redaction
├── snapshot.py              # Safe filesystem, REST, and WebSocket snapshot collector
├── constants.py             # Legacy patterns and YAML loader
├── analyzers.py             # Domain analyzers
├── formatters.py            # Atomic bounded Markdown report writer
├── core.py                  # Isolated generation entry points
└── utils.py                 # Runtime-aware registry and Home Assistant adapters
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
- Path traversal blocked in `tools/filesystem_explorer.py` — `..` and `~` rejected

### Risk Prefix (L2+)

- Risk prefix (`[READ]`, `[WRITE]`, among others) is dynamically injected from `TOOL_MANIFESTS`
  by `_inject_risk_prefixes()` in `tools/manifests.py`.
- DO NOT manually write `[READ]` in tool docstrings — the injection layer handles it.
- To set a tool's risk level, add an entry to `TOOL_MANIFESTS` via `register_manifest()`
  or `auto_register_all_read_tools()`.
- Reference: `ref.mcp-server-standards`, Canonical Template 5a.

### Exception Handler Tests [TEST-REG-3]

- Every tool wrapper's `except Exception` block MUST have a corresponding unit test.
- Pattern: patch the internal `_do_*` function with `side_effect=RuntimeError("msg")`,
  call the tool, assert `data["success"] is False` and error text matches.
- Reference: MCP Server Architect standard, Canonical Template 14.
- Example: see `tests/unit/test_automations.py::TestExceptionHandler`.

### AFDS Documentation Standard

- All documentation files in `docs/` conform to AI-First Documentation Standard.
- `afds_config.yaml` — project-specific validator configuration in repository root.
- Validate governed docs: `make docs-check`
- `README.md` and `CHANGELOG.md` are explicit AFDS exceptions: README keeps normal user-facing Markdown and CHANGELOG follows Keep a Changelog; both remain subject to their separate repository checks.
- Reference: `scripts/vendor/afds_validate_b54fc6b2.py` (vendored validator pinned to `b54fc6b27ea80b36a70d5de73445970e17f55789` in `ai-skills.lock.yaml`)

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

- **Modes:** `offline` (filesystem only and network-disabled), `online` (API required), `hybrid` (local plus available API sources)
- **Completeness contract:** every supported source is recorded as complete, partial, unavailable, skipped, or policy-excluded.
- **Safe snapshot:** discovers local storage/configuration plus supported REST and WebSocket sources, including calendar events, to-do items, and advertised weather forecasts.
- **Isolation:** configuration is immutable per run and passed through a context-local runtime; do not mutate module globals or `os.environ` during generation.
- **Limits:** preserve source, output, history, logbook, calendar, process-deadline, redaction, and atomic-publication bounds.

---

## Common Pitfalls

1. **Generation configuration is per-run:** create `GenerationConfig` in the composition root and use `generation_scope()`. Do not update `constants`, mutate `os.environ`, or reuse credentials between tasks.

2. **`_get_automation_by_id_or_alias` needs strings:** Pass `None` → crash. Always validate `automation_id` before calling internal helpers.

3. **`list_automations` response:** Must include `id` field (unique_id from automations.yaml) so clients can call `get_automation_code`.

4. **Fixture resolution:** Pytest auto-discovers only `conftest.py` files, not package
   init markers. Put test fixtures in `conftest.py`.

5. **Mock MCP pattern:** Unit tests use `MagicMock` with a custom `tool` decorator that stores tools in `mcp._tools[func.__name__]`. Tools are called by awaiting the stored tool with the arguments dict passed as keyword arguments.

6. **Response format:** Every tool MUST return `{"success": True/False, ...}`. Some older tools (get_lovelace_dashboards, get_persons, get_zones, get_hacs_data, trigger_health_report) historically returned plain JSON — always verify with curl after writing a new tool.

7. **Parameter naming consistency:** Use snake_case for all parameters. `read_file` uses `file_path` (not `path`), matching `read_config_file(file_path=...)`. Keep parameter names consistent between similar tools.

8. **UI-created automations:** `_load_automations()` only reads the HA automations
   YAML file. UI-created automations exist only in HA state engine. Tools like
   `get_automation_usage_stats` must fall back to searching `/api/states` for
   `automation.*` entities.

9. **Smoke test response format check:** `tests/smoke/test_response_format.py` iterates all tools and verifies the `success` field. Tools that require parameters return HTTP 400 `INVALID_ARGUMENTS` and are skipped automatically; heavy environment-dependent tools are listed in `_KNOWN_ENV_FAIL`.

10. **Integration conftest:** New tool modules must be registered in `tests/integration/conftest.py` (import + `register_*_tools()` call) or integration tests won't find them.

---

## Definition of Done

A change is complete only when:

- Every new tool has a registered manifest, unit tests, and a smoke test, and the
  manifest validates against the pinned canonical schema.
- `ruff check .`, `ruff format --check .`, `mypy server.py tools/ --strict`,
  and `bandit -r server.py tools/ context_generator/ ha_graph/ -ll` all pass.
- `pytest tests/unit/ tests/protocol/ -q` passes and no test in the same process
  depends on state mutated by another test.
- Backend suites were executed against the running deployment when credentials and
  a server are available: `pytest tests/smoke/ -q`, `pytest tests/e2e/ -q`,
  `pytest tests/integration/ -q`.
- `pre-commit run --all-files` and `make docs-check` pass without skips, and `CHANGELOG.md` records the change under the unreleased section.
- For every newly supported Home Assistant REST/WebSocket surface, official documentation establishes the API shape, the intended LLAT class succeeds against a real instance, a sanitized recorded upstream cassette covers the request, and protocol/smoke coverage verifies catalog/capability consistency.

Report the exact revision, the gates that were executed, skipped checks, and any
residual risk before completion.

---

## Pre-commit Hook (MANDATORY)

This project uses `pre-commit` to run the same checks as CI before every commit:

```bash
# One-time setup
pip install pre-commit semgrep
pre-commit install

# Run before every commit
pre-commit run --all-files
```

### What the hook checks (in order)

| Hook | Stage | Purpose |
|------|-------|---------|
| trailing-whitespace | pre-commit | Remove trailing whitespace |
| check-yaml | pre-commit | Validate YAML syntax |
| end-of-file-fixer | pre-commit | Ensure files end with newline |
| Ruff check | pre-commit | Lint Python code (E, F, I, W) |
| Ruff format | pre-commit | Format Python code |
| mypy strict | pre-commit | Static type checking |
| Bandit | pre-commit | Security scanning |
| Version sync | pre-commit | Detect version.py vs pyproject.toml drift |
| Semgrep | pre-commit | Security patterns (p/auto+p/secrets+p/owasp-top-ten) |
| CAFDS docs | pre-commit | Documentation quality (AFDS validation) |
| Unit tests | pre-commit | Test failures (`pytest tests/unit/ -q`) |

### Agent Workflow

1. **Before committing**: Run `pre-commit run --all-files`
2. If ANY check fails: fix the issue immediately, do NOT skip
3. Re-run `pre-commit run --all-files` until all pass
4. Only then stage and commit

If you cannot install `pre-commit` (e.g., restricted environment), at minimum run the same commands manually in the order shown above. These checks are NOT optional — CI runs the same checks and will reject a commit that bypasses the hook.

### Error Handling: Fix, Don't Bypass

When a pre-commit hook or CI check fails:

- **NEVER** add `--ignore` flags to pytest to skip failing test files
- **NEVER** add `grep -v` or `|| true` to suppress error output
- **NEVER** skip hooks by commenting them out or removing them from the config

**ALWAYS** fix the underlying issue:
- Syntax errors → fix the code (e.g., old-style `except X, Y:` → `except (X, Y):` for Python 3.13+)
- Missing imports → install the correct package version
- Failing tests → fix the test or the code it tests
- Slow tests → profile and optimize, don't skip

The pre-commit hook is the gatekeeper. If it passes locally, CI passes remotely. Bypassing it guarantees CI rejection.
