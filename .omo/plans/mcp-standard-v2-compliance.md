# MCP Server Architect v2.0 — Compliance Plan for ha-mcp-readonly

## TL;DR

> **Quick Summary**: Fix 5 compliance gaps against MCP Server Architect standard v2.0. Health endpoints missing `tool_count`, progressive discovery not implemented for 122+ tools, and category-level grouping absent.
>
> **Deliverables**:
> - Enhanced health endpoints (ports 9091 + 9093) with `tool_count` + `tools_version`
> - `tools/list?detail=minimal` endpoint (names + one-liners < 2000 tokens)
> - `tools/get_schema` endpoint for full schema on demand
> - Category-level tool grouping in `describe_ha_capabilities`
>
> **Estimated Effort**: Medium (~2-3 hours)
> **Parallel Execution**: YES — 2 waves
> **Critical Path**: Wave 1 (health + progressive discovery) → Wave 2 (verify)

---

## Context

### Audit Results
Audit performed 2026-06-06 against `mcp-server-standards.md` v2.0.0. Found 5 compliance gaps.

### Project Classification
- **Type**: Python/FastMCP, read-only MCP server
- **Tool count**: 122+ tools (L3+ progressive discovery required)
- **Transport**: SSE (port 9092) + REST API (port 9093) + Health (port 9091)
- **Current compliance**: Already L2+ on most rules (two-layer pattern, response contract, manifests, consumer ergonomics, test hierarchy, security)

### Already compliant (20+ rules verified)
- Two-layer pattern ✓ | try/except Exception ✓ | _success_response/_error_response ✓
- Risk prefix injection ✓ | describe_ha_capabilities ✓ | `/api/tools/{tool}/manifest` ✓
- Consumer ergonomics (batch, compact, detail_level) ✓ | Test hierarchy ✓
- Three-port architecture ✓ | English only, no emoji ✓ | `.env` gitignored ✓
- `/api/tools` returns both `total` AND `tool_count` ✓

---

## Work Objectives

### Core Objective
Achieve L3+ MCP Server Architect compliance by implementing progressive tool discovery and fixing health endpoint metadata.

### Concrete Deliverables
- `server.py` — Health endpoints enhanced with `tool_count` + `tools_version`
- `server.py` — `tools/list?detail=minimal` endpoint (category-grouped, under 2000 tokens)
- `server.py` — `tools/get_schema` endpoint (full tool schema on demand)
- `tools/capabilities.py` — Category-level grouping in describe_ha_capabilities
- `tests/unit/test_server.py` — Updated health endpoint tests

### Must Have
- Health endpoints return `tool_count` and `tools_version`
- `tools/list?detail=minimal` returns names + one-liners, fits under 2000 tokens
- `tools/get_schema` returns full tool definition on demand
- Category-level grouping for 122+ tools
- All existing tests pass (1065+)

### Must NOT Have
- No breaking changes to existing tool APIs
- No new Python dependencies
- No changes to MCP SSE transport (keep SSE as primary)
- No WebSocket or Streamable HTTP addition (deferred to v2.1)

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: Tests-after
- **Framework**: pytest
- **QA**: `pytest tests/unit/ -q`, ruff check

---

## TODOs

- [x] 1. **Enhance health endpoint (port 9091)** — add `tool_count` + `tools_version`

  **What to do**: Modify `HealthHandler.do_GET` in `server.py`. After `HEALTH_STATE` and `invocations`, add:
  ```python
  payload["tool_count"] = get_tool_count()
  payload["tools_version"] = __version__
  ```
  Import `get_tool_count` from `tools/manifests.py` (or use the existing import).

  **Acceptance Criteria**:
  - [ ] `curl http://localhost:9091/health` returns `tool_count` (int) and `tools_version` (str)
  - [ ] `tool_count` matches actual tool count

  **Agent Profile**: `quick`
  **Wave**: 1

