# CI/CD Standard v2.0.1 Compliance — ha-mcp-readonly

## TL;DR

> **Quick Summary**: Fix 10 CI/CD standard violations across workflow files, pyproject.toml, and dependabot.yml to achieve full compliance with ci-cd-architect standard v2.0.1.
>
> **Deliverables**:
> - Action version fixes (attest, SHA pinning)
> - pyproject.toml updates (mypy, ruff, classifiers)
> - Workflow security hardening (persist-credentials, auto-tag dispatch)
> - Dependabot ecosystem fix
> - Docs validation restoration (optional)
>
> **Estimated Effort**: Quick (~30 min)
> **Parallel Execution**: Partial — 2 waves
> **Critical Path**: Wave 1 (pyproject + workflow fixes in parallel) → Wave 2 (verify)

---

## Context

### Audit Results
Compliance audit performed 2026-06-06 against `ci-cd-standard.md` v2.0.1. Found 18 of 27 rules compliant, 9 violations (3 L1+ critical, 6 L2+ medium).

### Current State
- Branch: `feature/v1.6.0-endpoints-standards`
- Config contract: `.github/ci-cd-config.yaml` (exists, configured for MCP + Docker + tools/ variant B)
- 6 workflow files: ci.yml, publish.yml, auto-tag.yml, semgrep.yml, semgrep-scheduled.yml, docs-validation.yml

---

## Work Objectives

### Core Objective
Achieve full L1+ compliance with CI/CD standard v2.0.1 by fixing all 9 identified violations plus 1 optional improvement.

### Concrete Deliverables
- `.github/workflows/publish.yml` — attest action version fix
- `.github/workflows/ci.yml` — persist-credentials on checkout ×3
- `.github/workflows/auto-tag.yml` — persist-credentials + filename-based trigger + workflow_dispatch
- `.github/workflows/semgrep.yml` — persist-credentials + SHA pinning
- `.github/workflows/semgrep-scheduled.yml` — persist-credentials + SHA pinning
- `pyproject.toml` — remove ignore_missing_imports, add 3.14 classifier, fix ruff target-version
- `.github/dependabot.yml` — add docker ecosystem
- `.github/workflows/docs-validation.yml` — restore validation (optional)

### Must Have
- All L1+ violations resolved
- `pytest tests/unit/ -q` passes
- `ruff check .` passes
- `mypy tools/ --strict` passes (after removing ignore_missing_imports)

### Must NOT Have
- No changes to application code (tools/, server.py, context_generator/)
- No regressions in existing CI pipeline behavior

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: Tests-after — verify after each change that CI checks pass
- **Framework**: pytest, ruff, mypy

### QA Policy
- `ruff check .` — zero errors
- `mypy tools/ --strict` — zero errors (after mypy config fix)
- `pytest tests/unit/ -q` — all 1065 tests pass

---

## TODOs

- [x] 1. **CI-CDW-3**: Fix `actions/attest@v4` → `actions/attest-build-provenance@v2` in publish.yml

  **What to do**: Edit `.github/workflows/publish.yml` line 73: change `uses: actions/attest@v4` to `uses: actions/attest-build-provenance@v2`.

  **Acceptance Criteria**:
  - [x] publish.yml line 73 uses `actions/attest-build-provenance@v2`

  **Agent Profile**: `quick`
  **Wave**: 1

- [x] 2. **CI-CDW-8**: Remove `ignore_missing_imports = true` from pyproject.toml

  **What to do**: Edit `pyproject.toml` line 89: remove `ignore_missing_imports = true`. If mypy fails on missing stubs, add specific per-module exceptions in `[[tool.mypy.overrides]]`.

  **Acceptance Criteria**:
  - [x] `ignore_missing_imports` not present in `[tool.mypy]`
  - [x] `mypy tools/ --strict` passes (may need adding type stubs or per-module overrides for untyped libs like `fastmcp`, `requests`, `pyyaml`, `starlette`, `uvicorn`, `dateutil`, `pydantic`)

  **Agent Profile**: `deep`
  **Wave**: 1

- [x] 3. **CI-CDW-70**: Add `Programming Language :: Python :: 3.14` classifier to pyproject.toml

  **What to do**: Edit `pyproject.toml` classifiers section (line 16-26): add `"Programming Language :: Python :: 3.14"` after 3.13.

  **Acceptance Criteria**:
  - [x] `grep "Python :: 3.14" pyproject.toml` returns match

  **Agent Profile**: `quick`
  **Wave**: 1

