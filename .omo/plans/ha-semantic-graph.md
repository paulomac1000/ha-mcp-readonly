# HA Semantic Graph — Phase 1 MVP

## TL;DR

> **Quick Summary**: Add a read-only HA Semantic Graph layer (`ha_graph/`) to ha-mcp-readonly that maps entity relationships as typed graph edges (triggers_on, reads, controls, displays) with confidence scoring, backed by an in-memory cache with zero new pip dependencies.
>
> **Deliverables**:
> - New package `ha_graph/` (7 files): models, extractors, scanner, cache, queries, export
> - 7 new MCP tools: `graph_build_index`, `graph_find_references`, `graph_entity_impact`, `graph_get_neighbors`, `graph_detect_ghost_references`, `graph_detect_orphans`, `graph_export_mermaid`
> - 5 test files + integration conftest update
> - `server.py` registration call
>
> **Estimated Effort**: Large (~3-5 days sequential, ~2-3 days parallel)
> **Parallel Execution**: YES — 3 Waves
> **Critical Path**: Wave 1 (models+extractors) → Wave 2 (scanner+cache) → Wave 3 (tools+tests)

---

## Context

### Original Request
Implement a "HA Semantic Graph" — a domain-specific dependency graph for Home Assistant configurations. The graph maps relationships between entities, automations, scripts, scenes, dashboards, devices, areas, and integrations using typed edges and confidence scoring.

### Source Document
[todo2.md] — Comprehensive 2539-line analysis including external tool research (CodeGraph, EntityMap), implementation complexity estimates (4 levels), architectural model design, and full Phase 1 specification.

### Research Findings
- CodeGraph cannot handle HA semantics (YAML-based dependencies, not code symbols)
- EntityMap exists as HA integration but is new/untested in production
- No existing tool provides the full semantic graph for AI agents
- The project already has 70-80% of the raw extraction capability (`entity_dependencies.py`, `context_generator/utils.py`, `AutomationAnalyzer`)
- Current approach does per-entity reverse lookups with no graph structure, no cache, and duplicated extraction logic

### Metis Review
**Identified Gaps** (addressed):
- Async safety of TTL cache → resolved: use `asyncio.Lock()` pattern
- Partial scan failure recovery → resolved: scan per-file, aggregate errors, return partial graph with warnings
- Circular import risk → resolved: `ha_graph/` imports from `tools/utils.py`/`tools/yaml_utils.py` only, never reverse
- Code duplication → resolved: Phase 1 deduplicates extraction into `ha_graph/extractors.py`
- Memory budget for large instances → resolved: Phase 1 targets user's 176-file instance; scaling is Phase 2+

### Momus Review
**Verdict**: PENDING — will run after plan generation if high-accuracy mode selected.

---

## Work Objectives

### Core Objective
Deliver the Phase 1 MVP of a read-only HA Semantic Graph: an in-memory dependency graph with typed edges, confidence scoring, cache, and 7 MCP query tools — all built from existing YAML config and registry files with zero new pip dependencies.

### Concrete Deliverables
- `ha_graph/models.py` — `GraphNode`, `GraphEdge`, `GraphIndex` dataclasses
- `ha_graph/extractors.py` — unified entity/service extraction (moved from `context_generator/utils.py` + enhanced)
- `ha_graph/scanner.py` — `HomeAssistantGraphScanner` for YAML + registries
- `ha_graph/cache.py` — async-safe TTL cache (300s default)
- `ha_graph/queries.py` — graph traversal, reference lookup, impact analysis, ghost/orphan detection
- `ha_graph/export.py` — Mermaid subgraph export
- `ha_graph/__init__.py` — public API exports
- `tools/graph_tools.py` — 7 MCP tool registrations
- `server.py` — `register_graph_tools()` call
- `tests/unit/test_graph_models.py` — model tests
- `tests/unit/test_graph_extractors.py` — extraction tests
- `tests/unit/test_graph_scanner.py` — scanner tests
- `tests/unit/test_graph_queries.py` — query tests
- `tests/unit/test_graph_tools.py` — tool integration tests
- `tests/integration/conftest.py` — graph tool registration

### Definition of Done
- [ ] `ha_graph/` package imports cleanly without errors
- [ ] All 7 graph tools visible in MCP tool list (`describe_ha_capabilities`)
- [ ] `pytest tests/unit/ -q` → all existing tests pass + new graph tests pass
- [ ] `ruff check .` → zero errors
- [ ] `get_entity_dependencies` output unchanged (backward compat)
- [ ] Graph built from test fixtures produces correct node/edge counts
- [ ] Coverage ≥ 85% on `ha_graph/`

