# Testing Guidelines — HA-MCP-Readonly

## Anti-Pattern: Blind Mocks

This is what failed in v1.1.0:

```python
# ❌ This test passes but proves nothing about real HA behavior
with patch("tools.automations.make_ha_request",
           return_value={"success": True, "data": [...]}):
    tool = mcp._tools["get_automation_traces"]
    data = json.loads(tool("automation.123"))
    assert data["success"] is True  # Always true - mock never fails
```

The real HA endpoint returned `404`, but the mock returned `success: true`.
**100% of 6 tests passed while the tool was completely broken in production.**

> **Status:** Planned enhancement. VCR cassette testing is not yet implemented.

## Required: VCR Cassette Tests

For every NEW tool that calls the HA REST API, include at least one test using
a recorded HTTP cassette (real response captured from a live HA instance).

### Setup

```bash
pip install vcrpy
```

Add `vcrpy` to `requirements-test.txt`.

### Writing a VCR Test

```python
import vcr
import json

@vcr.use_cassette("tests/cassettes/get_xyz.yaml")
def test_get_xyz_real_response(self, mock_mcp, config_path, ha_url, ha_token):
    register_tools(mock_mcp, config_path, ha_url, ha_token)
    tool = mock_mcp._tools["get_xyz"]
    data = json.loads(tool("some_entity"))
    assert data["success"] is True  # Actually verified against real HA
```

### Cassette Recording

1. Run the test against a real HA instance once → cassette is recorded to `tests/cassettes/`
2. Commit the cassette file to the repo
3. Subsequent CI runs replay the cassette (no live HA needed)

### Cassette Sanitization

Before committing, sanitize any sensitive data:

```python
import re

def sanitize_cassette(cassette_path):
    """Remove tokens, IPs, and personal data from cassette."""
    with open(cassette_path) as f:
        content = f.read()
    content = re.sub(r"Authorization: Bearer [^\n]+", "Authorization: Bearer REDACTED", content)
    content = re.sub(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+", "XXX.XXX.XXX.XXX", content)
    with open(cassette_path, "w") as f:
        f.write(content)
```

## Existing Tool Changes

When modifying an existing tool's HA API interaction:
- [ ] If the endpoint or response schema changes → update or re-record the cassette
- [ ] If only internal logic changes → existing mocks may still be acceptable
- [ ] Run `pytest tests/unit/ -v` and confirm all pass

## Test Categories

| Layer | Command | What it tests | Requires |
|-------|---------|--------------|----------|
| Unit (pure logic) | `pytest tests/unit/ -m "not cassette"` | YAML parsing, internal logic, edge cases | Nothing |
| VCR (API) | `pytest tests/unit/ -m cassette` | Real HA API responses (replayed) | `vcrpy` |
| Integration | Manual / on-demand | Live HA instance | Running HA |

## CI Configuration

In `.github/workflows/ci.yml`, VCR tests should run as part of the test job.
Mark cassette tests with `@pytest.mark.cassette` and run them separately if
cassette recording is slow.

```bash
# In CI
pip install vcrpy
pytest tests/unit/ -v --tb=short  # includes cassette tests
```
