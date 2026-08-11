---
description: Contribution workflow and quality requirements for HA-MCP-Readonly changes.
doc_id: guide.ha-mcp-contributing
type: guide
status: active
rigor: operational
owners: [repository-maintainers]
verification: Run Ruff, strict mypy, Bandit, unit tests, protocol tests, and the applicable package or runtime checks before opening a pull request.
---

# Contributing to HA-MCP-Readonly

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## How to Contribute

### Reporting Bugs

- Check if the issue already exists in the [issue tracker](https://github.com/paulomac1000/ha-mcp-readonly/issues)
- Provide a clear description of the bug
- Include steps to reproduce
- Mention your Python version and Home Assistant version
- Include relevant logs (with sensitive data redacted)

### Suggesting Features

- Open an issue with the `enhancement` label
- Describe the feature and its use case
- Explain how it fits the read-only design philosophy

### Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Add or update tests as needed
5. Run the deterministic gates from `docs/testing-guidelines.md` in a virtual environment
6. Update documentation if needed
7. Commit with clear messages
8. Open a Pull Request

## Development Setup

```bash
git clone https://github.com/paulomac1000/ha-mcp-readonly.git
cd ha-mcp-readonly
python -m venv venv
source venv/bin/activate
python -m pip install -c constraints-ci.txt '.[dev]'
```

## Coding Standards

- **Language**: All code, comments, and docstrings must be in English
- **Style**: Follow PEP 8
- **Docstrings**: Use Google-style docstrings for all public functions
- **Type hints**: Include type hints for function signatures
- **Tests**: Minimum 80% code coverage for new code
- **Error handling**: Always return JSON with `success` field

## Adding a New Tool

1. Choose the appropriate module in `tools/`
2. Add the tool function with `@mcp.tool()` decorator
3. Update the `register_*_tools()` function
4. Add tests in `tests/unit/test_*.py`
5. Update `docs/documentation.md`

### Tool Pattern

```python
@mcp.tool()
async def my_new_tool(entity_id: str) -> str:
    """
    Brief description of what the tool does.

    Args:
        entity_id: Description of the parameter

    Returns:
        JSON string with the result
    """
    try:
        result = do_something(entity_id)
        return json.dumps({"success": True, "data": result}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)
```

## Testing

### Unit Tests (no credentials needed)

```bash
pytest tests/unit/ tests/smoke/ -v --tb=short
```

### Smoke Tests (requires REST API + HA_TOKEN)

```bash
cp .env.example .env
# Edit .env with your HA_URL and HA_TOKEN
pytest tests/smoke/ -v
```

### Integration Tests (requires real HA)

```bash
export HA_URL=http://your-ha:8123
export HA_TOKEN=your_token
pytest tests/integration/ -v
```

### E2E Tests (requires real HA + REST API)

```bash
pytest tests/e2e/ -v
```

### All Tests

```bash
pytest tests/unit/ tests/smoke/ tests/e2e/ -v
```

### Coverage and static gates

```bash
pytest tests/unit/ -q --cov=tools --cov=context_generator.core --cov=context_generator.config --cov=context_generator.runtime --cov=context_generator.provenance --cov=context_generator.snapshot --cov-report=term-missing --cov-fail-under=80
ruff check .
ruff format --check .
mypy server.py tools/ context_generator/core.py context_generator/config.py context_generator/runtime.py context_generator/provenance.py context_generator/snapshot.py scripts/verify_runtime_endpoints.py --strict
bandit -r server.py tools/ context_generator/ ha_graph/ -ll
```

The legacy analyzer/formatter and graph modules have a deliberately scoped mypy override documented in `docs/testing-guidelines.md`; do not extend that exemption to new code.

### Hosted CI during branch iteration

The repository follows the pinned `ai-skills` cost-aware CI model. The current `CI` workflow supports a cheap manual path and an explicit full path:

```bash
# Fast quality/contracts feedback on the selected branch.
gh workflow run ci.yml --ref <branch>

# Full Python matrix, coverage policy, wheel, stdio, and container gate.
gh workflow run ci.yml --ref <branch> -f full=true
```

Do not run the hosted full matrix after every intermediate agent commit. Run local deterministic checks while iterating, then use the full hosted gate for an acceptance candidate. A full run is valid evidence only for the exact SHA that executed it; any later branch commit makes that evidence stale.

Automatic pull-request full gates remain enabled while newly introduced workflow paths complete GitHub's `workflow_dispatch` default-branch bootstrap. See `docs/testing-guidelines.md` for the migration and acceptance rules.

## Release Checklist

- [ ] All tests passing
- [ ] Code coverage > 80%
- [ ] Documentation updated
- [ ] Security review completed
- [ ] No hardcoded credentials
- [ ] `.env.example` updated
- [ ] `CHANGELOG.md` updated
