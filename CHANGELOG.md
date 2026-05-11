---
description: Release history for HA-MCP-Readonly following Keep a Changelog
---

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - Unreleased

### Added
- `get_automation_file_location(automation_id)` — returns the file path, line_start,
  line_end, and surrounding YAML for an automation in automations.yaml. Eliminates
  manual grep + read steps when inspecting file context around an automation.
- `get_automation_codes_batch(automation_ids)` — batch retrieval of YAML code for
  multiple automations in a single call. Loads automations.yaml once. ~70% token
  savings vs N individual get_automation_code calls.
- AFDS documentation standard adoption — added YAML frontmatter with `description`
  to README.md and CHANGELOG.md, created `afds_config.yaml` validator configuration,
  cleaned up ambiguous language (banned words) in documentation
- AFDS validator improvements — added line number reporting to banned word
  violation messages, implemented tier-based relaxation (L0 skips all section
  checks, L1 requires only the first 2 sections), unified code block removal
  regex between `check_single_h1` and `_blank_code_blocks`

### Tests
- Unit tests: 689 → 701 (+12). 11 new tests for the two new tools across
  test_automations.py and test_batch_operations.py.
- Smoke tests: 84 → 86 (+2). Two new tests in test_critical_tools.py.
- Both tools added to _REQUIRES_PARAMS in test_response_format.py.
- Code coverage tools/ maintained at >85%.

## [1.2.0] - 2026-05-08

### Added
- **`get_template_entity_code(entity_id)`** — new tool returning full Jinja2 template
  code for a single template helper entity. ~95% token savings vs `get_template_entities()`.
- **`get_template_entities(entity_id)`** — added optional filter parameter to return
  a single template instead of all 66+.
- **Test hierarchy v1.0** — 4-tier structure (unit/smoke/integration/e2e):
  - `tests/fixtures.py` — all mock data constants extracted from conftest
  - `tests/unit/conftest.py` — unit test fixtures (MCPWrapper, mock_mcp, config_path)
  - `tests/smoke/` — 84 tests, direct REST API calls, <5s
  - `tests/e2e/` — 24 tests, full pipeline (context generator + REST API + SSE)
- **Context Generator v1.0** — 6 new analyzers:
  - `PersonAnalyzer`, `ZoneAnalyzer`, `EnergyAnalyzer`, `HelperAnalyzer`,
    `ServiceCatalogAnalyzer`, `HacsAnalyzer`
  - 6 existing analyzers extended (domain summary, blueprint stats, lovelace resources,
    exposed entities, per-entity history stats, notifications)
  - 18 output sections (was 12): Persons & Tracking, Zones & Geofencing, Energy,
    Helpers, Services, HACS
  - Context generator env vars re-read at runtime (fixes module import order issue)
  - Output file header updated to v1.0 branding
- **Entity dependencies !include support** — `get_entity_dependencies` now scans
  files referenced by YAML `!include` directives.
- **Documentation:** `AGENTS.md` created with full rule set (language, tests, code
  quality, coverage, common pitfalls). `.env.example` created as configuration template.

### Changed
- **`read_file(path=...)` → `read_file(file_path=...)`** for parameter name consistency
  with `read_config_file(file_path=...)`.
- **`search_automations`** now sorts enabled automations before disabled ones.
- **`get_automation_usage_stats`** added HA API fallback for UI-created automations
  not present in `automations.yaml`.
- **`list_automations`** response now includes `id` field (unique_id from
  automations.yaml).
- **`print()` → `logging`** in `tools/utils.py`, `tools/yaml_utils.py`,
  `tools/health_reporter.py` (17 locations).
- **Docstring gaps** filled with `Args`/`Returns` sections in 13 functions across
  `config.py`, `config_entries.py`, `scripts.py`, `scenes.py`, `storage.py`.
- **Integration conftest** now registers all tool modules (composite, batch_operations,
  filesystem_explorer, entity_context).
- **Context generator** header upgraded from V7 to v1.0, version string in output file.

