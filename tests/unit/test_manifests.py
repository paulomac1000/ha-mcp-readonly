"""Unit tests for tool manifests, factory functions, and risk prefix injection."""

import json

import pytest

from tools.manifests import (
    _TOOL_MANIFESTS,
    _inject_meta_envelope,
    _inject_risk_prefixes,
    _make_destructive_manifest,
    _make_write_manifest,
    auto_register_all_read_tools,
    get_all_manifests,
    get_manifest,
    make_manifest,
    register_manifest,
)
from tools.utils import _success_response, build_meta, sanitize_response_data


@pytest.fixture(autouse=True)
def _clear_manifests():
    """Reset TOOL_MANIFESTS before each test to ensure isolation."""
    _TOOL_MANIFESTS.clear()


class TestManifestFactories:
    """Tests for make_manifest, _make_write_manifest, _make_destructive_manifest."""

    def testmake_manifest_defaults(self):
        m = make_manifest("test_tool")
        assert m["name"] == "test_tool"
        assert m["risk"] == "READ"
        assert m["side_effects"] == "read"
        assert m["idempotent"] is True
        assert m["retryable"] is True
        assert m["requires_confirmation"] is False
        assert m["reversible"] is True
        assert m["impact"] == "none"

    def test_make_write_manifest(self):
        m = _make_write_manifest("write_tool")
        assert m["risk"] == "WRITE"
        assert m["side_effects"] == "write"
        assert m["requires_confirmation"] is True
        assert m["impact"] == "persistent"
        assert m["reversible"] is True

    def test_make_destructive_manifest(self):
        m = _make_destructive_manifest("delete_tool")
        assert m["risk"] == "DESTRUCTIVE"
        assert m["side_effects"] == "destructive"
        assert m["idempotent"] is False
        assert m["retryable"] is False
        assert m["requires_confirmation"] is True
        assert m["reversible"] is False
        assert m["impact"] == "service_outage"


class TestRiskConsistencyMatrix:
    """Verify each factory satisfies the Risk Consistency Matrix."""

    def test_read_factory_consistency(self):
        m = make_manifest("x")
        assert m["risk"] == "READ"
        assert m["side_effects"] in ("none", "read")
        assert m["idempotent"] is True
        assert m["retryable"] is True
        assert m["reversible"] is True
        assert m["requires_confirmation"] is False
        assert m["impact"] == "none"

    def test_write_factory_consistency(self):
        m = _make_write_manifest("y")
        assert m["risk"] == "WRITE"
        assert m["side_effects"] == "write"
        assert m["idempotent"] is True
        assert m["retryable"] is True
        assert m["reversible"] is True
        assert m["requires_confirmation"] is True
        assert m["impact"] in ("transient", "persistent")

    def test_destructive_factory_consistency(self):
        m = _make_destructive_manifest("z")
        assert m["risk"] == "DESTRUCTIVE"
        assert m["side_effects"] == "destructive"
        assert m["idempotent"] is False
        assert m["retryable"] is False
        assert m["reversible"] is False
        assert m["requires_confirmation"] is True
        assert m["impact"] in ("persistent", "service_outage")


class TestManifestRegistry:
    """Tests for register_manifest, get_manifest, get_all_manifests."""

    def test_register_and_get(self):
        register_manifest("demo_tool", make_manifest("demo_tool"))
        m = get_manifest("demo_tool")
        assert m is not None
        assert m["name"] == "demo_tool"
        assert m["risk"] == "READ"

    def test_get_missing_returns_none(self):
        assert get_manifest("does_not_exist") is None

    def test_get_all_returns_copy(self):
        register_manifest("a", make_manifest("a"))
        all_m = get_all_manifests()
        assert "a" in all_m
        assert isinstance(all_m, dict)

    def test_auto_register_all_read_tools(self):
        names = {"tool_one", "tool_two", "tool_three"}
        auto_register_all_read_tools(names)
        for name in names:
            m = get_manifest(name)
            assert m is not None, "missing manifest for " + name
            assert m["risk"] == "READ"


class TestInjectRiskPrefixes:
    """Tests for _inject_risk_prefixes dynamic prefix injection."""

    @staticmethod
    def _make_tool(doc: str, desc: str = ""):
        """Create a callable tool-like object with __doc__ and description."""

        def _inner():
            pass

        _inner.__doc__ = doc
        _inner.description = desc or doc.split("\n")[0].rstrip(".")
        return _inner

    def test_injects_prefix_when_none_present(self):
        tool = self._make_tool("Fetches data from Home Assistant.")
        register_manifest("test_inject", make_manifest("test_inject"))

        registered = {"test_inject": tool}
        _inject_risk_prefixes(registered)

        assert tool.__doc__.startswith("[READ]")
        assert "Fetches data" in tool.__doc__
        assert tool.description.startswith("[READ]")

    def test_replaces_existing_prefix(self):
        tool = self._make_tool("[READ] Original description.")
        register_manifest("test_replace", _make_write_manifest("test_replace"))

        registered = {"test_replace": tool}
        _inject_risk_prefixes(registered)

        assert tool.__doc__.startswith("[WRITE]")
        assert "[READ]" not in tool.__doc__

    def test_handles_empty_docstring(self):
        tool = self._make_tool("")
        register_manifest("test_empty", make_manifest("test_empty"))

        registered = {"test_empty": tool}
        _inject_risk_prefixes(registered)

        assert tool.__doc__ == "[READ] "

    def test_handles_missing_manifest_defaults_to_read(self):
        tool = self._make_tool("No manifest registered.")
        registered = {"test_missing_manifest": tool}
        _inject_risk_prefixes(registered)
        assert tool.__doc__.startswith("[READ]")

    def test_unwraps_tool_object_with_fn_attr(self):
        inner = self._make_tool("Inner function docstring.")

        class ToolWrapper:
            def __init__(self, fn):
                self.fn = fn

        tool = ToolWrapper(inner)

        register_manifest("test_wrapped", make_manifest("test_wrapped"))

        registered = {"test_wrapped": tool}
        _inject_risk_prefixes(registered)

        assert inner.__doc__.startswith("[READ]")


