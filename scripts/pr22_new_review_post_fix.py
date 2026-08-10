#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def save(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


# README and CHANGELOG intentionally keep their native user/release formats. AFDS
# governs AGENTS, CONTRIBUTING, SECURITY and every Markdown file below docs/.
makefile = load("Makefile")
makefile = makefile.replace(
    'python3 $(AFDS_VALIDATOR) AGENTS.md CHANGELOG.md CONTRIBUTING.md README.md SECURITY.md $$docs',
    'python3 $(AFDS_VALIDATOR) AGENTS.md CONTRIBUTING.md SECURITY.md $$docs',
)
save("Makefile", makefile)

agents = load("AGENTS.md")
agents = agents.replace(
    '1. **Unit tests:** Zero I/O, all dependencies mocked via `unittest.mock.patch`. Run without credentials.',
    '1. **Unit tests:** No network, Home Assistant, operator-filesystem, or external-service I/O. Ephemeral `tmp_path` I/O is allowed only when filesystem/path semantics are the behavior under test; all other dependencies are mocked. Run without credentials.',
)
agents = agents.replace(
    '     whether an endpoint is public and therefore usable with a long-lived token.\n   - If the endpoint is NOT listed there, it is **not a public REST API endpoint**',
    '     whether an endpoint is part of the documented public REST surface. Documentation establishes API shape, not the privileges of a particular token.\n   - If the endpoint is NOT listed there, do not claim public REST support without separate authoritative evidence.',
)
agents = agents.replace(
    '   If it returns `404` or `401`, the endpoint is not accessible via LLAT.',
    '   Use the same class of LLAT intended for production. Support is documented only after this request succeeds with that credential class; `401`/`403`/`404` means the LLAT-access claim is not verified.',
)
agents = re.sub(
    r'- Validate docs: `[^`]+`\n- Reference: `scripts/vendor/afds_validate_c6dc6b13\.py` \(vendored validator pinned to the ai-skills revision in `ai-skills\.lock\.yaml`\)',
    '- Validate governed docs: `make docs-check`\n- `README.md` and `CHANGELOG.md` are explicit AFDS exceptions: README keeps normal user-facing Markdown and CHANGELOG follows Keep a Changelog; both remain subject to their separate repository checks.\n- Reference: `scripts/vendor/afds_validate_b54fc6b2.py` (vendored validator pinned to `b54fc6b27ea80b36a70d5de73445970e17f55789` in `ai-skills.lock.yaml`)',
    agents,
    count=1,
)
agents = agents.replace(
    '- `pre-commit run --all-files` passes without skips, and `CHANGELOG.md` records the\n  change under the unreleased section.',
    '- `pre-commit run --all-files` and `make docs-check` pass without skips, and `CHANGELOG.md` records the change under the unreleased section.\n- For every newly supported Home Assistant REST/WebSocket surface, official documentation establishes the API shape, the intended LLAT class succeeds against a real instance, a sanitized recorded upstream cassette covers the request, and protocol/smoke coverage verifies catalog/capability consistency.',
)
save("AGENTS.md", agents)

# dotenv-linter requires the host-side path before the in-container path.
env = load(".env.example")
env = env.replace(
    'HA_CONFIG_PATH=/config\n# Host-side directory mounted read-only by docker-compose.yml\nHA_CONFIG_HOST_PATH=/absolute/path/to/home-assistant/config',
    '# Host-side directory mounted read-only by docker-compose.yml\nHA_CONFIG_HOST_PATH=/absolute/path/to/home-assistant/config\nHA_CONFIG_PATH=/config',
)
save(".env.example", env)

