# Learnings

## Docstring Mismatches Fixed

- `get_automation_dependencies`: Docstring said "services, blueprints" but actual return key is `scenes` (not `services`) and `uses_blueprint` (not `blueprints` under dependencies). Fixed docstring and Returns section to match reality.
- `entity_get_context_tree`: Returns section said `recent_changes` was a "list of recent state changes with sources" but it's actually an object with `total_history_entries`, `total_logbook_entries`, `last_changed`, `last_updated`. Fixed to match.

## 2025-06-10: make_ha_request timeout increases for history/logbook

- Added `timeout=30` (from default 10s) to all `/api/history/period/` and `/api/logbook/` calls
- Files modified: `tools/history.py`, `tools/entity_context.py`, `tools/composite.py`, `tools/automations.py`, `tools/diagnostics.py`
- `/api/states` calls left at default 10s (unchanged)
- Default timeout in `make_ha_request` signature (`tools/utils.py:124`) not modified
- Ruff check passed with zero warnings

## 2026-06-10: Pagination (limit/offset) added to 4 registry tools

- Added `limit` (default 200) and `offset` (default 0) to `_do_get_entity_registry`, `_do_get_device_registry`, `_do_get_area_registry`, `_do_get_config_entries`
- Response includes `_meta.truncated` and `_meta.total_count` when the full dataset exceeds the paginated slice
- No structure change when results fit within limit (backward compatible)
- MCP tool wrappers expose the new params; defaults maintain existing behavior
- Unit test with 5,000 mock entities verifies truncation

## 2026-06-10: Dead code removal

- Deleted `tools/validators.py` (101 lines, 0 callers in production code) and its test file
- Removed wasted `load_registry("automations.yaml", config_path)` call in `entity_context.py:91` (result unused)
- Removed dead `include_context` parameter from `_do_get_entity_dependencies` and `get_entity_dependencies` tool (never used in function body)
- Removed dead `timeout` parameter from `_do_test_template` in `dev_tools.py:28` (never used in function body, caller always passed `None`)
- Cleaned up unused `load_registry` import from `entity_context.py`
- Verified: 1089 unit tests pass, ruff check clean

## 2026-06-10: data_quality field added to 3 composite tools

- Extended the data_quality pattern (from `_do_audit_config_orphans` Task 4) to:
  - `_do_get_entity_with_automations` — tracks `registry`, `automations`, `states_api`
  - `_do_investigate_entity` — tracks `registry`, `automations`, `states_api`, `history`
  - `_do_get_area_diagnostic` — tracks `registry`, `states_api`, `automations` (only when `include_automations=True`)
- Pattern: when all sources succeed → `{"overall": "complete"}`; otherwise per-source status with `_error` suffix for messages
- `_do_get_area_diagnostic` needed `auto_warn: str | None = None` initialized before the `if include_automations:` block to avoid NameError
- `_do_investigate_entity` needed `history_success: bool | None = None` initialized before the history fetch block
- Unit tests (9 new) verify data_quality for all three tools under complete, states_api-failed, automations-failed, and history-failed scenarios
- 44 tests pass, ruff check clean

## 2026-06-10: _read_log_file capped at 10,000 lines + HA API fallback for get_log_insights

- `_read_log_file` now returns `tuple[list[str] | None, dict[str, object]]` instead of `list[str] | None`
- When `max_lines` is `None`, defaults to `10000` and reads last N lines via `tail_log_file` (most recent data)
- `_meta` dict includes: `source` ("log_file" or "api_fallback"), `truncated` (bool), `max_lines` (int)
- `_do_get_log_insights` gains HA API fallback: tries `/api/error_log` first, then `/api/logbook`, when log file not found
- `register_log_tools` now accepts `ha_url` and `ha_token` params (default empty), passed to `_do_get_log_insights`
- `server.py` updated to pass `HA_URL` and `HA_TOKEN` to `register_log_tools`
- All 8 callers of `_read_log_file` updated to unpack tuple (7 use `_` for meta, 1 passes meta to result)
- `_meta` added to `get_log_insights` response (passed through `_success_response` as part of data dict)
- 8 new unit tests: 4 for `_read_log_file` truncation, 1 for `_meta.source`, 3 for API fallback scenarios
- 26 total log tests pass, 1115 unit tests pass overall, ruff check clean

- Added `get_cache_stats()` tool in `tools/storage.py` wrapping `utils.get_registry_cache_stats()` — returns hits, misses, blocked, total, hit_rate_percent, cached_keys
- Registered manifest via `register_manifest("get_cache_stats", make_manifest(...))` with latency="fast"
- Added entity_registry cache invalidation alongside existing config_entries invalidation in `_do_get_template_entity_code` (`storage.py` line 1316): `invalidate_registry_cache("core.entity_registry", config_path)` triggers when `force_reload=True`
- Imported `get_registry_cache_stats` at module level in `storage.py` (alongside existing imports from `tools.utils`)
- Unit tests: 2 tests for `get_cache_stats` (returns stats, exception handler), 3 tests for force_reload invalidation (verifies both registries invalidated, no-op on default, exact call count)
- 5 new tests pass, 78 total storage tests pass, ruff check clean

## 2026-06-10: Final cleanup pass (dead code, suppressed exceptions, error patterns)

- **categories.py:101**: Removed redundant `str()` wrapper from `_error_response(str(data.get("error", data)))` → `_error_response(data.get("error", data))`. The `_error_response` function already accepts `str | dict`, so `str()` was unnecessary.
- **health_reporter.py:398-399**: Added `_logger.exception("trigger_health_report failed")` before `_error_response(str(e))` in the tool wrapper's except block. Documented the unique `_do_*` → `_success_response` pattern at `_do_trigger_health_report` — `run_once()` does its own caching/error handling, returning a fully-formed response dict.
- **batch_operations.py:431**: Replaced silent `except Exception: continue` (same as `pass` in context of template file scanning) with `except Exception: _logger.debug(...) ; continue` — at minimum logs a debug message for failed YAML parse attempts.
- **entity_context.py**: Removed unused `_build_history_url` import (identified by `ruff`), added missing `from typing import Any` (used in type annotations but never imported).
- **ruff**: Sorted imports in `automations.py` and `states.py` via `ruff check --fix`. Reformatted 3 files via `ruff format`.
- Verified: 1142 unit tests pass (excluding pre-existing `test_server.py` import error), `ruff check tools/` clean, `ruff format --check` clean.