- [x] 2. **Enhance REST health endpoint (port 9093)** — add `tool_count`

  **What to do**: Modify the Starlette health route in `server.py`. Add `"tool_count": get_tool_count()` to the health response JSON.

  **Acceptance Criteria**:
  - [ ] `curl http://localhost:9093/api/health` returns `tool_count` field
  - [ ] `tool_count` matches actual tool count

  **Agent Profile**: `quick`
  **Wave**: 1

- [x] 3. **Implement `tools/list?detail=minimal`** — progressive discovery endpoint

  **What to do**: Add a query parameter `detail` to the existing `list_tools_endpoint` in `server.py`:
  - `detail=minimal` (default): returns names + one-liner descriptions, grouped by category, aiming for < 2000 tokens
  - `detail=full`: returns full tool schemas with all parameters (current behavior)

  Implementation:
  ```python
  async def list_tools_endpoint(request):
      detail = request.query_params.get("detail", "minimal")
      tools = get_all_tools()
      tool_list = []
      for name, tool in tools.items():
          desc = extract_description(tool)
          if detail == "minimal":
              tool_list.append({"name": name, "description": desc, "category": get_tool_category(name)})
          else:
              tool_list.append({"name": name, "description": desc, "parameters": extract_params(tool)})
      # Group by category for minimal mode
      if detail == "minimal":
          grouped = group_by_category(tool_list)
          return JSONResponse({"success": True, "total": len(tool_list), "tool_count": len(tool_list), "categories": grouped})
      return JSONResponse({"success": True, "total": len(tool_list), "tool_count": len(tool_list), "tools": tool_list})
  ```

  **Acceptance Criteria**:
  - [ ] `GET /api/tools?detail=minimal` returns tools grouped by category, no full schemas
  - [ ] `GET /api/tools?detail=full` returns full schemas (backward compat)
  - [ ] `GET /api/tools` (no param) defaults to `minimal`
  - [ ] Response size under 2000 tokens in minimal mode
  - [ ] Both `total` and `tool_count` present in response

  **Agent Profile**: `deep`
  **Wave**: 1

- [x] 4. **Implement `tools/get_schema` endpoint** — full schema on demand

  **What to do**: Add new route `GET /api/tools/{tool_name}/schema` that returns the full JSON schema for a single tool. This enables lazy-loading: the agent sees minimal listing first, then fetches full schemas only for tools it intends to use.

  **Acceptance Criteria**:
  - [ ] `GET /api/tools/get_entity_state/schema` returns full tool schema
  - [ ] Returns 404 for non-existent tool
  - [ ] Schema includes: description, parameters (with types and defaults), response format

  **Agent Profile**: `quick`
  **Wave**: 1

- [x] 5. **Category-level grouping in `describe_ha_capabilities`**

  **What to do**: Modify `_do_describe_ha_capabilities()` in `tools/capabilities.py` to group tools by category (States, Automations, Scripts, Devices, Diagnostics, etc.) instead of returning a flat list. The existing tool manifest already has categories via `TOOL_MANIFESTS`.

  **Acceptance Criteria**:
  - [ ] `describe_ha_capabilities` returns tools grouped by category
  - [ ] Each category has `name`, `tool_count`, `tools: [{name, description}]`
  - [ ] Total tool count matches

  **Agent Profile**: `quick`
  **Wave**: 1

- [x] 6. **Full verification — tests, ruff, health endpoints**

  **What to do**:
  - Run `pytest tests/unit/ -q` → 1065+ tests pass
  - Run `ruff check .` → zero errors
  - Verify health endpoints via curl or python test
  - Verify progressive discovery response size under 2000 tokens

  **Acceptance Criteria**:
  - [ ] All tests pass
  - [ ] Ruff clean
  - [ ] Health endpoints return tool_count
  - [ ] `/api/tools?detail=minimal` response fits under 2000 tokens

  **Agent Profile**: `quick`
  **Wave**: 2

---

## Commit Strategy

| Wave | Commit Message | Files |
|------|---------------|-------|
| 1 | `feat(mcp): L3+ progressive discovery, health endpoint tool_count` | server.py, tools/capabilities.py |
| 2 | `test(server): verify health endpoints and progressive discovery` | tests/unit/test_server.py |
