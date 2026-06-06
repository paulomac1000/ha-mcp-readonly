# ha-mcp-readonly — Implementation Tasks

> Generated: 2026-06-02 | For: AI Agent | Source: Real debugging session analysis

---

## Priority Summary

| Priority | Count | Items |
|----------|-------|-------|
| HIGH | 5 | M1, M4, M2, N1, N2 |
| MEDIUM | 3 | M3, W1, N3 |
| LOW | 3 | N4, N5, N6 |

---

## PART 1 — NEW TOOLS (6)

---

### N1: `diagnose_seasonal_guards` (HIGH)

**What:** Composite diagnostic that evaluates all seasonal mode guards, heating/cooling force logic, and detects conflicts — in a single MCP call.

**Why:** Today debugging a "cooling triggered during pre_winter" requires:
1. `check_entities_batch` for seasonal modes + force binaries
2. `get_state` for temperatures + targets
3. `get_automation_traces` for AC Power Manager decisions
4. `eval_template` for heating_force_logic + cooling_force_logic
5. Manual cross-referencing

That's 5+ MCP calls and ~3000 tokens. This tool does it in 1 call with ~300 tokens of output.

**Input:**
```
None — reads live state from HA
```

**Output schema:**
```python
{
  "success": True,
  "seasonal_modes": {
    "summer_mode": "off",
    "winter_mode": "off", 
    "pre_winter_mode": "on",
    "heating_manual_override": "off",
    "cooling_manual_override": "off",
    "winter_active": True,   # computed: winter_mode OR pre_winter_mode
    "summer_active": False,  # computed: summer_mode
  },
  "ac_power_state": {
    "fuse_on": True,
    "compressor_current": 0.06,
    "cooldown_active": False,
    "eco_pct": 83.29,
    "cheapest_5h": True,
    "cheap_hour": False,
    "cheap_morning": False,
    "outdoor_temp": 21.5,
  },
  "zones": {
    "livingroom": {
      "temperature": 22.2,
      "heating_target": 22.5,
      "cooling_target": 23.0,
      "heating_force": False,     # heating_force_logic() result
      "cooling_force": False,     # cooling_force_logic() result
      "is_too_hot": False,        # temp >= cooling_urgent_threshold
      "is_cold_inside": False,    # delta >= 0.8 for heating
      "needs_heating": False,     # delta > 0
      "needs_cooling": False,     # delta > 0.5
      "window_open": False,       # from BT entity
    },
    "bedroom": {
      # same structure as livingroom
      "temperature": 23.7,
      "heating_target": 22.0,
      ...
    }
  },
  "guard_decisions": {
    "should_boot_heating": {
      "result": True,
      "blocked_by": None,  # or "summer_active" / "cooldown" / "data_invalid"
      "reason": "force_heating=True + eco=83.29 + cheapest_5h",
    },
    "should_shutdown_heating": {
      "result": False,
      "blocked_by": None,
      "reason": "force_heating=True prevents shutdown",
    },
    "should_boot_cooling": {
      "result": False,
      "blocked_by": "summer_off",  # summer_active=False
      "reason": "Cooling blocked: summer_mode is off",
    },
    "should_shutdown_cooling": {
      "result": False,
      "blocked_by": "winter_active",  # our fix!
      "reason": "Cooling shutdown blocked: winter_active=True guard",
    },
  },
  "conflicts": [
    # Empty if no conflicts. Example entries:
    # {"severity": "warning", "message": "should_shutdown_cooling=True but winter_active=True — AC would be shut down for cooling while heating needs it. Guard check: winter_active guard is MISSING."}
    # {"severity": "error", "message": "not_valid_hvac_mode — BT bedroom supports [heat,off] but blueprint tries 'cool'"}
  ],
  "recommendations": [
    # Example: "Bedroom at 23.7°C exceeds target 22.0°C. Force heating is off (correct). AC in idle."
  ],
  "bt_status": {
    "bedroom_bt_valid": True,      # has_value + state in ['heat']
    "livingroom_bt_valid": False,  # has_value + state in ['heat']
    "bedroom_bt_state": "heat",
    "livingroom_bt_state": "off",
    "needs_recovery": ["livingroom"],  # List of zones where BT is in wrong state
  }
}
```