### Fixed
- **Response format compliance:** `success` field added to 5 tools that were missing
  it: `get_lovelace_dashboards`, `get_persons`, `get_zones`, `get_hacs_data`,
  `trigger_health_report`.
- **`get_automation_code(None)`** — now returns `{"success": False, "error": "..."}`
  instead of HTTP 500. Input validation added.
- **`entity_context` crash** — `(entry.get("message") or "").lower()` prevents
  NoneType crash on logbook entries with null messages.
- **`entity_context.py` dead code** — orphaned dict literal (lines 73-78) prevented
  `filter_entity_id` from being sent to history API.
- **Context Generator constants** — `HA_URL`/`HA_TOKEN`/`HA_CONFIG_PATH` now
  re-read from environment at `main()` runtime, not just at import time.
- **`tests/integration/__init__.py`** — 171 lines of dead code removed (fixtures in
  `__init__.py` are not auto-discovered by pytest).
- **Polish text removed** from ~25 places in tool descriptions, comments, test data.
- **Emoji removed** from 35 tool description first lines and 28 API response strings.
- **Hardcoded names replaced** with generic equivalents in ~35 places across
  conftest.py, test fixtures, and tool docstrings.
- **Culture-specific content** replaced (G12w tariff names, PLN currency, Polish
  recommendations) with generic equivalents.
- **3 typos:** `typeeical` → `typical`, `CONstateTS` → `CONSTANTS`,
  `ROZSZERZONE` → `extended`.
- **Shebang** removed from `context_generator/constants.py` (not a script).

### Tests
- **Unit tests:** 621 → 689 (+68).
- **Integration tests:** 57 → 98 (all passing, +41).
  Added tests for Lovelace, Persons, Zones, Energy, HACS, automation extras,
  config entry diagnostics, entity deps extra, history, batch ops, composite,
  filesystem explorer, health reporter, template entity code.
- **Smoke tests:** Created from scratch — 84 tests covering connectivity,
  critical tools (67 unique tools), response format compliance, and input validation.
- **E2E tests:** Created from scratch — 24 tests covering context generator
  pipeline (all 3 modes), REST API endpoints, and SSE transport.
- **Total tests:** 621 → 895 (+274).
- **Tool coverage:** 88/117 (75%) → 116/117 (99%).
- **Code coverage tools/:** 83% → 89%.

## [1.1.5] - 2026-05-07

### Added
- `get_lovelace_resources()` — lists registered Lovelace resources (custom cards,
  JS modules, CSS) with type breakdown and HACS source detection.
- `search_lovelace_config()` — multi-criteria search across dashboard configs by
  entity_id, card_type, or free-text search_term. Returns card position, view, and
  match criteria. ~90% token savings vs returning full dashboard configs.
- `get_lovelace_config_summary()` — token-efficient dashboard structure overview
  with card type breakdown, view counts, and strategy/YAML mode detection. Supports
  per-dashboard or global view. ~95% token savings.
- `diagnose_lovelace_setup()` — composite Lovelace diagnostics in a single call:
  missing entity references, strategy/YAML mode detection, resource analysis, and
  actionable recommendations. ~85% token savings vs manual multi-call workflow.
- `tests/unit/test_lovelace.py` — 27 tests covering all new endpoints, edge cases
  (empty registries, corrupt files, strategy dashboards, badges, multi-criteria
  search), and backward compatibility.

### Fixed
- `get_lovelace_dashboards()` — corrected registry filename from `lovelace.dashboards`
  (dot) to `lovelace_dashboards` (underscore), matching Home Assistant's actual storage
  convention. Previously returned empty results.
- `get_lovelace_config("lovelace")` — default dashboard resolution now looks up the
  dashboards registry and correctly maps `url_path="lovelace"` → storage file
  `lovelace.lovelace` instead of the non-existent `lovelace` file.

### Changed
- README and documentation expanded to clarify context generator use cases: RAG systems,
  ChatGPT Projects, Qwen, static AI context, and documentation snapshots.