- [x] 4. **CI-CDW-72**: Fix ruff `target-version` from `py311` to `py314` in pyproject.toml

  **What to do**: Edit `pyproject.toml` line 62: change `target-version = "py311"` to `target-version = "py314"`.

  **Acceptance Criteria**:
  - [x] `ruff.target-version = "py314"`
  - [x] `ruff check .` still passes

  **Agent Profile**: `quick`
  **Wave**: 1

- [x] 5. **CI-CDW-79**: Add `persist-credentials: false` to ALL `actions/checkout` steps across all 6 workflow files

  **What to do**: Edit each workflow file. Add `persist-credentials: false` to every `uses: actions/checkout@v6` step as a `with:` property.

  **Files affected**:
  - ci.yml (lines 20, 64, 95) — 3 checkouts
  - publish.yml (line 34) — 1 checkout
  - auto-tag.yml (line 25) — 1 checkout
  - semgrep.yml (line 27) — 1 checkout
  - semgrep-scheduled.yml (line 24) — 1 checkout
  - docs-validation.yml (line 41) — 1 checkout

  **Acceptance Criteria**:
  - [x] Every `actions/checkout@v6` in all 6 workflow files has `persist-credentials: false`

  **Agent Profile**: `quick`
  **Wave**: 1

- [x] 6. **CI-CDW-76c**: Fix auto-tag.yml `gh workflow run` to use filename instead of display name

  **What to do**: Edit `.github/workflows/auto-tag.yml` line 49: change `gh workflow run "Create and publish a Docker image"` to `gh workflow run publish.yml`.

  **Acceptance Criteria**:
  - [x] `grep 'gh workflow run' .github/workflows/auto-tag.yml` shows `publish.yml`

  **Agent Profile**: `quick`
  **Wave**: 1

- [x] 7. **CI-CDW-76d**: Fix auto-tag.yml job condition to handle `workflow_dispatch`

  **What to do**: Edit `.github/workflows/auto-tag.yml` line 18: change `if: github.event.pull_request.merged == true` to `if: github.event_name == 'workflow_dispatch' || github.event.pull_request.merged == true`.

  **Acceptance Criteria**:
  - [x] Job condition includes `workflow_dispatch` alternative

  **Agent Profile**: `quick`
  **Wave**: 1

- [x] 8. **Dependabot**: Add `docker` ecosystem to `.github/dependabot.yml`

  **What to do**: Edit `.github/dependabot.yml`: add a third update block after `pip`:
  ```yaml
    - package-ecosystem: "docker"
      directory: "/"
      schedule:
        interval: "weekly"
      groups:
        docker:
          patterns:
            - "*"
  ```

  **Acceptance Criteria**:
  - [x] dependabot.yml has 3 ecosystems (github-actions, pip, docker)

  **Agent Profile**: `quick`
  **Wave**: 1

- [x] 9. **CI-CDW-73/74** (L2+): Implement SHA pinning on all workflows (OPTIONAL — deferred)

  **What to do**: If chosen: resolve commit SHAs for all action references via `git ls-remote`, replace `@vN` with `@<full-sha>  # vN`. If deferred: mark as tracked tech debt.

  **Acceptance Criteria**:
  - [x] If implemented: all `uses:` fields use SHA format with `# vN` comment

  **Agent Profile**: `quick`
  **Wave**: 2 (optional)

- [x] 10. **Verify**: Run `ruff check .`, `mypy tools/ --strict`, `pytest tests/unit/ -q`

  **Acceptance Criteria**:
  - [ ] `ruff check .` — zero errors
  - [ ] `mypy tools/ --strict` — zero errors
  - [ ] `pytest tests/unit/ -q` — 1065 tests pass

  **Agent Profile**: `quick`
  **Wave**: 2

---

## Commit Strategy

| Wave | Commit Message | Files |
|------|---------------|-------|
| 1 | `fix(ci): CI/CD standard v2.0.1 compliance — pyproject, workflows, dependabot` | pyproject.toml, .github/workflows/*.yml, .github/dependabot.yml |
| 2 | `fix(ci): restore mypy strict mode, SHA pinning (optional), final verification` | pyproject.toml, .github/workflows/*.yml |