### Must Have
- Read-only — graph is pure analysis, no writes to HA or filesystem
- Typed edges with semantic relations (triggers_on, reads, controls, displays, calls_service, belongs_to, from_integration)
- Confidence scoring for all edges (exact, inferred, dynamic, weak)
- In-memory TTL cache (async-safe) — no persisted files in /config
- Works without HA API (offline mode: automations.yaml, scripts.yaml, scenes.yaml, .storage/ registries, dashboards)
- 7 MCP tools following existing tool patterns (`_success_response`, `register_*_tools()`)
- Zero new pip dependencies (stdlib only: dataclasses, pathlib, re, json, asyncio, time)

### Must NOT Have (Guardrails)
- No NetworkX, SQLite, `ruamel.yaml`, or any new Python dependency
- No writes to `/config` directory (cache is in-memory only)
- No changes to existing tool behavior (`get_entity_dependencies`, `get_entity_consumers`, etc.)
- No WebSocket connections — filesystem + HTTP REST API only
- No HTML visualization, interactive graph, or D3 export (Phase 4 only)
- No blueprint input resolution or Jinja macro expansion (Phase 3+)
- No `!include`/package directory resolution (Phase 2 only)
- No MQTT, command_line, rest, or shell_command integration scanning
- No REST API endpoints for graph data (Phase 5 only)
- No dynamic Jinja resolution beyond `states()`, `is_state()`, `state_attr()` — unknown patterns get `confidence: dynamic, target: null`

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: Tests-after (test files written alongside implementation code)
- **Framework**: pytest (existing — `asyncio_mode = "auto"`, mock MCP pattern)
- **Test tier**: Unit (mocked, no HA required) + Integration (T15 only)
- **Coverage**: `--cov=ha_graph --cov-report=term-missing`, target ≥ 85%

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.omo/evidence/task-{N}-{scenario-slug}.{ext}`.

- **CLI/Backend**: Use Bash (pytest) — Run tests, assert output, check exit codes
- **API**: Use Bash (curl) — Call MCP tools via REST API, assert JSON response fields
- **Code Quality**: Use Bash (ruff) — Check lint errors

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — models + extractors, parallel):
├── Task 1: ha_graph/models.py — GraphNode, GraphEdge, GraphIndex [quick]
├── Task 2: ha_graph/extractors.py — unified extraction [deep]
└── Task 3: ha_graph/__init__.py + imports setup [quick]

Wave 2 (Core Engine — scanner + cache + queries + export, parallel after Wave 1):
├── Task 4: ha_graph/scanner.py — HomeAssistantGraphScanner [deep]
├── Task 5: ha_graph/cache.py — async-safe TTL cache [quick]
├── Task 6: ha_graph/queries.py — graph traversal algorithms [deep]
└── Task 7: ha_graph/export.py — Mermaid subgraph export [quick]

Wave 3 (Integration — tools + core tests, parallel):
├── Task 8: tools/graph_tools.py — 7 MCP tool registrations [deep]
├── Task 9: tests/unit/test_graph_models.py [quick]
├── Task 10: tests/unit/test_graph_extractors.py [quick]
├── Task 11: tests/unit/test_graph_scanner.py [deep]
├── Task 12: tests/unit/test_graph_queries.py [deep]
└── Task 13: tests/unit/test_graph_tools.py [deep]

Wave 4 (Finalize — cache tests + integration + verification):
├── Task 14: tests/unit/test_ha_graph_cache.py [quick]
├── Task 15: integration conftest + server.py registration [quick]
└── Task 16: Full test suite verification + ruff check [quick]

Critical Path: T1 → T4 → T8 → T16
Parallel Speedup: ~60% faster than sequential
Max Concurrent: 4 (Wave 2), 3 (Wave 1)
```

### Agent Dispatch Summary

- **Wave 1**: 3 tasks — T1,T3→`quick`, T2→`deep`
- **Wave 2**: 4 tasks — T4→`deep`, T5→`quick`, T6→`deep`, T7→`quick`
- **Wave 3**: 6 tasks — T9,T10→`quick`, T8,T11,T12,T13→`deep`
- **Wave 4**: 3 tasks — T14,T15,T16→`quick`

---