- Source code test coverage increased from 59% to 74% (+183 tests, now 594 total).
  Key improvements: `tools/utils.py`, `tools/yaml_utils.py`, `tools/scenes.py`,
  `tools/scripts.py` reached 100%; `tools/storage.py` 73% → 96%;
  `tools/dev_tools.py` 67% → 83%; `server.py` 66% → 80%;
  `tools/diagnostics.py` 50% → 75%; `context_generator` 3-13% → 28-55%.

### Added
- Tests for `automation_validate_triggers` — validates trigger IDs against their
  handlers in choose/if/parallel blocks, detects orphaned triggers, duplicate IDs,
  and missing handler references.
- Tests for `diagnose_person_tracking` — person state, tracker freshness, zone
  proximity, automation references.
- Tests for `get_area_automation_summary` — area intelligence with device mappings,
  entity breakdown, and automation linking.
- Tests for `server.py` REST API endpoints: tool calling, context generation,
  download, status, OpenAPI schema, error paths.
- Tests for `context_generator` analyzers (`RegistryCollector`, `AutomationAnalyzer`,
  `LogAnalyzer`, `DashboardAnalyzer`, `TemplateEntityCollector`, `HistoryAnalyzer`)
  and formatter (`ReportGenerator`).
- Edge case tests: registry loading errors, YAML fallback tags, corrupt scene/script
  files, storage history stats, empty registries, search filters.

## [1.1.3] - 2026-05-06

### Added
- `diagnose_person_tracking(person_entity)` — composite diagnostic for person entity
  tracking. Aggregates person state, tracker freshness analysis, zone proximity,
  automation references, and health recommendations into a single call. ~85% token
  savings vs separate API calls.
- `read_file` and `read_config_file`: new `offset` parameter (1-indexed line number,
  default 1) to start reading from a specific line. Enables efficient access to
  large files like `automations.yaml` without reading from the beginning.

### Fixed
- `diagnose_template(entity_id)` — now finds UI-created template helpers (config
  entry flow) via entity registry lookup. Previously only matched YAML-defined
  templates, returning "Template code not found" for UI-created helpers. Added
  Unicode-normalized fallback matching for names with diacritics (e.g., "Paweł").

## [1.1.2] - 2026-05-06

### Added
- `read_file` and `read_config_file`: new `offset` parameter (1-indexed line number, default 1) to start reading from a specific line. Enables efficient access to large files like `automations.yaml` without reading from the beginning.

## [1.1.1] - 2026-05-06

### Fixed
- Removed `get_automation_traces` tool — the `/api/trace/context/` endpoint used by this tool requires frontend authentication (not available via Long-Lived Access Token). Tool count corrected to 107.

## [1.1.0] - 2026-05-05

### Added
- New tool: `get_automation_traces` — fetch execution traces for automations/scripts from HA API. Supports listing recent traces and retrieving single trace details by `run_id`.
- CI/CD: `docker-build` job now pushes `sha-<commit>` tagged images to GHCR on every push to `main`.
- CI/CD: `publish.yml` now triggers on successful `workflow_run` of `CI` on `main`, producing `latest` and version-tagged images automatically.

### Changed
- `get_recent_logs` and `get_previous_logs` now return structured JSON instead of raw strings for consistency with other tools. Includes `success`, `lines_returned`, `level_filter`, and `logs` fields.
- Tool count increased from 107 to 108.

### Fixed
- `get_recent_logs` / `get_previous_logs` empty-file handling now returns proper JSON error response instead of plain text.

## [1.0.0] - 2025-04-29

### Added
- Initial release of HA-MCP-Readonly
- 107 read-only MCP tools for Home Assistant observation
- REST API on port 9093 with OpenAPI schema
- MCP SSE transport on port 9092 for AI clients
- Health check endpoint on port 9091
- Context generator with offline/online/hybrid modes
- Docker and Docker Compose support
- Comprehensive unit test suite (417 tests)
- Integration tests for real Home Assistant instances
- Filesystem access restricted to `/config` directory
- Credential redaction in logs and outputs

### Security
- Read-only design: no write operations to Home Assistant
- Auth registry blocked from AI access
- Path traversal protection
- Max file size and directory depth limits
