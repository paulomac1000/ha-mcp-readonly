# HA-MCP-Readonly — Developer Instructions

> **Read before writing any tool that calls the Home Assistant REST API.**

## CRITICAL: HA API Authentication Model

Home Assistant has **two separate authentication scopes** for its REST API:

| Auth Method | Scope | Use Case |
|-------------|-------|----------|
| `Authorization: Bearer <LLAT>` | Public REST API endpoints | Entity states, services, config check, templates, history, logbook |
| Frontend session cookie | Internal/frontend endpoints | Trace context, some config flows, UI-only endpoints |

### The `LLAT` vs Frontend Trap

**Long-Lived Access Tokens (LLATs)** do NOT have access to every endpoint that the
frontend uses. Some endpoints (like `/api/trace/context/`) require a frontend
session cookie and will return `404` or `401` when accessed with an LLAT.

### Before Implementing Any New HA API Tool

1. **Verify the endpoint in official docs** first:
   - [HA REST API docs](https://developers.home-assistant.io/docs/api/rest/)
   - If the endpoint is NOT listed there, it is **not a public REST API endpoint**

2. **Test the endpoint with curl BEFORE writing any code:**
   ```bash
   curl -s -H "Authorization: Bearer $HA_TOKEN" "http://HA_IP:8123/the/endpoint"
   ```
   If it returns `404` or `401`, the endpoint is not accessible via LLAT.

3. **Never assume** an endpoint exists just because you saw it in:
   - WebSocket API docs (different transport)
   - Frontend network tab (uses cookie auth)
   - Other Home Assistant API wrappers (may use different auth)

### What Happened with `get_automation_traces` (v1.1.0)

- Tool was written assuming `/api/trace/context/` was a public REST endpoint
- All 6 unit tests used `patch("make_ha_request")` returning mocked `success: true`
- No curl verification was done against a real HA instance
- Tool never worked in production; was removed in v1.1.1

### Preventing Recurrence

After implementing any new HA API tool:

- [ ] Endpoint verified in [official HA REST API docs](https://developers.home-assistant.io/docs/api/rest/)
- [ ] Endpoint tested with `curl` + LLAT on a real HA instance
- [ ] At least one test uses a recorded VCR cassette (not just a mock)
- [ ] CI smoke test confirms the tool count is correct