**Implementation notes:**
- Use `make_ha_request()` to call `/api/states` for live state
- Use `HomeAssistantLoader` to read `heating_macros.jinja` from disk
- Evaluate heating_force_logic and cooling_force_logic via internal Jinja2 rendering (same as ha-mcp-readonly's `test_template` internal logic)
- Compute `should_boot`, `should_shutdown`, `should_boot_cooling`, `should_shutdown_cooling` using the EXACT same logic as the AC Power Manager automation variables
- The `conflicts` detection logic:
  - If `should_shutdown_cooling=True AND winter_active=True` → WARNING: "cooling shutdown would cut AC during heating"
  - If `should_shutdown_heating=True AND summer_active=True` → WARNING: "heating shutdown would cut AC during cooling"  
  - If `should_boot_cooling=True AND winter_active=True AND summer_active=False` → CRITICAL: "cooling boot triggered without summer mode (string-truthy bug?)"
- File: `tools/diagnostics.py` (new) or add to `tools/system.py`

**Edge cases:**
- `sensor.thermostat_bedroom_virtual_force` may reference non-existent `sensor.temperature_bedroom_avg` → use `sensor.temphum_bedroom_temperature` as fallback
- BT entities may be unavailable → set `bt_status.*_valid = False`
- `sensor.energy_price_composite` may be missing → default to `0.80`
- Template sensor defaults: `states(entity)` returns `None` → `| float(21)` gives 21.0 for heating, `| float(23)` for cooling

---

### N2: `search_config_references` (HIGH)

**What:** Reverse dependency lookup — finds all places a given entity_id is referenced in HA configuration.

**Why:** Today finding where `sensor.temperature_bedroom_avg` is used requires grep over multiple directories. This tool pinpoints every reference in one call.

**Input:**
```python
{
    "entity_id": "sensor.temperature_bedroom_avg",  # required
    "search_types": ["template", "automation", "script"],  # default: all
    "include_context": True,  # show surrounding lines
}
```

**Output schema:**
```python
{
  "success": True,
  "entity_id": "sensor.temperature_bedroom_avg",
  "total_references": 1,
  "references": [
    {
      "type": "template",           # "template" | "automation" | "script"
      "entity_id": "sensor.thermostat_bedroom_virtual_force",
      "entry_id": "01KDNK26FK91150Q9ZKNAC8RZC",
      "field": "state_template",     # which field contains the reference
      "file": "N/A (storage)",       # "automations.yaml" for automations, "N/A (storage)" for template entries
      "line": None,                  # line number if from YAML file
      "context": "{% from 'heating_macros.jinja' import heating_force_logic %}\n          {{ heating_force_logic('sensor.temperature_bedroom_avg', ...",
    }
  ],
  "entity_exists": False,  # Whether the referenced entity actually exists in HA
}
```

**Implementation notes:**
- Search template entities: iterate over `get_template_entities()` (or config entries with domain="template"), read each state_template, grep for entity_id
- Search automations: read `automations.yaml` with `HomeAssistantLoader`, grep each automation's YAML for entity_id
- Search scripts: same as automations but read `scripts.yaml`
- Check `entity_exists` via HA API: `GET /api/states/{entity_id}`
- File: `tools/config.py`

**Edge cases:**
- Entity referenced in Jinja2 macro parameters (e.g., `heating_force_logic('sensor.temp', ...)`) — must match the template entity_id pattern
- Entity used in `states('entity_id')` function calls
- Entity used in `is_state('entity_id', ...)` function calls
- Entity used in variable references (like `{{ entity_id }}`)

---

### N3: `diagnose_hvac_conflicts` (MEDIUM)

**What:** Detects `not_valid_hvac_mode` patterns — compares hvac_modes of Better Thermostat entities, blueprint automations, and native Gree devices.

**Why:** Today a `not_valid_hvac_mode` error flooded the HA event loop, blocking all API access. This tool would have caught the mismatch earlier.

**Input:**
```
None — auto-discovers BT entities and their TRV devices
```

**Output schema:**
```python
{
  "success": True,
  "bt_entities": [
    {
      "bt_entity_id": "climate.bedroom_ac_better_thermostat",
      "bt_hvac_modes": ["heat", "off"],
      "trv_entity_id": "climate.9424b84a5cd6",
      "trv_hvac_modes": ["auto", "cool", "dry", "fan_only", "heat", "off"],
      "compatibility": {  # which modes BT can set that TRV accepts
        "heat": "ok",      # BT has heat, TRV has heat → ok
        "cool": "missing_in_bt",  # TRV has cool, BT doesn't → BT can't set cool
        "heat_cool": "missing_in_trv",  # BT might try heat_cool, TRV doesn't have it
      },
      "blueprint_automations_using_bt": [
        {
          "alias": "Heating - Bedroom AC - Advanced Control",
          "entity_id": "automation.advanced_heating_control_bedroom_ac",
          "hvac_mode_comfort": "heat",
          "hvac_mode_eco": "heat",
          "valid": True,  # Both heat modes exist in BT hvac_modes
        },
        {
          "alias": "Heating - Cooling Control - Bedroom AC",
          "entity_id": "automation.advanced_cooling_control_bedroom_ac",
          "hvac_mode_comfort": "cool",
          "hvac_mode_eco": "cool",
          "valid": False,  # cool not in BT hvac_modes!
          "error": "BT bedroom_ac supports [heat, off] but automation uses hvac_mode: cool",
        }
      ]
    }
  ],
  "conflicts": [
    {
      "severity": "error",
      "bt_entity": "climate.bedroom_ac_better_thermostat",
      "automation": "Heating - Cooling Control - Bedroom AC",
      "message": "Automation uses hvac_mode 'cool' but BT only supports [heat, off]"
    }
  ],
  "recommendations": [
    "Change cooling automation to target native Gree directly (climate.9424b84a5cd6, hvac_mode: cool) instead of BT",
    "OR recreate BT config entry to include 'cool' in hvac_modes"
  ]
}
```

**Implementation notes:**
- Discover BT entities: search `get_states_filtered(domains="climate")` for entities with `hvac_modes` in attributes filtered by BT naming pattern (`*better_thermostat*`)
- For each BT entity, find the TRV via BT config entry data (which stores the wrapped climate entity)
- For each BT entity, search automations that target it in `input_trvs` field
- Compare hvac_mode inputs against BT's actual hvac_modes
- File: `tools/diagnostics.py`

---

### N4: `eval_template_with_macros` (LOW)

**What:** Evaluates a Jinja2 template that imports a macro from a file, AND simultaneously evaluates the same logic inline. Flags when results differ (stale macro cache).

**Why:** Today `heating_macros.jinja` was edited, `template.reload` was called, but template sensors still used the OLD compiled version. The macro returned `True` while inline logic returned `False` — no tool detected this.

**Input:**
```python
{
    "template": "{% from 'heating_macros.jinja' import heating_force_logic %}\n{{ heating_force_logic('sensor.temphum_bedroom_temperature', ...) }}",
    "inline_equivalent": "{% set is_pre_winter = is_state('input_boolean.pre_winter_mode', 'on') %}\n... (same logic without macro import)",
    "timeout": 3,  # seconds
}
```

**Output schema:**
```python
{
  "success": True,
  "macro_result": "True",
  "inline_result": "False",
  "match": False,  # KEY FIELD: True if results match, False if macro cache is stale
  "warning": "⚠️ STALE MACRO CACHE: Macro returns 'True' but inline equivalent returns 'False'. The Jinja2 FileSystemLoader cache still holds the compiled (old) version of heating_macros.jinja. A full HA restart is required to clear the cache.",
  "macro_cache_stale": True,  # Convenience boolean
}
```

**Implementation notes:**
- Render the macro template using HA's Jinja2 environment (same as `test_template`)
- Render the inline_equivalent separately
- Compare string results
- Just report mismatch; do NOT attempt to fix (requires HA restart)
- File: `tools/template.py`

---

### N5: `get_automation_entity_id` (LOW)

**What:** Resolve automation alias → entity_id. Thin wrapper.

**Why:** `search_automations` returns alias but NOT entity_id. Getting trace via `get_automation_traces` requires entity_id. Today requires grep over automations.yaml.

**Input:**
```python
{
    "identifier": "Enhanced Smart Control - AC Power Manager"  # alias or partial match
}
```

**Output schema:**
```python
{
  "success": True,
  "alias": "Enhanced Smart Control - AC Power Manager (Predictive Standby Control v2)",
  "entity_id": "automation.enhanced_smart_control_air_conditioner_power_manager_winter_standby",
  "unique_id": "1768599267582",
}
```

**Implementation notes:**
- Search HA entity registry for `automation.*` entities
- Match by `friendly_name` (alias) using partial case-insensitive match
- Return first match if multiple; include `matches_count` if ambiguous
- File: `tools/automation.py`

---

### N6: `diagnose_bt_stale_hvac_modes` (LOW)

**What:** Detects when Better Thermostat has stale/cached hvac_modes (after HA restart where TRV was unavailable during BT init).

**Why:** After today's fix, BT was created while Gree was unavailable, causing BT to cache empty hvac_modes → `mode_remap` incorrectly translated `heat` → `heat_cool` → flooded event loop.

**Input:**
```
None — auto-discovers BT entities
```

**Output schema:**
```python
{
  "success": True,
  "bt_entities": [{
    "bt_entity_id": "climate.bedroom_ac_better_thermostat",
    "bt_hvac_modes": ["heat", "off"],
    "trv_entity_id": "climate.9424b84a5cd6",
    "trv_hvac_modes": ["auto", "cool", "dry", "fan_only", "heat", "off"],
    "trv_available": True,
    "stale_detected": False,  # True if BT modes don't make sense vs TRV
    "stale_reason": None,  # e.g., "BT missing 'cool' but TRV has 'cool' — BT may have been initialized while TRV was unavailable"
    "fix_hint": "Reload BT config entry via homeassistant.reload_config_entry" if stale else None,
  }]
}
```

**Implementation notes:**
- Same discovery logic as N3
- Compare BT hvac_modes against what the TRV actually supports
- If TRV has modes not present in BT, flag as potentially stale
- File: `tools/diagnostics.py`

---

## PART 2 — MODIFIED TOOLS (5)

---

### M1: `get_automation_traces` — add `detail_level` (HIGH)

**What:** Add `detail_level` parameter to control output verbosity.

**Why:** Currently always returns full execution step-by-step. For "which branch was chosen?" you just need the variables + choice index. Saves ~85% tokens.

**New parameter:**
```python
{
    "automation_id": "automation.xxx",  # required
    "run_id": "abc123",                # optional — get specific trace
    "limit": 10,                       # how many traces to return
    "detail_level": "full",            # NEW: "full" | "steps" | "decision" (default: "full")
}
```

**Output for `detail_level="decision"`:**
```python
{
  "success": True,
  "run_id": "abc123",
  "timestamp": "2026-06-02T08:01:00",
  "trigger": "emergency_boot",
  "state": "stopped",
  "decision": {
    "choice_index": 2,   # which choose branch was selected
    "choice_branch": "should_boot (heating boot)",
    "key_variables": {   # Only the decision-relevant variables
      "should_boot": True,
      "should_boot_cooling": False,
      "should_shutdown": "false",
      "should_shutdown_cooling": "false",
      "force_heating": True,
      "summer_active": False,
      "winter_active": True,
      "is_power_on": False,
      "current_eco_pct": 93.2,
    }
  }
}
```

**Implementation notes:**
- `"full"`: Keep existing behavior (backward compatible — default)
- `"steps"`: Return alias + result for each action step (skip variable dumps)
- `"decision"`: Return only `trigger`, `variables` (filtered to key decision vars), `choice` result
- The `key_variables` filter: keep only variables that appear in `should_boot`, `should_shutdown`, `should_boot_cooling`, `should_shutdown_cooling` conditions
- File: `tools/automation.py`

---

### M2: `search_automations` — add `include_entity_id` (HIGH)

**What:** Optionally include `entity_id` in search results.

**New parameter:**
```python
{
    "search_term": "AC Power Manager",  # existing
    "include_entity_id": True,          # NEW — default: False (backward compatible)
}
```

**Output change:** When `include_entity_id=True`, each result gains:
```python
{
    "alias": "...",
    "entity_id": "automation.enhanced_smart_control_...",  # NEW
    # ... existing fields unchanged
}
```

**Implementation notes:**
- Lookup entity_id from HA entity registry: iterate `automation.*` entities, match by `unique_id` (which is the automation's `id` field)
- If entity not found in registry (disabled?), set `entity_id: null`
- File: `tools/automation.py`

---

### M3: `get_entity` — accept list (MEDIUM)

**What:** Accept `entity_id` as string OR list of strings.

**Why:** Today checking 5 BT/Gree entries requires 5 separate MCP calls. With batch, 1 call.

**Input change:**
```python
# OLD: single string only
{"entity_id": "climate.bedroom_ac_better_thermostat"}

# NEW: also accepts list
{"entity_id": ["climate.bedroom_ac_better_thermostat", "climate.9424b84a5cd6"]}
```

**Output change:** When list is provided, return `{entity_id: info_dict, ...}`:
```python
{
  "success": True,
  "results": {
    "climate.bedroom_ac_better_thermostat": { ... },
    "climate.9424b84a5cd6": { ... },
  },
  "not_found": [],  # entity_ids from the list that don't exist
}
```

**Implementation notes:**
- Detect if `entity_id` is list or string (use `isinstance(entity_id, list)`)
- For single string: keep existing behavior (backward compatible)
- For list: make parallel HA API calls (or use batch endpoint if available)
- File: `tools/entity.py`

---

### M4: `get_state` / `get_entity` — add `compact` mode (HIGH)

**What:** Add `compact=True` parameter to reduce output to essential fields.

**Why:** Most diagnostics need just `state` + `last_changed` + `friendly_name`. Full attributes waste tokens. Today `get_state` on Gree entity returns 30+ attributes unnecessarily.

**Parameter:**
```python
{
    "entity_id": "climate.9424b84a5cd6",  # required
    "compact": True,                        # NEW — default: False (backward compatible)
}
```

**Output change (compact mode):**
```python
{
  "entity_id": "climate.9424b84a5cd6",
  "state": "heat",
  "friendly_name": "Klimatyzator 9424b84a5cd6",
  "last_changed": "2026-06-02T08:00:00",
  # attributes stripped EXCEPT: hvac_modes, hvac_action, current_temperature, temperature
  # These are the only attributes an AI agent typically needs
}
```

**Which attributes to KEEP in compact mode:**
- `climate.*`: `hvac_modes`, `hvac_action`, `current_temperature`, `temperature`
- `switch.*`: (no attributes needed, just state)
- `sensor.*`: `unit_of_measurement`, `device_class`
- `binary_sensor.*`: `device_class`
- All others: strip all attributes

**Implementation notes:**
- Apply same logic to `get_entity` (registry info — keep only `area_id`, `disabled_by`, `hidden_by`, `platform`, `device_id`)
- File: `tools/entity.py`

---

### M5: `search_automations` — add `category_filter` (LOW)

**What:** Filter automation search results by category_id.

**New parameter:**
```python
{
    "search_term": "AC",
    "category": "01JNKH53V7YCQMME5AYJRGDH48",  # NEW — catgory_id
}
```

**Implementation notes:**
- Filter after fetching all results (no HA API filter for category)
- Match `category_id` against the automation's category field in entity registry
- File: `tools/automation.py`

---

## PART 3 — GLOBAL PATTERNS (3)

---

### W1: `detail_level` pattern rollout (MEDIUM)

Apply the `detail_level` parameter to these tools:
- `get_overview` — `"minimal"` (default), `"standard"`, `"full"` (already exists, verify)
- `get_history` — `"summary"` (only state changes), `"full"` (with attributes)
- `diagnose_automation` — `"minimal"` (just problems), `"summary"` (key data), `"full"` (everything)
- `list_automations` — `"summary"` (alias + mode only), `"full"` (all metadata)

Add `detail_level` parameter to the `@mcp.tool()` decorator parameter list and docstring.

File: multiple files under `tools/`

---

### W2: `include_*` pattern rollout (MEDIUM)

Apply `include_*` boolean parameters to:
- `search_entities` — `include_state=True` (live state alongside registry info)
- `get_device` — `include_entities=True` (entity list with device)
- `get_integration` — `include_options=True` (config entry options)
- `list_automations` — `include_trigger_count=True`

File: multiple files under `tools/`

---

### W3: `compact` mode rollout (MEDIUM)

Apply `compact` parameter to:
- `get_history` — `compact=True` strips attributes from state entries
- `search_entities` — `compact=True` returns only entity_id + state

File: `tools/history.py`, `tools/entity.py`

---

## PART 4 — DOCUMENTATION (3)

---

### D1: README.md
- Increment tool count (add new tools to total)
- Add rows for N1–N6 in the tool table
- Add note about `detail_level`, `compact`, `include_*` params

### D2: docs/documentation.md
- Full documentation block for each new tool (N1–N6)
- Update documentation for modified tools (M1–M5)
- Add "Parameter Patterns" section explaining `detail_level`, `compact`, `include_*`

### D3: CHANGELOG.md
- Add `[Unreleased]` section with all new tools, modifications, and patterns
- Follow `Keep a Changelog` format: Added, Changed, Fixed

---

## IMPLEMENTATION ORDER (RECOMMENDED)

```
Round 1 (MOST IMPACT, LEAST EFFORT):
  M1 (detail_level on traces)     ~30 min
  M4 (compact mode)               ~20 min

Round 2 (HIGH IMPACT):
  N1 (diagnose_seasonal_guards)   ~90 min
  M2 (include_entity_id)          ~15 min

Round 3 (BATCH + DIAGNOSTICS):
  M3 (batch get_entity)           ~30 min
  N2 (search_config_references)   ~60 min

Round 4 (NICE TO HAVE):
  N3 (diagnose_hvac_conflicts)    ~45 min
  N4 (eval_template_with_macros)  ~45 min

Round 5 (FINISHING):
  N5, N6, M5, W1-W3, D1-D3       ~120 min

TOTAL ESTIMATED: ~8 hours
```

---

## TESTING REQUIREMENTS

For each new tool, create tests in `tests/unit/test_<module>.py`:

1. **Happy path** — valid input returns `success: True`
2. **Missing input** — returns `success: False` with meaningful error message
3. **Edge case 1** — entity not found
4. **Edge case 2** — invalid parameters

For each modified tool:
1. **Backward compatibility** — existing behavior unchanged when new param is default
2. **New behavior** — works correctly with new param

Run: `python3 -m pytest tests/unit/ -v --tb=short`
Verify: no coverage drop > 2%.

---

## KEY CONSTANTS AND REFERENCES

**File locations:**
- AC Power Manager: `data/hassio/automations.yaml` (alias: "Enhanced Smart Control - AC Power Manager (Predictive Standby Control v2)")
- Heating macros: `data/hassio/custom_templates/heating_macros.jinja`
- BT entity IDs to know: `climate.livingroom_ac_better_thermostat`, `climate.bedroom_ac_better_thermostat`
- Native Gree IDs: `climate.502cc6fa1b44` (livingroom), `climate.9424b84a5cd6` (bedroom)

**Seasonal guard logic (from heating_macros.jinja):**
```python
# Heating force in pre_winter:
Path A: is_cold_inside AND eco <= 120 AND NOT is_guest_sleeping
Path B: is_cheap_morning AND eco < 140 AND NOT is_guest_sleeping
Path C: needs_heating AND is_occupied AND (is_cheap_hour OR eco < 80)
Path D: needs_heating AND is_occupied AND is_cheapest_5h AND eco < 110

# Cooling force:
Gate 1: cooling_manual_override → True
Gate 2: not summer → False
Gate 3: is_urgent (temp >= 27°C) → True
... (see cooling_force_logic macro)

# AC Power Manager should_shutdown_cooling:
not is_power_on → false
winter_active → false  ← CRITICAL GUARD
summer_not_active → true
compressor_min_protection → false
else: not cooling_force AND not is_too_hot
```

**Common entity IDs for testing:**
- Seasonal modes: `input_boolean.summer_mode`, `input_boolean.winter_mode`, `input_boolean.pre_winter_mode`
- Override: `input_boolean.cooling_manual_override`, `input_boolean.heating_manual_override`
- Temperatures: `sensor.temphum_bedroom_temperature`, `sensor.temperature_livingroom_avg`
- Heating targets: `sensor.thermostat_bedroom_virtual_calibrated`, `sensor.thermostat_livingroom_virtual_calibrated`
- Cooling targets: `sensor.thermostat_bedroom_virtual_cooling_calibrated`, `sensor.thermostat_livingroom_virtual_cooling_calibrated`
- Force binaries: `binary_sensor.thermostat_bedroom_virtual_force_binary`, `binary_sensor.thermostat_livingroom_virtual_force_binary`
