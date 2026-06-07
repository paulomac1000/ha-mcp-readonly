# Pre-commit Hook Architect — Skill Design & Implementation Plan
>
> Based on deployment experience from `ha-mcp-readonly` and `local-home-devices-mcp`,
> plus research on FastAPI, Django, and professional Python projects.
> Date: 2026-06-07

---

## 1. Why a Dedicated Skill

The CI/CD Architect skill covers GitHub Actions workflows (server-side CI). The MCP Server Architect covers tool design patterns. But **pre-commit hooks live at a different boundary** — the developer's machine, before code reaches version control. They need their own architect because:

1. **Different timing**: Pre-commit runs at `git commit`, not on push/PR
2. **Different constraints**: Must be fast (<30s total), must not require network, must work offline
3. **Different failure modes**: `ruff format` bugs (target-version mismatch, except syntax), stale caches, language version conflicts
4. **Different configuration patterns**: `local` vs `remote` repos, `system` vs `unsupported` language, `files` filtering, stage selection
5. **CI mirroring**: Pre-commit MUST run the same checks as CI (same tools, same config, same ordering)

---

## 2. Skill Architecture

```
skills/pre-commit-architect/
├── SKILL.md                      # System prompt / persona
├── precommit-standard.md         # Authoritative standard (~800 lines)
├── templates/
│   ├── pre-commit-python.j2      # Base Python config
│   ├── pre-commit-mcp.j2         # MCP server variant
│   └── pre-commit-minimal.j2     # Fast-only checks
└── references/
    ├── hook-catalog.md           # Available hooks with version matrix
    └── pitfalls.md               # Common bugs and fixes
```

---

## 3. Core Standard Rules (precommit-standard.md)

Modeled after `ci-cd-standard.md` with semantic anchors `[RULE: PRECOMMIT-NN]`:

| Rule ID | Level | Content |
|---------|-------|---------|
| PRECOMMIT-01 | L1+ | Pre-commit MUST run the same checks as CI lint+test jobs in the same order |
| PRECOMMIT-02 | L1+ | Hook ordering: generic → lint → format → types → security → docs → tests |
| PRECOMMIT-03 | L1+ | `ruff target-version` MUST match `requires-python` minimum, NOT CI runner version |
| PRECOMMIT-04 | L1+ | NEVER use `|| true` or `--ignore` to mask errors |
| PRECOMMIT-05 | L1+ | Use `python3` not `python` in all entry commands |
| PRECOMMIT-06 | L1+ | ALL hooks use `fail_fast: false` (collect all errors before failing) |
| PRECOMMIT-07 | L2+ | Heavy tests (integration, e2e) go to `pre-push` stage, not `pre-commit` |
| PRECOMMIT-08 | L2+ | Pre-commit total runtime MUST be under 30 seconds |
| PRECOMMIT-09 | L2+ | Remote hooks use commit SHA, not version tags |
| PRECOMMIT-10 | L1+ | `.pre-commit-config.yaml` committed to repo, AGENTS.md section present |

---

## 4. What We Learned from ha-mcp-readonly (7 pitfalls)

| # | Pitfall | What happened | Rule |
|---|---------|--------------|------|
| 1 | `ruff target-version = "py314"` broke `except (X, Y):` syntax | ruff format reverted Python 3.13-compatible except clauses | PRECOMMIT-03 |
| 2 | `python` vs `python3` in hook entry | Hook used `python` but Debian only has `python3` | PRECOMMIT-05 |
| 3 | CAFDS scanned `.omo/` plans | Plans lack YAML frontmatter → CAFDS fails | Exclude pattern needed |
| 4 | Unit tests timed out `git commit` | 30s hook timeout vs 30s test runtime | PRECOMMIT-07 |
| 5 | Agent used `--ignore` to skip failures | Instead of fixing old-style except syntax | PRECOMMIT-04 |
| 6 | YAML parsing errors from complex bash in `entry:` | `entry: bash -c '...'` with special chars broke YAML | Use `entry: |` literal block |
| 7 | `fastmcp` env issue blocked ALL tests | Pre-existing install bug, not code bug | Known env issue doc |

---

## 5. Decision Matrix: Local vs Remote Repos

| Factor | Local (`repo: local`) | Remote (`repo: https://...`) |
|--------|----------------------|------------------------------|
| **When** | Project-specific tools (mypy, bandit, pytest, version-check) | Standard tools (ruff, pre-commit-hooks, semgrep) |
| **Version** | Uses system-installed version | Pinned to specific git rev |
| **Speed** | Faster (no download) | First run downloads, cached |
| **Portability** | Depends on developer tools | Self-contained, consistent |
| **Maintenance** | Must sync with CI | Version updates via dependabot |

---

## 6. Speed Budget per Hook

| Hook | Target Time | Stage |
|------|------------|-------|
| trailing-whitespace | <1s | pre-commit |
| check-yaml/toml | <1s | pre-commit |
| ruff check | <3s | pre-commit |
| ruff format | <3s | pre-commit |
| mypy | <10s | pre-commit |
| bandit | <5s | pre-commit |
| semgrep | <10s | pre-commit (if installed) |
| CAFDS docs | <5s | pre-commit (network) |
| pytest unit | <30s | pre-commit |
| pytest integration | — | **pre-push only** |
| e2e tests | — | **CI only** |

---

## 7. SKILL.md — 3 Workflow Types

**AUDIT**: Check existing `.pre-commit-config.yaml` against standard
- Verify hook ordering, tool versions, CI mirroring
- Produce compliance report with PRECOMMIT-NN rule IDs

**GENERATE**: Create `.pre-commit-config.yaml` from Jinja2 template
- Classify project (Python/MCP/minimal), select template, substitute params
- Generate AGENTS.md pre-commit section

**UPGRADE**: Migrate between standard versions
- Update tool versions, add/remove hooks, sync CI workflow

---

## 8. Code Review Checklist

- [ ] Hook ordering: generic → lint → format → types → security → docs → tests
- [ ] All hooks use `fail_fast: false`
- [ ] `ruff target-version` matches `requires-python` minimum
- [ ] No `|| true` or `--ignore` on any hook
- [ ] `python3` not `python` in all entry commands
- [ ] CI lint+test jobs run same commands in same order
- [ ] AGENTS.md pre-commit section present
- [ ] Heavy tests at `pre-push` stage, not `pre-commit`

---

## 9. Integration with Other Skills

| Skill | Relationship |
|-------|-------------|
| `ci-cd-architect` | Pre-commit runs the same checks as CI. Config contract shared. |
| `mcp-server-architect` | MCP variant adds tool count + manifest validation |
| `afds-doc-writer` | CAFDS validation hook uses the same validator |

---

## 10. References

- FastAPI `.pre-commit-config.yaml`: https://github.com/fastapi/fastapi/blob/master/.pre-commit-config.yaml
- Ruff pre-commit: https://github.com/astral-sh/ruff-pre-commit
- Pre-commit docs: https://pre-commit.com
- 2025 Guide: https://gatlenculp.medium.com/effortless-code-quality-the-ultimate-pre-commit-hooks-guide-for-2025
