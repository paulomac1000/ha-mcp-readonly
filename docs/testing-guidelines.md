---
description: Executable testing strategy for domain, policy, protocol, package, and container boundaries.
doc_id: guide.ha-mcp-testing
type: guide
status: active
rigor: operational
owners: [repository-maintainers]
verification: Run `pytest tests/unit tests/protocol -q`, build the wheel, install it in a clean environment, and execute the container smoke test from `.github/workflows/ci.yml`.
---

# Testing guidelines

## Evidence layers

Each layer answers a different question:

| Layer | Evidence |
| --- | --- |
| Domain unit | Pure parsing, filtering, normalization, and controlled errors |
| Policy unit | Manifest coverage, authorization, deadline, concurrency, response size, path containment |
| Protocol | Official FastMCP client handshake, `tools/list`, `tools/call`, schema rejection |
| Backend contract | Recorded or dedicated Home Assistant fixture responses, including failures |
| Package | Wheel contents and clean-environment import and startup |
| Container | Non-root runtime, default-image health, exact platform archive, REST metadata, context lifecycle, authenticated Streamable HTTP |

A direct call to a Python function or private SDK registry is useful unit evidence but not MCP protocol evidence.

## Network-backed tools

Tests must cover success, timeout, authentication failure, not-found behavior, malformed backend data, and sanitization. Prefer deterministic recorded responses or a dedicated test Home Assistant instance. Never commit real tokens, hostnames, entity histories, or unredacted cassettes.

Blind mocks that always return `success: true` are insufficient. A mock must represent the actual endpoint shape and at least one realistic failure mode.

## Security regressions

At minimum, preserve tests for:

- sibling-prefix and `..` path escape attempts;
- symlinks crossing a configured root;
- `.storage`, authentication records, secrets files, and environment files;
- missing manifests and missing capabilities;
- deadline and response-size enforcement;
- REST bearer authentication, CORS preflight, stable errors, and all manifest/schema routes;
- protocol-native input validation over a real stdio subprocess and network transport;
- offline context generation with blocked network access, provenance, redaction, and atomic publication;
- wheel contents, non-root execution, default-image health, exact quarantined-digest smoke tests on every published architecture, and protected digest-only promotion.

## Local commands

```bash
pytest tests/unit -q
pytest tests/protocol -q
ruff check .
ruff format --check .
mypy server.py tools/ context_generator/core.py context_generator/config.py context_generator/runtime.py context_generator/provenance.py context_generator/snapshot.py scripts/verify_runtime_endpoints.py --strict
bandit -r server.py tools/ context_generator/ ha_graph/ -ll
python -m build --wheel --no-isolation
```

Real Home Assistant suites remain environment-dependent and must run only against an isolated test instance with disposable data. They do not replace deterministic unit, protocol, package, and container gates.

## Hosted CI execution policy

The pinned `ci-cd-architect` standard separates workflow trust from hosted-runner execution frequency. This repository is migrating expensive development workflows to the `on-demand` execution profile without weakening the exact-SHA acceptance gate.

The `CI` workflow already exists with `workflow_dispatch` on the default branch, so candidate branches can use two manual modes immediately:

```bash
# Fast branch feedback: quality, standards, typing, security lint, and contracts.
gh workflow run ci.yml --ref <branch>

# Full acceptance candidate: Python matrix, coverage policy, wheel, stdio smoke, and containers.
gh workflow run ci.yml --ref <branch> -f full=true
```

During the bootstrap migration, pull-request and `main` events still execute the full `CI` gate. A default manual dispatch runs only the fast `quality` job; `full=true` enables the test matrix, wheel, and container jobs. Coverage for a manual full run is compared with `origin/main`, not with the dispatched commit itself.

Do not remove automatic pull-request execution from a newly introduced expensive workflow until a dispatchable definition for that workflow path exists on the default branch. GitHub requires that bootstrap before a candidate-ref `workflow_dispatch` can be used. In particular, `Official MCP client` and `Migration evidence` are new workflow paths in the current migration and must retain their trusted PR acceptance path until their definitions reach the default branch. `Semgrep Security Scan` now exposes `workflow_dispatch` as its bootstrap step but also retains its PR trigger until that definition reaches the default branch.

After the default-branch bootstrap is complete, expensive development workflows can remove their PR trigger and declare `# ai-skills-execution-policy: on-demand`; the pinned `check_ci_execution_policy.py` auditor is already part of `AI Skills policy` and will reject unsafe trigger shapes. Cheap policy/pre-commit checks may remain event-driven. Scheduled assurance and protected release workflows remain separately governed and are not disabled merely to save branch-iteration minutes.

A hosted run is acceptance evidence only for the immutable SHA that actually executed all required steps. A later branch update makes older full-run evidence stale. A created workflow record with no assigned runner or executed steps is infrastructure/provider failure, not a code pass or failure.

## Runtime boundary verification

`scripts/verify_runtime_endpoints.py` targets a running release image. It verifies public liveness, component readiness, anonymous rejection, CORS preflight, all 145 tool manifest and schema routes, OpenAPI coverage, a controlled tool call, unknown-tool behavior, the full offline context generate/status/download cycle, redaction sentinels, and an official FastMCP Streamable HTTP handshake with input rejection.

The container CI job executes the script against the built release container. The release workflow independently builds the multi-platform candidate once into quarantine, records the manifest digest, smoke-tests that exact digest on amd64 and arm64, and lets the protected publisher promote only that digest without checking out or executing candidate source. Home Assistant-dependent integration, smoke, and end-to-end suites still require an isolated live Home Assistant instance and valid credentials; skips in an environment without that backend are reported rather than represented as passes.


## Legacy typing boundary

The runtime composition layer, all `tools/` modules, and the new context runtime modules (`core`, `config`, `runtime`, `provenance`, and `snapshot`) are strict-mypy gates. The older monolithic `context_generator.analyzers`, `context_generator.formatters`, `context_generator.utils`, `context_generator.constants`, and `ha_graph` modules predate that contract and remain under an explicit scoped mypy override. They must not be added to new runtime-policy code, and new modules must not inherit the override. A full strict migration of that legacy surface is tracked as residual technical debt rather than reported as already complete.