class TestBuildMeta:
    """Tests for build_meta envelope helper."""

    def test_returns_expected_keys(self):
        import time as _t

        start = _t.monotonic()
        meta = build_meta("test_tool", start)
        assert "duration_ms" in meta
        assert "tool_version" in meta
        assert isinstance(meta["duration_ms"], int)
        assert isinstance(meta["tool_version"], str)

    def test_duration_is_reasonable(self):
        import time as _t

        start = _t.monotonic()
        meta = build_meta("test_tool", start)
        assert meta["duration_ms"] >= 0
        assert meta["duration_ms"] < 1000

    def test_includes_request_id(self):
        import time as _t

        meta = build_meta("test_tool", _t.monotonic())
        assert "request_id" in meta


class _FakeTool:
    """Minimal tool-like object exposing an ``fn`` attribute."""

    def __init__(self, fn):
        self.fn = fn


class TestInjectMetaEnvelope:
    """Tests for the central _inject_meta_envelope wrapper."""

    def test_wraps_sync_tool_and_adds_meta(self):
        tool = _FakeTool(lambda: json.dumps({"success": True, "value": 1}))
        _inject_meta_envelope({"sync_tool": tool})
        parsed = json.loads(tool.fn())
        assert parsed["success"] is True
        assert parsed["value"] == 1
        assert {"request_id", "duration_ms", "tool_version"} <= set(parsed["_meta"])

    async def test_wraps_async_tool_and_adds_meta(self):
        async def _inner():
            return json.dumps({"success": True})

        tool = _FakeTool(_inner)
        _inject_meta_envelope({"async_tool": tool})
        parsed = json.loads(await tool.fn())
        assert "_meta" in parsed

    def test_does_not_double_wrap(self):
        tool = _FakeTool(lambda: json.dumps({"success": True}))
        _inject_meta_envelope({"once_tool": tool})
        first = tool.fn
        _inject_meta_envelope({"once_tool": tool})
        assert tool.fn is first

    def test_exception_propagates_not_caught(self):
        """Wrapper does NOT catch exceptions -- the tool's own handler must."""

        def _boom():
            raise RuntimeError("kaboom")

        tool = _FakeTool(_boom)
        _inject_meta_envelope({"boom_tool": tool})
        with pytest.raises(RuntimeError, match="kaboom"):
            tool.fn()

    def test_non_json_result_passed_through(self):
        tool = _FakeTool(lambda: "not json at all")
        _inject_meta_envelope({"plain_tool": tool})
        assert tool.fn() == "not json at all"


class TestSanitizeResponseData:
    """Tests for sanitize_response_data recursive sanitizer."""

    def test_redacts_bearer_token(self):
        dirty = {
            "auth": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0HmzUvZQ"
        }
        clean = sanitize_response_data(dirty)
        assert "Bearer eyJ" not in str(clean)
        assert "REDACTED" in str(clean)

    def test_redacts_ip_in_nested_dict(self):
        dirty = {"servers": [{"ip": "192.168.1.100", "name": "test"}]}
        clean = sanitize_response_data(dirty)
        assert "192.168.1.100" not in str(clean)
        assert "IP_REDACTED" in str(clean)

    def test_preserves_safe_data(self):
        dirty = {"name": "Living Room", "state": "on", "temperature": 22.5}
        clean = sanitize_response_data(dirty)
        assert clean["name"] == "Living Room"
        assert clean["state"] == "on"
        assert clean["temperature"] == 22.5

    def test_handles_plain_string(self):
        result = sanitize_response_data("Hello world")
        assert result == "Hello world"

    def test_handles_none(self):
        result = sanitize_response_data(None)
        assert result is None


class TestSuccessResponseWithMeta:
    """Tests for _success_response with _meta parameter."""

    def test_includes_meta_when_provided(self):
        meta = {"duration_ms": 42, "tool_version": "1.0.0"}
        result = _success_response({"key": "value"}, _meta=meta)
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["key"] == "value"
        assert "_meta" in parsed
        assert parsed["_meta"]["duration_ms"] == 42

    def test_no_meta_when_not_provided(self):
        result = _success_response({"key": "value"})
        parsed = json.loads(result)
        assert "_meta" not in parsed

    def test_sanitizes_data_in_response(self):
        dirty = {"log": "Bearer secret_token_here"}
        result = _success_response(dirty)
        parsed = json.loads(result)
        assert "secret_token_here" not in str(parsed)
        assert "REDACTED" in str(parsed)