- [x] 1. `ha_graph/models.py` — GraphNode, GraphEdge, GraphIndex dataclasses

  **What to do**:
  - Create `ha_graph/models.py` with frozen dataclasses:
    - `GraphNode(id, type, name, metadata)` — type is `Literal["entity","automation","script","scene","dashboard","device","area","integration","service","file","template","blueprint","helper","unknown"]`
    - `GraphEdge(source, target, relation, confidence, file_path, object_path, line, evidence, metadata)` — relation is `Literal["triggers_on","reads","controls","calls_service","calls_script","activates_scene","displays","belongs_to_device","belongs_to_area","from_integration","defined_in","includes","uses_blueprint","has_entity","via_device","unknown_reference"]`
    - `GraphIndex(nodes: dict, edges: list, built_at: float, stats: dict)` — with `add_node()`, `add_edge()`, `outgoing(node_id) -> list[GraphEdge]`, `incoming(node_id) -> list[GraphEdge]` methods
  - Node IDs use prefixed format: `entity:binary_sensor.motion`, `automation:doorbell_alert`, etc.
  - Edge target can be `None` for dynamic references
  - Add `__init__.py` that re-exports: `GraphNode, GraphEdge, GraphIndex, NodeType, RelationType, Confidence`

  **Must NOT do**:
  - Do NOT import NetworkX or any graph library
  - Do NOT add persistence/serialization methods (Phase 2+)

  **Recommended Agent Profile**: `quick`

  **Parallelization**: Wave 1 (with T2, T3)

  **References**:
  - Todo2.md lines 385-1013 — complete model specification
  - `tools/entity_dependencies.py` — current flat output format (for contrast with graph model)

  **Acceptance Criteria**:
  - [x] `GraphNode(id="entity:light.hall", type="entity")` creates valid node
  - [x] `GraphEdge(source="automation:x", target="entity:light.hall", relation="controls")` creates valid edge
  - [x] `GraphEdge(source="automation:x", target=None, relation="reads", confidence="dynamic")` handles null target
  - [x] `index.add_edge(edge)` followed by `index.outgoing("automation:x")` returns the edge
  - [x] `index.incoming("entity:light.hall")` returns edges pointing to it

  **QA Scenarios**:
  ```
  Scenario: GraphIndex builds and queries correctly
    Tool: Bash (pytest)
    Steps:
      1. Create unit test: build index with 3 nodes and 4 edges
      2. Assert outgoing("automation:a") returns 2 edges
      3. Assert incoming("entity:light.hall") returns 1 edge
    Expected Result: Graph traversals work, counts match
    Evidence: tests/unit/test_graph_models.py (included in T9)
  ```

  **Commit**: YES (Wave 1)
  - Message: `feat(ha_graph): add GraphNode, GraphEdge, GraphIndex models`

- [x] 2. `ha_graph/extractors.py` — unified entity/service extraction

  **What to do**:
  - Create `ha_graph/extractors.py` moving extraction logic from `context_generator/utils.py` and `tools/entity_dependencies.py` into one canonical location
  - Functions to include (adapted for edge generation):
    - `extract_entities_from_template(template_str) -> list[tuple[str, Confidence]]` — returns `(entity_id, confidence)` tuples
    - `extract_entities_from_data(data) -> set[str]` — extracts entity IDs from nested YAML/dict structures
    - `extract_trigger_info(triggers) -> list[tuple[str, str]]` — returns `(entity_id, platform)` tuples
    - `extract_services(actions) -> set[str]` — returns service names like `light.turn_on`
    - `extract_controlled_entities(actions) -> set[str]` — returns entities targeted in service calls
  - Enhanced beyond current: add `expand()` detection, `area_entities()` detection, `device_entities()` detection (with `confidence: inferred`)
  - Dynamic Jinja patterns: `{{ 'sensor.' ~ room ~ '_temperature' }}` → `confidence: dynamic, target: null`
  - Word-boundary entity regex: `\b(domain\.[a-zA-Z0-9_-]+)\b` to avoid substring false positives
  - Domain list from todo2.md ~70 domains, kept current from HA docs

  **Must NOT do**:
  - Do NOT import from `context_generator/` (avoid circular imports) — move logic, don't delegate
  - Do NOT add `ruamel.yaml` dependency
  - Do NOT resolve dynamic templates — return `confidence: dynamic, target: null`

  **Recommended Agent Profile**: `deep`

  **Parallelization**: Wave 1 (with T1, T3)

  **References**:
  - `context_generator/utils.py` — current `extract_entities_from_template()`, `extract_entities_from_data()`, `extract_trigger_info()`, `extract_services()`, `extract_controlled_entities()`
  - `tools/entity_dependencies.py` — `_extract_entities_from_template()`, `_extract_entities_from_data()` copies
  - Todo2.md lines 1014-1136 — extraction specification

  **Acceptance Criteria**:
  - [x] `extract_entities_from_template("{{ states('sensor.temp') }}")` returns `[("sensor.temp", "inferred")]`
  - [x] `extract_entities_from_template("{{ states('sensor.' ~ room) }}")` returns `[]` (dynamic, no hard target)
  - [x] `extract_entities_from_data({"entity_id": "light.hall"})` returns `{"light.hall"}`
  - [x] `extract_entities_from_data({"service": "light.turn_on", "target": {"entity_id": "light.bed"}})` finds `light.bed`
  - [x] `extract_trigger_info([{"platform": "state", "entity_id": "binary_sensor.motion"}])` returns `[("binary_sensor.motion", "state")]`
  - [x] `extract_services([{"action": "light.turn_on"}]` returns `{"light.turn_on"}`
  - [x] `extract_controlled_entities([{"action": "light.turn_on", "target": {"entity_id": "light.hall"}}])` returns `{"light.hall"}`

  **QA Scenarios**:
  ```
  Scenario: Template extraction handles both static and dynamic
    Tool: Bash (pytest)
    Steps:
      1. Import extract_entities_from_template from ha_graph.extractors
      2. Pass "{{ states('sensor.temp') }} and {{ states('sensor.' ~ room) }}"
      3. Assert returns [("sensor.temp", "inferred")] — dynamic pattern NOT matched
    Expected Result: Only static references extracted, dynamic filtered
    Evidence: tests/unit/test_graph_extractors.py
  ```

  **Commit**: YES (Wave 1)
  - Message: `feat(ha_graph): add unified entity/service extraction`