# Resolve per-parameter forward references through Pydantic's namespace rebuild
# instead of Python eval(). A single unresolved name therefore degrades only that
# parameter to string while unrelated annotations retain their real schemas.
save(
    "tools/schema_utils.py",
    '''"""Application-owned JSON-schema helpers for callable signatures."""

from __future__ import annotations

import inspect
from typing import Any, get_type_hints

from pydantic import TypeAdapter
from pydantic.errors import PydanticSchemaGenerationError


def _resolved_annotations(function: Any) -> dict[str, Any]:
    """Resolve annotations wholesale when possible, otherwise retain raw values."""
    raw = inspect.get_annotations(function, eval_str=False)
    try:
        resolved = get_type_hints(function, include_extras=True)
    except (NameError, TypeError):
        return raw
    return {**raw, **resolved}


def _parameter_schema(annotation: Any, namespace: dict[str, Any]) -> dict[str, Any]:
    if annotation is inspect.Signature.empty:
        return {"type": "string"}
    try:
        adapter = TypeAdapter(annotation)
        if isinstance(annotation, str):
            rebuilt = adapter.rebuild(
                force=True,
                raise_errors=False,
                _types_namespace=namespace,
            )
            if rebuilt is False:
                return {"type": "string"}
        return adapter.json_schema()
    except PydanticSchemaGenerationError:
        return {"type": "string"}


def _merge_defs(root: dict[str, Any], parameter_schema: dict[str, Any]) -> dict[str, Any]:
    """Lift TypeAdapter definitions so local #/$defs references resolve from the root schema."""
    schema = dict(parameter_schema)
    definitions = schema.pop("$defs", None)
    if not isinstance(definitions, dict):
        return schema
    root_definitions = root.setdefault("$defs", {})
    for name, definition in definitions.items():
        existing = root_definitions.get(name)
        if existing is not None and existing != definition:
            raise ValueError(f"Conflicting JSON-schema definition: {name}")
        root_definitions[name] = definition
    return schema


def signature_to_json_schema(function: Any) -> dict[str, Any]:
    """Build a JSON schema while preserving unions, generics, refs, and Annotated metadata."""
    signature = inspect.signature(function)
    annotations = _resolved_annotations(function)
    namespace = dict(getattr(function, "__globals__", {}))
    schema: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
    required: list[str] = []

    for name, parameter in signature.parameters.items():
        if name == "self":
            continue
        annotation = annotations.get(name, parameter.annotation)
        property_schema = _merge_defs(schema, _parameter_schema(annotation, namespace))
        if parameter.default is inspect.Signature.empty:
            required.append(name)
        else:
            property_schema["default"] = parameter.default
        schema["properties"][name] = property_schema

    if required:
        schema["required"] = required
    return schema
''',
)

# Pin the transport in every settings test that is intended to reach host/CORS validation.
settings_test = load("tests/unit/test_settings.py")
for function_name in ("test_wildcard_cors_is_rejected", "test_wildcard_host_is_rejected"):
    pattern = rf'(def {function_name}\(monkeypatch: pytest\.MonkeyPatch\) -> None:\n)(?!    monkeypatch\.setenv\("MCP_TRANSPORT")'
    settings_test = re.sub(
        pattern,
        r'\1    monkeypatch.setenv("MCP_TRANSPORT", "stdio")\n',
        settings_test,
        count=1,
    )
save("tests/unit/test_settings.py", settings_test)

# Isolate all inherited environment variables in the port-boundary regression.
review_test = load("tests/unit/test_review_regressions.py")
if "from unittest.mock import patch" not in review_test:
    review_test = review_test.replace("import pytest\n", "import os\nfrom unittest.mock import patch\n\nimport pytest\n", 1)
review_test = re.sub(
    r'''@pytest\.mark\.parametrize\("name", \["HEALTH_CHECK_PORT", "MCP_PORT", "REST_API_PORT"\]\)\n@pytest\.mark\.parametrize\("value", \["0", "70000"\]\)\ndef test_runtime_ports_reject_out_of_range\(\n    monkeypatch: pytest\.MonkeyPatch, name: str, value: str\n\) -> None:\n    monkeypatch\.setenv\("MCP_TRANSPORT", "stdio"\)\n    monkeypatch\.setenv\(name, value\)\n    with pytest\.raises\(ValueError, match=name\):\n        RuntimeSettings\.from_env\(\)''',
    '''@pytest.mark.parametrize("name", ["HEALTH_CHECK_PORT", "MCP_PORT", "REST_API_PORT"])
@pytest.mark.parametrize("value", ["0", "70000"])
def test_runtime_ports_reject_out_of_range(name: str, value: str) -> None:
    with patch.dict(os.environ, {"MCP_TRANSPORT": "stdio", name: value}, clear=True):
        with pytest.raises(ValueError, match=name):
            RuntimeSettings.from_env()''',
    review_test,
    count=1,
)
save("tests/unit/test_review_regressions.py", review_test)

# The async hardening assertion should be ordering-based, not runner-speed based.
async_test = load("tests/unit/test_operation_async_hardening.py")
if "elapsed <" in async_test:
    async_test = re.sub(
        r'''    start = time\.monotonic\(\)\n    invocation = asyncio\.create_task\(operation\.fn\(\)\)\n    await asyncio\.sleep\(0\.01\)\n    elapsed = time\.monotonic\(\) - start\n    result = json\.loads\(await invocation\)\n    assert elapsed < [0-9.]+\n    assert result\["success"\] is True''',
        '''    order: list[str] = []

    async def probe() -> None:
        await asyncio.sleep(0)
        order.append("probe")

    invocation = asyncio.create_task(operation.fn())
    probe_task = asyncio.create_task(probe())
    await asyncio.wait_for(probe_task, timeout=1)
    result = json.loads(await asyncio.wait_for(invocation, timeout=1))
    order.append("finished")
    assert order[0] == "probe"
    assert result["success"] is True''',
        async_test,
        count=1,
    )
save("tests/unit/test_operation_async_hardening.py", async_test)

print("Applied final review post-fixes without eval")
