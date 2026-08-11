---
description: Revision-bound live-HA evidence for the 27d32c9b runtime and explicitly identified test-harness revisions.
doc_id: reference.ha-mcp-evidence-27d32c9b
type: reference
status: active
rigor: normative
owners: [repository-maintainers]
verification: Re-run the documented suites against the recorded runtime SHA and image digest; record the exact test-harness SHA separately before relying on new results.
---

# Live-HA Evidence — HA-MCP-Readonly v2.0.0

This document is revision-bound evidence. It does not imply that later branch heads inherit live-HA results without a rerun.

## Metadata

| Field | Value |
|-------|-------|
| Runtime repo SHA / full-suite test source | `27d32c9bc7e0a327a10e54f2b0a20a37a480ab73` |
| Test-harness SHA for non-default-root rerun | `6b945241db5a50fea719b3cb01c60ddbd9239abe` |
| Runtime image tag | `ha-mcp-readonly:27d32c9b` |
| Runtime image digest | `sha256:52a86083761fae9ed15f06cdfdf391378a3c6e009a1176ec76d70c4957df7248` |
| Server version | 2.0.0 |
| Home Assistant version | 2026.5.1 |
| HA location / host | redacted; private deployment metadata is intentionally not published |
| Timestamp | 2026-08-11 ~21:30 UTC |
| Test run env | live HA + deployed runtime image `ha-mcp-readonly:27d32c9b` |

### Provenance boundary

The complete unit/protocol, smoke, E2E, and integration run below used source revision `27d32c9b` and the runtime image bound to that revision. The non-default filesystem-root follow-up used test-harness revision `6b94524` against the same `27d32c9b` runtime image. The harness-only change did not alter the runtime image. Later branch commits, including documentation and fail-closed test-hardening changes, are not represented as full live-HA reruns by this document.

## Runtime dependency alignment (P0 #1)

| Package | constraints-ci.txt | Plain Dockerfile | Dockerfile.release |
|---------|-------------------|------------------|---------------------|
| fastmcp | 3.4.6 | **3.4.6** | **3.4.6** |
| starlette | 1.4.1 | **1.4.1** | **1.4.1** |
| uvicorn | 0.52.1 | **0.52.1** | **0.52.1** |
| pip check | - | No broken requirements | No broken requirements |

Dependency drift (previously fastmcp 3.4.7 / starlette 1.6.0 without constraints) is **resolved** for the recorded runtime: the plain Dockerfile installs with `-c constraints-ci.txt` and `pip check`.

## Full-suite results (live HA, source/runtime SHA 27d32c9b)

| Suite | Result | Duration | Notes |
|-------|--------|----------|-------|
| Unit + Protocol | **1234 passed** | 58s | 1220 unit + 14 protocol |
| Smoke (run 1) | 77 passed / 9 failed | 244s | failures occurred while the host was simultaneously building an image; retained as evidence rather than discarded |
| Smoke (run 2) | **86 passed** | 128s | clean rerun after build load ended |
| E2E | **174 passed** | 437s | all-tool smoke + context generator + server API |
| Integration | **272 passed, 6 skipped** | 170s | skips were environment prerequisites |

A passing rerun does not erase the first smoke failure. The observed correlation with concurrent image-build load is evidence, not proof of root cause; resource-contention sensitivity remains something to watch in future live runs.

### Skips (integration, 6)

Skips are `pytest.skip` for prerequisites absent on this instance. `test_get_script_code` and `test_get_scene_code` were also run standalone and passed (3.6s / 3.4s), demonstrating that those tests no longer depend on execution order. Public responses were v2 JSON envelopes (`success` + `_meta` + `result`), with no raw-YAML fallback.

## MCP tools

- Total tools exposed over MCP `tools/list`: **158** with `MCP_DEV_TOOLS_ENABLED=1`.
- Health details reported all components ready, backend reachable, and no capability degradation for the recorded run.

## TrustedHost x auth matrix (P0 #4)

| Case | Result | Expected |
|------|--------|----------|
| Allowed redacted LAN host + valid bearer | 200 | pass |
| Disallowed synthetic host + valid bearer | 400 | TrustedHost reject |
| Allowed host + no bearer | 401 | auth reject |
| Allowed host + wrong bearer | 401 | auth reject |
| Loopback default (`localhost`) | 200 | pass |
| `MCP_ALLOWED_HOSTS` in container | present (Compose forwards) | pass |
| `MCP_ALLOWED_HOSTS=*` | ValueError at startup | config reject |

