"""Tests for fail-closed operation manifests and wrapper injection."""

import asyncio
import json
from dataclasses import dataclass

import pytest

from tools.invocation import LOCAL_PRINCIPAL, InvocationError, principal_scope
from tools.manifests import (
    _inject_meta_envelope,
    _inject_risk_prefixes,
    _make_destructive_manifest,
    _make_write_manifest,
    auto_register_all_read_tools,
    get_all_manifests,
    get_manifest,
    make_manifest,
    register_manifest,
    set_active_tools,
)


@dataclass
class FakeTool:
    fn: object
    description: str = ""


def _register(name: str, **updates: object) -> None:
    manifest = make_manifest(name)
    manifest.update(updates)
    register_manifest(name, manifest)


def test_read_manifest_declares_enforced_policy() -> None:
    manifest = make_manifest("manifest_read", timeout_ms=1200, latency="fast")
    assert manifest["operation_kind"] == "read"
    assert manifest["risk"] == "low"
    assert manifest["impact"] == "none"
    assert manifest["retryable"] is False
    assert manifest["idempotent"] is False
    assert manifest["reversible"] is False
    assert manifest["extensions"]["timeout_ms"] == 1200
    assert manifest["authorization_scopes"] == ["ha.read"]


def test_write_and_destructive_factories_are_not_retryable() -> None:
    write = _make_write_manifest("manifest_write")
    destructive = _make_destructive_manifest("manifest_destructive")
    assert write["operation_kind"] == "write"
    assert write["idempotent"] is False
    assert write["retryable"] is False
    assert write["requires_confirmation"] is True
    assert destructive["operation_kind"] == "destructive"
    assert destructive["reversible"] is False
    assert destructive["impact"] == "external"


def test_invalid_manifest_is_rejected() -> None:
    invalid = make_manifest("invalid_manifest")
    invalid["concurrency"]["limit"] = 0
    with pytest.raises(ValueError, match="Invalid manifest"):
        register_manifest("invalid_manifest", invalid)


def test_manifest_name_mismatch_is_rejected() -> None:
    manifest = make_manifest("actual_name")
    with pytest.raises(ValueError, match="name mismatch"):
        register_manifest("different_name", manifest)


def test_unknown_tool_stops_coverage_validation() -> None:
    with pytest.raises(RuntimeError, match="Missing explicit tool manifests"):
        auto_register_all_read_tools({"definitely_not_declared"})


def test_active_catalog_is_an_explicit_subset() -> None:
    names = {"get_entity_state", "describe_ha_capabilities"}
    set_active_tools(names)
    assert set(get_all_manifests(active_only=True)) == names


def test_risk_prefix_is_injected_from_manifest() -> None:
    name = "prefix_manifest_test"
    _register(name)

    def operation() -> str:
        """Return a value."""
        return "ok"

    tool = FakeTool(operation)
    _inject_risk_prefixes({name: tool})
    assert operation.__doc__ == "[READ] Return a value."
    assert tool.description == "[READ] Return a value"


def test_risk_injection_fails_without_manifest() -> None:
    with pytest.raises(RuntimeError, match="Missing explicit manifest"):
        _inject_risk_prefixes({"missing_prefix_manifest": FakeTool(lambda: None)})


def test_sync_wrapper_routes_through_kernel_and_adds_meta() -> None:
    name = "sync_manifest_wrapper"
    _register(name)

    def operation(value: int = 1) -> str:
        return json.dumps({"success": True, "value": value})

    tool = FakeTool(operation)
    _inject_meta_envelope({name: tool})
    with principal_scope(LOCAL_PRINCIPAL):
        result = json.loads(tool.fn(value=7))  # type: ignore[operator]
    assert result["value"] == 7
    assert result["_meta"]["tool_version"]


def test_async_wrapper_routes_through_kernel_and_adds_meta() -> None:
    name = "async_manifest_wrapper"
    _register(name)

    async def operation() -> dict[str, object]:
        return {"success": True}

    tool = FakeTool(operation)
    _inject_meta_envelope({name: tool})

    async def verify() -> None:
        with principal_scope(LOCAL_PRINCIPAL):
            result = await tool.fn()  # type: ignore[operator]
        assert result["_meta"]["request_id"]

    asyncio.run(verify())


def test_final_envelope_is_included_in_response_size_limit() -> None:
    name = "final_envelope_response_limit"
    _register(name, max_response_bytes=60)

    def operation() -> dict[str, object]:
        return {"success": True}

    tool = FakeTool(operation)
    _inject_meta_envelope({name: tool})
    with principal_scope(LOCAL_PRINCIPAL), pytest.raises(InvocationError) as caught:
        tool.fn()  # type: ignore[operator]
    assert caught.value.code == "RESPONSE_TOO_LARGE"


def test_wrapper_is_not_applied_twice() -> None:
    name = "single_manifest_wrapper"
    _register(name)
    calls = 0

    def operation() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"success": True}

    tool = FakeTool(operation)
    _inject_meta_envelope({name: tool})
    first = tool.fn
    _inject_meta_envelope({name: tool})
    assert tool.fn is first
    with principal_scope(LOCAL_PRINCIPAL):
        tool.fn()  # type: ignore[operator]
    assert calls == 1


def test_seeded_catalog_contains_filesystem_classification() -> None:
    manifest = get_manifest("read_file")
    assert manifest is not None
    assert manifest["authorization_scopes"] == ["filesystem.read"]
    assert manifest["extensions"]["data_classification"] == "sensitive"


def test_manifest_contract_exposes_canonical_operation_fields() -> None:
    manifest = get_manifest("get_entity_state")
    assert manifest is not None
    assert manifest["schema_version"] == 1
    assert manifest["active_state"] == "active"
    assert manifest["concurrency"]["scope"] == "capability"
    assert manifest["concurrency"]["limit"] >= 1
    assert manifest["extensions"]["target_binding"]["revalidation"] == "per-invocation"
    assert manifest["extensions"]["retry_conditions"]["attempts"] == 1
    assert manifest["extensions"]["outcome_semantics"]["ambiguous"] == "fail-closed-no-retry"


def test_read_contract_does_not_infer_positive_safety_properties() -> None:
    manifest = make_manifest("read_contract_no_inference")
    assert manifest["retryable"] is False
    assert manifest["idempotent"] is False
    assert manifest["reversible"] is False