- [x] 3. `ha_graph/__init__.py` — public API + imports verification

  **What to do**:
  - Create `ha_graph/__init__.py` with clean public API:
    ```python
    from ha_graph.models import GraphNode, GraphEdge, GraphIndex, NodeType, RelationType, Confidence
    from ha_graph.extractors import (extract_entities_from_template, extract_entities_from_data,
        extract_trigger_info, extract_services, extract_controlled_entities)
    from ha_graph.cache import get_graph_index, build_graph_index, GRAPH_CACHE_TTL
    from ha_graph.queries import (find_entity_references, entity_impact, get_neighbors,
        detect_ghost_references, detect_orphans)
    from ha_graph.export import export_mermaid
    ```
  - Add `__all__` list
  - Verify: `python3 -c "import ha_graph; print(ha_graph.__all__)"` succeeds

  **Must NOT do**:
  - Do NOT import from `tools/` in `__init__.py` (keeps the package self-contained)
  - Do NOT add module-level code that runs on import (no registry loads, no file I/O)

  **Recommended Agent Profile**: `quick`

  **Parallelization**: Wave 1 (with T1, T2)

  **References**: Standard Python package conventions

  **Acceptance Criteria**:
  - [ ] `python3 -c "import ha_graph"` succeeds without errors
  - [ ] `ha_graph.__all__` lists all public symbols

  **QA Scenarios**:
  ```
  Scenario: Package imports cleanly
    Tool: Bash
    Steps:
      1. python3 -c "import ha_graph; print(len(ha_graph.__all__))"
      2. Assert exit code 0, output is a number ≥ 5
    Expected Result: Package imports without error
    Evidence: .omo/evidence/task-3-import-check.txt
  ```

  **Commit**: NO (groups with T1, T2 in Wave 1 commit)

---

