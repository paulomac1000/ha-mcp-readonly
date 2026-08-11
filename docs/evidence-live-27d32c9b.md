---
description: Immutable live-HA evidence for the exact 27d32c9b revision of the ai-skills compliance branch.
doc_id: reference.ha-mcp-evidence-27d32c9b
type: reference
status: active
rigor: normative
owners: [repository-maintainers]
verification: Re-run the documented suites against the pinned image digest and compare against the recorded results before relying on this evidence.
---

# Live-HA Evidence — HA-MCP-Readonly v2.0.0 (exact SHA 27d32c9b)

## Metadata

| Field | Value |
|-------|-------|
| Repo SHA (evidence source) | `27d32c9bc7e0a327a10e54f2b0a20a37a480ab73` |
| Repo SHA (post portability fix) | `6b945241db5a50fea719b3cb01c60ddbd9239abe` |
| Image tag | `ha-mcp-readonly:27d32c9b` |
| Image digest | `sha256:52a86083761fae9ed15f06cdfdf391378a3c6e009a1176ec76d70c4957df7248` |
| Image ID | `sha256:52a860...` |
| Server version | 2.0.0 |
| Home Assistant version | 2026.5.1 |
| HA location | Dom |
| Host | terminal (192.168.0.101) |
| Timestamp | 2026-08-11 ~21:30 UTC |
| Test run env | live HA + deployed container `ha-mcp-readonly:27d32c9b` |

## Runtime dependency alignment (P0 #1)

| Package | constraints-ci.txt | Plain Dockerfile | Dockerfile.release |
|---------|-------------------|------------------|---------------------|
| fastmcp | 3.4.6 | **3.4.6** | **3.4.6** |
| starlette | 1.4.1 | **1.4.1** | **1.4.1** |
| uvicorn | 0.52.1 | **0.52.1** | **0.52.1** |
| pip check | - | No broken requirements | No broken requirements |

Dependency drift (previously fastmcp 3.4.7 / starlette 1.6.0 without constraints) is **resolved**: plain Dockerfile now installs with `-c constraints-ci.txt` + `pip check`.

## Test suite results (live HA, exact 27d32c9b)

| Suite | Result | Duration | Notes |
|-------|--------|----------|-------|
| Unit + Protocol | **1234 passed** | 58s | 1220 unit + 14 protocol |
| Smoke (run 1) | 77 passed / 9 failed | 244s | flaky under host load during image build |
| Smoke (run 2) | **86 passed** | 128s | clean rerun, no failures |
| E2E | **174 passed** | 437s | all tool smoke + context generator + server API |
| Integration | **272 passed, 6 skipped** | 170s | 6 skips = no script/scene/template prereqs |

### Skips (integration, 6)

Skips are `pytest.skip` for prerequisites absent on this instance (e.g. no scene/script/blueprint objects to exercise get_*_code). Verified individually: `test_get_script_code` and `test_get_scene_code` pass standalone (3.6s / 3.4s), proving no order dependence. Public responses are pure v2 JSON envelope (`success` + `_meta` + `result`), no raw-YAML fallback.

## MCP tools

- Total tools exposed over MCP `tools/list`: **158** (with MCP_DEV_TOOLS_ENABLED=1)
- Health details: all components ready, backend reachable, capability_degradation empty

## TrustedHost x auth matrix (P0 #4)

| Case | Result | Expected |
|------|--------|----------|
| Allowed LAN host (192.168.0.101) + valid bearer | 200 | pass |
| Disallowed host (evil.example.com) + valid bearer | 400 | TrustedHost reject |
| Allowed host + no bearer | 401 | auth reject |
| Allowed host + wrong bearer | 401 | auth reject |
| Loopback default (localhost) | 200 | pass |
| MCP_ALLOWED_HOSTS in container | present (compose forwards) | pass |
| MCP_ALLOWED_HOSTS=* | ValueError at startup | config reject |

## Non-default filesystem root (P0 #3)

Container with `HA_CONFIG_PATH=/srv/ha-config`:

| Tool | Result |
|------|--------|
| list_directory (no path) | success, 47 entries, path=/srv/ha-config |
| read_file("configuration.yaml") | success |
| search_files (no root) | success |
| read_file("/config/...") explicit | **ACCESS_DENIED** (outside allowed dirs) |
| Full smoke suite vs non-default root | **86 passed** |
| e2e read_file/list_directory/search_files vs non-default root | **4 passed** |

Tests now discover config root from `list_directory` instead of hardcoding `/config` (commit `6b94524`).

## Script/scene contract (P0 #5)

- `test_get_script_code` standalone: 1 passed (3.6s)
- `test_get_scene_code` standalone: 1 passed (3.4s)
- Module: 4 passed
- Full integration suite: 272 passed
- Response is JSON v2 envelope (`success`, `_meta`, `result` string), no raw YAML

## Redaction (P1 #6) — artificial secrets only

9/9 redaction checks pass: accessToken, apiKey, refreshToken, clientSecret, privateKey, snake_case, realistic bearer with +/=/., query-string secret. Log sanitization redacts bearer/password/token/IP. Real tool response (get_main_configuration) contains no IP addresses.

## Context generation failure/recovery (P1 #7)

| Scenario | Result |
|----------|--------|
| Parallel generate | first 202 running, second **409 CONFLICT** (controlled) |
| Worker deadline (300s, huge instance) | process killed, status error, **next generate starts cleanly** |
| Zombie workers after failures | none (6 processes = baseline) |
| Server health after failures | ready, 158 tools |
| Context download after failed run | 404 (no artifact) |
| Full success-path generation | NOT verifiable on this instance (5480 config files exceed 300s fixed deadline) — environment limitation |

## Performance / concurrency (P1 #8)

| Tool | Times (5 runs) | Regression to ~120s? |
|------|----------------|---------------------|
| diagnose_automation_aliases | 1750/4564/2287/1977/2590 ms | No |
| list_automations | 2234/1918/1459/1757/2543 ms | No |
| get_states_filtered (sensor) | 206/366/220/225/276 ms | No |

- 8 parallel read-tools: all success, 0.4–1.3s, no SERVER_BUSY
- After client-side timeouts: server responds in 14ms, no permit/executor leak, no capability degradation
- Container RSS 323 MiB, CPU 0.36% idle

## Outage/restart (P1 #9)

| Scenario | Result |
|----------|--------|
| Container restart | healthy in 15s |
| HA unreachable + HA_BACKEND_REQUIRED_FOR_READY=1 | `/ready` = not_ready, `/live` = 200 |
| HA restored (correct URL) | ready, backend reachable |
| Port collision | `Address already in use` (Errno 98) |
| Native arm64 smoke | not executed (no physical arm64 host available) |

## Gaps / residual risk

1. **Context generator success path** not verified on this instance — fixed 300s deadline is exceeded by the 5480-file config. Failure/recovery path fully verified.
2. **Native arm64** not executed (QEMU-only would be misleading).
3. First smoke run had 9 flaky failures under host load (image build running concurrently); clean rerun passes 86/86.
4. L3 certification still pending: opaque artifact ID, lifecycle/TTL/checksum/delete, task-ID entropy requirements (per agent's instructions, not resolved in this round).
