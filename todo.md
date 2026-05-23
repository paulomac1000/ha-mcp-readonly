# TODO — Improvement Backlog

> Generated from real-world AI agent usage analysis, 2026-05-23.

## Observed Gaps

During an agent session diagnosing Spook warnings and HA repairs, the following
toolchain inefficiencies were identified:

| Symptom | Root Cause | Tools Used (Workaround) |
|---------|------------|------------------------|
| `search_lovelace_config` only scans 2/10 dashboards | 8 dashboards are "strategy", skipped entirely | Full config dump + manual grep |
| `get_entity_consumers` misses dashboard references | Only checks automations/scripts, not `.storage/` | Separate `search_lovelace_config` + `search_config_by_params` |
| No tool to list HA repair issues | Only available in write-enabled `ha-mcp` | `ha_get_system_health(include="repairs")` from write server |
| No filter for entities missing `state_class` | LTS diagnostics require manual entity-by-entity check | Per-entity `get_entity_context` calls |

## Proposed New Tools

### 1. `search_entity_all_contexts(entity_id)` — P1

Composite tool that finds an entity everywhere it's referenced:
automations, scripts, dashboards (all modes), template helpers, groups, scenes,
and configuration YAML files.

- **Token savings:** ~70% vs 3+ separate calls
- **HA API:** `states/` + `lovelace/` + `automations.yaml` + `scripts.yaml` + config search
- **Difficulty:** Medium

### 2. `bulk_search_lovelace_config(entity_ids)` — P1

Batch search for multiple entity IDs across all dashboards in a single call.
Pattern already exists for `bulk_search_entities` / `check_entities_batch`.

- **Token savings:** ~85% vs N individual `search_lovelace_config` calls
- **Difficulty:** Low (existing pattern)

### 3. `get_repairs_list(domain, severity)` — P2

Wrapper on HA's repair issue registry. Returns active issues with optional
filtering by integration domain and severity level.

- **Token savings:** ~90% vs `get_system_health(include="repairs")`
- **Difficulty:** Low

### 4. `get_states_filtered` — add `missing_state_class` param — P2

Extend existing `get_states_filtered` with `missing_state_class=true` to
quickly find entities that lost their `state_class` (common after HA upgrades).

- **Difficulty:** Low (existing tool extension)

## Quick Wins (Low Effort)

- [ ] Add `missing_state_class` filter to `get_states_filtered`
- [ ] Add `bulk_search_lovelace_config` using existing batch pattern
- [ ] Add `get_repairs_list` (simple HA API wrapper)
- [ ] Extend `get_entity_consumers` to also scan dashboard storage files

## Tool Count Impact

Current: 134 tools (122 in CI). Proposed additions: +3-4 tools.

---

*Last updated: 2026-05-23*