- [x] 4. `ha_graph/scanner.py` — HomeAssistantGraphScanner

  **What to do**:
  - Create `ha_graph/scanner.py` with `build_graph_index(config_path, ha_url=None, ha_token=None) -> GraphIndex`
  - `HomeAssistantGraphScanner` class with `scan(index)` method calling sub-scanners:
    1. `scan_registries(index)`: entity, device, area, config_entry registries from `.storage/`
    2. `scan_automations(index)`: `automations.yaml` → nodes + edges
    3. `scan_scripts(index)`: `scripts.yaml` → nodes + edges
    4. `scan_scenes(index)`: `scenes.yaml` → nodes + edges
    5. `scan_dashboards(index)`: `.storage/lovelace*` → nodes + edges
    6. `scan_configuration(index)`: `configuration.yaml` → file nodes
  - For each automation: extract triggers → `triggers_on` edges, conditions → `reads` edges, actions → `controls` + `calls_service` edges
  - For each script: extract actions → same edges as automations
  - For each scene: extract entities → `controls` edges
  - For each dashboard: extract card entities → `displays` edges
  - Handle missing files gracefully: if `scripts.yaml` doesn't exist, skip with log message, don't crash
  - Handle corrupt YAML: `try/except` per file, aggregate `warnings` list in `index.stats`
  - Offline mode: when `ha_url`/`ha_token` are None, skip API enrichment (entity states), use registry data only
  - Compute stats after scan: node count, edge count, by-type breakdown
  - Use `load_yaml_file` from `tools/yaml_utils.py` for YAML parsing, `load_registry` from `tools/utils.py` for registries

  **Must NOT do**:
  - Do NOT resolve `!include` directives (Phase 2 only)
  - Do NOT scan blueprint instances or MQTT/shell/rest_command integrations
  - Do NOT write anything to disk
  - Do NOT crash on corrupt YAML — return partial graph with `warnings`

  **Recommended Agent Profile**: `deep`

  **Parallelization**: Wave 2 (with T5, T6, T7) — blocked by T1, T2

  **References**:
  - Todo2.md lines 1137-1280 — full scanner specification
  - `tools/entity_dependencies.py:24-208` — existing file scanning patterns
  - `tools/yaml_utils.py` — `load_yaml_file()` for YAML parsing
  - `tools/utils.py` — `load_registry()` for registry files
  - `context_generator/analyzers.py` — `AutomationAnalyzer` entity extraction patterns

  **Acceptance Criteria**:
  - [x] `build_graph_index(test_config_path)` returns GraphIndex with nodes > 0 and edges > 0
  - [x] Graph contains `entity:*` nodes from entity registry
  - [x] Graph contains `automation:*` nodes with `triggers_on` edges to entities
  - [x] Graph contains `service:*` nodes with `calls_service` edges from automations/scripts
  - [x] Scenes with entity snapshots produce `controls` edges
  - [x] Dashboards with entity cards produce `displays` edges
  - [x] Missing file (e.g., `scripts.yaml` doesn't exist) does not crash — returns partial graph
  - [x] Corrupt YAML file does not crash — returns partial graph with `warnings` in stats
  - [x] `build_graph_index(path, ha_url=None, ha_token=None)` works in offline mode

  **QA Scenarios**:
  ```
  Scenario: Scanner handles missing optional files
    Tool: Bash (pytest)
    Steps:
      1. Set up test config with automations.yaml only (no scripts.yaml, no scenes.yaml)
      2. Call build_graph_index(test_config_path)
      3. Assert: graph is built successfully, node count reflects only automations + registries
      4. Assert: no crash, no error
    Expected Result: Partial graph built, missing files gracefully skipped
    Evidence: tests/unit/test_graph_scanner.py

  Scenario: Scanner handles corrupt YAML
    Tool: Bash (pytest)
    Steps:
      1. Write invalid YAML to automations.yaml (e.g., "{{ invalid")
      2. Call build_graph_index(test_config_path)
      3. Assert: graph is built, warnings list in stats is non-empty
    Expected Result: Partial graph with warnings, no crash
    Evidence: tests/unit/test_graph_scanner.py
  ```

  **Evidence to Capture**:
  - [x] tests/unit/test_graph_scanner.py — all scanner tests

  **Commit**: YES (Wave 2)
  - Message: `feat(ha_graph): add HomeAssistantGraphScanner`

- [x] 5. `ha_graph/cache.py` — async-safe TTL cache

  **What to do**:
  - Create `ha_graph/cache.py` with async-safe TTL cache:
    ```python
    from ha_graph.models import GraphIndex
    from ha_graph.scanner import build_graph_index as _build_graph_index
    import asyncio, time

    _GRAPH_CACHE: GraphIndex | None = None
    _GRAPH_CACHE_TS: float = 0
    _GRAPH_LOCK = asyncio.Lock()
    GRAPH_CACHE_TTL = 300  # seconds

    async def get_graph_index(config_path: str, ha_url: str | None = None, ha_token: str | None = None, force: bool = False) -> GraphIndex:
        now = time.time()
        async with _GRAPH_LOCK:
            if not force and _GRAPH_CACHE is not None and now - _GRAPH_CACHE_TS < GRAPH_CACHE_TTL:
                return _GRAPH_CACHE
            _GRAPH_CACHE = _build_graph_index(config_path, ha_url, ha_token)
            _GRAPH_CACHE_TS = now
            return _GRAPH_CACHE
    ```
  - Use `asyncio.Lock()` instead of `threading.Lock()` (FastMCP runs in asyncio event loop)
  - `build_graph_index` is the non-cached version exported separately (for tests/callers that want fresh build always)

  **Must NOT do**:
  - Do NOT write cache to disk
  - Do NOT use `threading.Lock()` — must be async-compatible

  **Recommended Agent Profile**: `quick`

  **Parallelization**: Wave 2 (with T4, T6, T7)

  **References**:
  - `context_generator/utils.py` — existing TTL cache pattern (for reference, adapt to asyncio)
  - Todo2.md lines 1473-1507 — cache specification

  **Acceptance Criteria**:
  - [x] `get_graph_index(config_path)` returns GraphIndex
  - [x] Second call within TTL returns cached GraphIndex (no rebuild)
  - [x] `get_graph_index(config_path, force=True)` forces rebuild
  - [x] `build_graph_index(config_path)` always builds fresh (non-cached)

  **QA Scenarios**:
  ```
  Scenario: Cache returns cached graph within TTL
    Tool: Bash (pytest)
    Steps:
      1. Call get_graph_index(config_path) → record index
      2. Call get_graph_index(config_path) again within TTL
      3. Assert: same object returned (no rebuild)
    Expected Result: Second call returns cached graph
    Evidence: tests/unit/test_ha_graph_cache.py
  ```

  **Commit**: NO (groups with T4, T6, T7 in Wave 2 commit)

- [x] 6. `ha_graph/queries.py` — graph traversal algorithms

  **What to do**:
  - Create `ha_graph/queries.py` with 5 query functions:
    1. `find_entity_references(index, entity_id) -> list[dict]` — returns all edges pointing to entity_id, with source name, relation, confidence, file_path, object_path
    2. `entity_impact(index, entity_id) -> dict` — categorizes incoming edges into: automations_triggered_by, automations_reading, automations_controlling, scripts_controlling, dashboards_displaying
    3. `get_neighbors(index, node_id, depth=1, direction="both") -> dict` — returns subgraph of nodes within `depth` hops; direction: "incoming", "outgoing", "both"
    4. `detect_ghost_references(index) -> list[str]` — entity_ids referenced in config but not in entity registry
    5. `detect_orphans(index, ignorable_domains=None) -> list[str]` — entity_ids in registry with no incoming edges (not triggered_on, read, controlled, or displayed), excluding ignorable domains
  - `get_neighbors` BFS: queue-based traversal, cycle detection via visited set, depth limit
  - `detect_ghost_references`: compare `{e.target for e in index.edges}` against `{n.id for n in index.nodes.values() if n.type == "entity"}`
  - `detect_orphans`: entities with no incoming edges, filter out ignorable domains (sun, update, persistent_notification, etc.)

  **Must NOT do**:
  - Do NOT add BFS pathfinding (graph_explain_path) in Phase 1 — that's Phase 2
  - Do NOT add full-graph export (graph_export_json) — that's Phase 2

  **Recommended Agent Profile**: `deep`

  **Parallelization**: Wave 2 (with T4, T5, T7)

  **References**:
  - Todo2.md lines 1281-1472 — query specifications
  - `tools/composite.py` — `audit_config_orphans` existing orphan detection
  - `context_generator/constants.py` — `IGNORABLE_DOMAINS` for orphan filtering

  **Acceptance Criteria**:
  - [x] `find_entity_references(index, "entity:light.hall")` returns list of incoming edges
  - [x] `entity_impact(index, "entity:light.hall")` categorizes by relation type
  - [x] `get_neighbors(index, "automation:x", depth=2)` returns correct subgraph
  - [x] `detect_ghost_references(index)` finds entities in edges but not in nodes
  - [x] `detect_orphans(index)` finds entities with zero incoming edges (excluding sun/update domains)

  **QA Scenarios**:
  ```
  Scenario: entity_impact categorizes correctly
    Tool: Bash (pytest)
    Steps:
      1. Build graph with automation that triggers on and controls same entity
      2. Call entity_impact(index, "entity:light.hall")
      3. Assert: "automations_triggered_by" has 1, "automations_controlling" has 1
    Expected Result: Impact analysis correctly separates trigger vs control
    Evidence: tests/unit/test_graph_queries.py

  Scenario: ghost references detection
    Tool: Bash (pytest)
    Steps:
      1. Build graph where automation references sensor.missing but registry doesn't have it
      2. Call detect_ghost_references(index)
      3. Assert: result contains "sensor.missing"
    Expected Result: Ghost entities detected
    Evidence: tests/unit/test_graph_queries.py
  ```

  **Commit**: NO (groups with T4, T5, T7 in Wave 2 commit)

- [x] 7. `ha_graph/export.py` — Mermaid subgraph export

  **What to do**:
  - Create `ha_graph/export.py` with `export_mermaid(index, node_id=None, depth=2) -> str`
  - `node_id` is REQUIRED (never export full graph — would be unreadable for LLM)
  - BFS from `node_id` up to `depth` hops, collect nodes + edges
  - Format as Mermaid `graph TD` with node labels as `A["friendly_name"]` mapped by UUID
  - Edge labels: `-->|triggers_on|`, `-->|controls|`, `-->|reads|`, `-->|displays|`
  - Max 50 nodes in output (enforced limit to prevent token explosion)
  - Friendly names from `GraphNode.name` attribute

  **Must NOT do**:
  - Do NOT allow full-graph export (no node_id = error)
  - Do NOT generate HTML/JavaScript/D3/Cytoscape
  - Do NOT exceed 50 nodes in output

  **Recommended Agent Profile**: `quick`

  **Parallelization**: Wave 2 (with T4, T5, T6)

  **References**:
  - Todo2.md lines 1660-1675 — Mermaid export specification
  - https://mermaid.js.org/ — Mermaid syntax reference

  **Acceptance Criteria**:
  - [x] `export_mermaid(index, node_id="entity:light.hall")` returns valid Mermaid string
  - [x] `export_mermaid(index)` raises `ValueError` (node_id required)
  - [x] Output starts with `graph TD`
  - [x] Output contains `-->|` edges
  - [x] Output does NOT exceed 50 nodes

  **QA Scenarios**:
  ```
  Scenario: Subgraph export is readable
    Tool: Bash (python3)
    Steps:
      1. Build graph from test fixtures
      2. Call export_mermaid(index, node_id="automation:x", depth=1)
      3. Assert: output starts with "graph TD", contains node labels
      4. Assert: output line count ≤ 60 (50 nodes + 10 edges max)
    Expected Result: Valid Mermaid subgraph
    Evidence: .omo/evidence/task-7-mermaid.md
  ```

  **Commit**: NO (groups with T4, T5, T6 in Wave 2 commit)

---

- [x] 8. `tools/graph_tools.py` — 7 MCP tool registrations

  **What to do**:
  - Create `tools/graph_tools.py` with `register_graph_tools(mcp, config_path, ha_url, ha_token)` function
  - Register 7 tools: `graph_build_index`, `graph_find_references`, `graph_entity_impact`, `graph_get_neighbors`, `graph_detect_ghost_references`, `graph_detect_orphans`, `graph_export_mermaid`
  - Follow existing tool patterns: `_success_response`/`_error_response`, `except Exception: return _error_response(str(e))`, docstrings with Args/Returns

  **Acceptance Criteria**:
  - [x] All 7 tools appear in `describe_ha_capabilities` output
  - [x] `graph_build_index()` returns `{nodes_count, edges_count, built_at, stats}` with nodes_count ≥ 0
  - [x] `graph_find_references("light.hall")` returns list of references
  - [x] `graph_export_mermaid(node_id="entity:light.hall")` returns Mermaid string, missing node_id returns error
  - [x] All 7 tools have exception handler (Template 14): patch internal with RuntimeError → `success: False`
  - [x] All tools have `[READ]` prefix via auto-registration

  **Recommended Agent Profile**: `deep`
  **Parallelization**: Wave 3
  **Commit**: YES (Wave 3) — `feat(tools): add 7 graph_* MCP tools`

- [x] 9. `tests/unit/test_graph_models.py` — model unit tests

  **What to do**: Test GraphNode/GraphEdge creation, immutability, GraphIndex.add_node/add_edge/outgoing/incoming. 5-8 tests.

  **Acceptance Criteria**:
  - [x] 5+ tests, all pass
  - [x] Coverage on `ha_graph/models.py` ≥ 95%
  - [x] Tests include: node with null name, edge with null target (dynamic refs), duplicate node overwrite, empty graph traversals

  **Recommended Agent Profile**: `quick`
  **Parallelization**: Wave 3
  **Commit**: NO (groups with Wave 3)

- [x] 10. `tests/unit/test_graph_extractors.py` — extraction unit tests

  **What to do**: Test extract_entities_from_template (static, dynamic, states(), is_state(), state_attr(), expand()), extract_entities_from_data, extract_trigger_info, extract_services, extract_controlled_entities. 15-20 tests.

  **Acceptance Criteria**:
  - [ ] 15+ tests, all pass
  - [x] Coverage on `ha_graph/extractors.py` ≥ 85%
  - [x] Tests include: empty template string, None input, malformed structures, dynamic Jinja (`'sensor.' ~ room`), multiple refs in one template

  **Recommended Agent Profile**: `quick`
  **Parallelization**: Wave 3
  **Commit**: NO (groups with Wave 3)

- [x] 11. `tests/unit/test_graph_scanner.py` — scanner unit tests

  **What to do**: Test build_graph_index with complete mock config (automations.yaml, scripts.yaml, scenes.yaml, .storage/, dashboards). Test missing files, corrupt YAML, offline mode, empty config. 8-12 tests.

  **Acceptance Criteria**:
  - [x] 8+ tests, all pass
  - [x] Coverage on `ha_graph/scanner.py` ≥ 80%
  - [x] Tests include: full scan produces correct node/edge counts, missing scripts.yaml → partial graph, corrupt automations.yaml → partial graph + warnings, offline mode (ha_url=None) works

  **QA Scenarios**:
  ```
  Scenario: Full scan produces correct graph structure
    Tool: Bash (pytest)
    Steps:
      1. Set up config with: 2 automations, 1 script, 1 scene, 1 dashboard, 5 entities in registry
      2. Call build_graph_index(config_path)
      3. Assert: automation nodes = 2, script nodes = 1, entity nodes ≥ 5, edges > 0
    Expected Result: All config elements as typed nodes + edges
    Evidence: tests/unit/test_graph_scanner.py
  ```

  **Recommended Agent Profile**: `deep`
  **Parallelization**: Wave 3
  **Commit**: NO (groups with Wave 3)

- [ ] 12. `tests/unit/test_graph_queries.py` — query unit tests

  **What to do**: Test find_entity_references, entity_impact, get_neighbors (depth/direction), detect_ghost_references, detect_orphans (including ignorable domain filtering). 12-15 tests.

  **Acceptance Criteria**:
  - [ ] 12+ tests, all pass
  - [ ] Coverage on `ha_graph/queries.py` ≥ 85%
  - [ ] Tests include: entity with refs vs without, impact categorization, depth=1 vs depth=3 subgraph, cycle in graph → BFS handles it, ghost detection finds missing entities, orphan detection with ignorable domains filtered

  **Recommended Agent Profile**: `deep`
  **Parallelization**: Wave 3
  **Commit**: NO (groups with Wave 3)

- [ ] 13. `tests/unit/test_graph_tools.py` — MCP tool integration tests

  **What to do**: Register graph tools via mock MCP, test each of 7 tools. Test exception handlers. Test backward compat. 15-20 tests.

  **Acceptance Criteria**:
  - [ ] 15+ tests, all pass
  - [ ] All 7 tools have exception handler test (Template 14): patch internal with RuntimeError → `success: False`
  - [ ] Backward compat: existing tool calls produce unchanged output
  - [ ] Tests include: graph_build_index returns valid stats, graph_find_references for unknown entity returns empty, graph_export_mermaid without node_id returns error

  **Recommended Agent Profile**: `deep`
  **Parallelization**: Wave 3
  **Commit**: NO (groups with Wave 3)

- [ ] 14. `tests/unit/test_ha_graph_cache.py` — cache unit tests

  **What to do**: Test get_graph_index cache behavior, force rebuild, TTL expiry, concurrent access, build_graph_index non-cached variant. 5-8 tests.

  **Acceptance Criteria**:
  - [ ] 5+ tests, all pass
  - [ ] Coverage on `ha_graph/cache.py` ≥ 90%
  - [ ] Tests include: first call builds → second call returns cached, force=True rebuilds, TTL expiry triggers rebuild, concurrent async access doesn't deadlock

  **Recommended Agent Profile**: `quick`
  **Parallelization**: Wave 4 (with T15, T16)

- [ ] 15. Integration conftest + server.py registration

  **What to do**: Add `register_graph_tools()` to `server.py` and `tests/integration/conftest.py`. Verify `python3 server.py` starts without import errors.

  **Acceptance Criteria**:
  - [ ] `server.py` imports without error: `python3 -c "from tools.graph_tools import register_graph_tools; print('OK')"`
  - [ ] `timeout 5 python3 server.py 2>&1 || true` — no ImportError, no ModuleNotFoundError
  - [ ] Integration conftest updated with `register_graph_tools()` call
  - [ ] All 7 graph tools listed in MCP tool catalog (curl to `/api/tools`)

  **Recommended Agent Profile**: `quick`
  **Parallelization**: Wave 4 (with T14, T16)
  **Commit**: NO (groups with Wave 4)

- [ ] 16. Full test suite verification

  **What to do**: Run `pytest tests/unit/ -q --tb=short`, `ruff check .`, coverage check on `ha_graph/` ≥ 85%. Verify backward compat. Fix any failures.

  **Acceptance Criteria**:
  - [ ] All unit tests pass: `pytest tests/unit/ -q --tb=short` → 960+ tests, 0 failures
  - [ ] `ruff check .` → zero errors
  - [ ] `pytest tests/unit/ --cov=ha_graph --cov-report=term-missing` → coverage ≥ 85%
  - [ ] Backward compat: `pytest tests/unit/test_entity_dependencies.py -q` → all pass unchanged
  - [ ] `python3 server.py` starts without import errors (verify with timeout)

  **Recommended Agent Profile**: `quick`
  **Parallelization**: Wave 4 (with T14, T15)
  **Commit**: YES (Wave 4) — `test: add graph tools, integration, server registration`
  - Pre-commit: `pytest tests/unit/ -q && ruff check .`