No private LAN address, Home Assistant location label, bearer value, or other deployment secret is retained in this checked-in evidence.

## Non-default filesystem root (P0 #3)

Runtime container with `HA_CONFIG_PATH=/srv/ha-config`; follow-up test harness at `6b94524`:

| Tool / check | Result |
|--------------|--------|
| `list_directory` with omitted path | success, 47 entries, reported `/srv/ha-config` |
| `read_file("configuration.yaml")` | success |
| `search_files` with omitted root | success |
| explicit `read_file("/config/...")` | **ACCESS_DENIED** (outside allowed dirs) |
| Full smoke suite vs non-default root | **86 passed** |
| E2E read/list/search checks vs non-default root | **4 passed** |

The `6b94524` harness changed smoke/E2E root discovery; it did not rebuild or modify the `27d32c9b` runtime image. Subsequent test hardening removes silent fallback behavior, so a failure to discover the configured root must now fail the test rather than silently assuming `/config`.

## Script/scene contract (P0 #5)

- `test_get_script_code` standalone: 1 passed (3.6s)
- `test_get_scene_code` standalone: 1 passed (3.4s)
- Module: 4 passed
- Full integration suite: 272 passed
- Response observed as JSON v2 envelope (`success`, `_meta`, `result` string), no raw YAML

## Redaction (P1 #6) — artificial secrets only

Nine artificial-secret cases were exercised: camelCase and snake_case credential keys, a bearer-shaped dummy value, and a query-string secret. The tested responses/log paths redacted those markers. A representative `get_main_configuration` response contained no IP address. These checks are adversarial samples, not a claim that arbitrary future secret formats are exhaustively impossible.

## Context generation failure/recovery (P1 #7)

| Scenario | Result |
|----------|--------|
| Parallel generate | first 202 running, second **409 CONFLICT** (controlled) |
| Worker deadline (300s) | process killed, status error, **next generate starts cleanly** |
| Zombie workers after failures | none observed (process count returned to baseline) |
| Server health after failures | ready, 158 tools |
| Context download after failed run | 404 (no artifact) |
| Full success-path generation | **not completed**: this deployment reached the fixed 300s deadline while processing a 5480-file configuration tree |

The success-path failure is a **scalability/deadline residual risk**, not proven to be merely an environment limitation. It needs profiling and either bounded performance improvements or a reviewed configurable deadline before this evidence can claim successful context generation on configurations of this size.

## Performance / concurrency (P1 #8)

| Tool | Times (5 runs) | Regression to ~120s? |
|------|----------------|---------------------|
| `diagnose_automation_aliases` | 1750/4564/2287/1977/2590 ms | No |
| `list_automations` | 2234/1918/1459/1757/2543 ms | No |
| `get_states_filtered` (sensor) | 206/366/220/225/276 ms | No |

- 8 parallel read-tools: all success, 0.4–1.3s, no `SERVER_BUSY`.
- After client-side timeouts: server responded in 14ms, with no permit/executor leak observed.
- Recorded container RSS was 323 MiB and idle CPU 0.36%; these are observations from one run, not service-level guarantees.

## Outage/restart (P1 #9)

| Scenario | Result |
|----------|--------|
| Container restart | healthy in 15s |
| HA unreachable + `HA_BACKEND_REQUIRED_FOR_READY=1` | `/ready` = not_ready, `/live` = 200 |
| HA restored | ready, backend reachable |
| Port collision | `Address already in use` (Errno 98) |
| Native arm64 smoke | not executed (no physical arm64 host available) |

## Gaps / residual risk

1. **Context generator success path/scalability:** the recorded large configuration did not complete within the fixed 300s deadline; root cause still needs profiling.
2. **Native arm64:** no physical arm64 host was available; public CI QEMU/build evidence is not equivalent to native runtime evidence.
3. **Smoke resource sensitivity:** the first run had 9 failures under concurrent host build load; the clean rerun passed, but the first result remains part of the evidence.
4. **Current-head live evidence:** commits after `6b94524` require their own live rerun if they are to be included in a certification claim.
5. **L3 certification:** opaque artifact identity, lifecycle/TTL/checksum/delete semantics, and task-ID entropy remain open requirements.
