"""Fail-closed canonical capability manifests and runtime wrapper injection."""

from __future__ import annotations

import functools
import inspect
import json
import time
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tools import TOOLS_VERSION
from tools.invocation import KERNEL

KNOWN_RISK_PREFIXES = frozenset(
    {"[READ]", "[WRITE]", "[DANGEROUS]", "[DESTRUCTIVE]", "[SENSITIVE]"}
)


class ConcurrencyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal[
        "global",
        "principal",
        "target",
        "credential",
        "resource",
        "capability",
        "principal-target",
        "custom",
    ]
    limit: int = Field(ge=1, le=10_000)
    queue_limit: int | None = Field(default=None, ge=0, le=100_000)
    global_limit: int | None = Field(default=None, ge=1, le=10_000)


class ApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enforcement: Literal["server-side"] = "server-side"
    record_required: Literal[True] = True
    record_ttl_seconds: int = Field(ge=1, le=86_400)
    binds: list[
        Literal[
            "principal", "capability", "target", "arguments-digest", "expires-at", "approval-id"
        ]
    ]


class ToolManifest(BaseModel):
    """Application-owned canonical operation contract plus bounded runtime extensions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2048)
    operation_kind: Literal["read", "write", "destructive"]
    risk: Literal["low", "medium", "high", "critical"]
    determinism: Literal["deterministic", "environment-dependent", "nondeterministic"]
    latency: Literal["interactive", "bounded-long", "background"]
    impact: Literal["none", "local", "external", "cross-tenant"]
    active_state: Literal["active", "inactive", "deprecated"]
    retryable: bool
    idempotent: bool
    reversible: bool
    requires_confirmation: bool
    idempotency_key_required: bool
    authorization_scopes: list[str]
    approval: ApprovalPolicy | None = None
    concurrency: ConcurrencyPolicy
    max_response_bytes: int = Field(ge=1, le=16 * 1024 * 1024)
    protocol_revisions: list[str] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_semantics(self) -> ToolManifest:
        if self.active_state != "active" and self.retryable:
            raise ValueError("inactive/deprecated manifests cannot be retryable")
        if self.operation_kind == "read":
            if self.impact != "none":
                raise ValueError("read operations must declare impact=none")
            if any((self.retryable, self.idempotent, self.reversible, self.requires_confirmation)):
                raise ValueError(
                    "read operations cannot infer retry/idempotency/reversibility/confirmation"
                )
            if self.idempotency_key_required:
                raise ValueError("read operations cannot require an idempotency key")
        if self.operation_kind in {"write", "destructive"} and not self.authorization_scopes:
            raise ValueError("write/destructive operations require authorization scopes")
        if self.operation_kind == "destructive" and not self.requires_confirmation:
            raise ValueError("destructive operations require confirmation")
        if self.retryable and (not self.idempotent or not self.idempotency_key_required):
            raise ValueError("retryable operations require idempotency and an idempotency key")
        if self.requires_confirmation:
            if self.approval is None:
                raise ValueError("confirmation requires a server-side approval policy")
            required = {"principal", "capability", "target", "arguments-digest", "expires-at"}
            if not required.issubset(self.approval.binds):
                raise ValueError("approval policy does not bind all required fields")
        elif self.approval is not None:
            raise ValueError("approval policy is only valid when confirmation is required")
        timeout = self.extensions.get("timeout_ms")
        if not isinstance(timeout, int) or not 100 <= timeout <= 120_000:
            raise ValueError("extensions.timeout_ms must be between 100 and 120000")
        if not isinstance(self.extensions.get("target_binding"), dict):
            raise ValueError("extensions.target_binding is required")
        if not isinstance(self.extensions.get("retry_conditions"), dict):
            raise ValueError("extensions.retry_conditions is required")
        if not isinstance(self.extensions.get("outcome_semantics"), dict):
            raise ValueError("extensions.outcome_semantics is required")
        if not isinstance(self.extensions.get("sdk_profile"), dict):
            raise ValueError("extensions.sdk_profile is required")
        return self


_TOOL_MANIFESTS: dict[str, dict[str, Any]] = {}
_ACTIVE_TOOL_NAMES: frozenset[str] = frozenset()


def _load_declared_manifests() -> None:
    manifest_path = Path(__file__).with_name("tool_manifests.json")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("tool_manifests.json must contain an object")
    for name, value in raw.items():
        register_manifest(name, value)


def register_manifest(name: str, manifest: dict[str, Any]) -> None:
    """Validate and register a manifest; invalid declarations stop startup."""
    candidate = dict(manifest)
    candidate.setdefault("id", name)
    candidate.setdefault("name", name)
    if candidate["name"] != name or candidate["id"] != name:
        raise ValueError(f"Manifest name mismatch: expected {name!r}")
    try:
        validated = ToolManifest.model_validate(candidate)
    except ValidationError as exc:
        raise ValueError(f"Invalid manifest for {name}: {exc}") from exc
    _TOOL_MANIFESTS[name] = validated.model_dump(exclude_none=True)


def get_manifest(name: str) -> dict[str, Any] | None:
    manifest = _TOOL_MANIFESTS.get(name)
    return dict(manifest) if manifest is not None else None


def get_all_manifests(*, active_only: bool = False) -> dict[str, dict[str, Any]]:
    names = _ACTIVE_TOOL_NAMES if active_only and _ACTIVE_TOOL_NAMES else _TOOL_MANIFESTS.keys()
    return {name: dict(_TOOL_MANIFESTS[name]) for name in names}


def set_active_tools(tool_names: set[str]) -> None:
    global _ACTIVE_TOOL_NAMES
    missing = sorted(tool_names - _TOOL_MANIFESTS.keys())
    if missing:
        raise RuntimeError("Missing explicit tool manifests: " + ", ".join(missing))
    _ACTIVE_TOOL_NAMES = frozenset(tool_names)


def _runtime_extensions(
    *, timeout_ms: int, target: str, data_classification: str, cost: str
) -> dict[str, Any]:
    return {
        "tool_version": TOOLS_VERSION,
        "timeout_ms": timeout_ms,
        "data_classification": data_classification,
        "target_binding": {
            "kind": "deployment-resource",
            "target": target,
            "revalidation": "per-invocation",
        },
        "idempotency_mechanism": "not-claimed-for-read-operation",
        "retry_conditions": {
            "eligible_errors": [],
            "attempts": 1,
            "backoff": "none",
            "reconciliation": "not-applicable",
        },
        "outcome_semantics": {
            "ambiguous": "fail-closed-no-retry",
            "unknown": "fail-closed-no-retry",
        },
        "sdk_profile": {
            "implementation": "fastmcp",
            "version_range": ">=3.4.6,<3.5",
            "transports": ["stdio", "streamable-http"],
        },
        "cost": cost,
    }


def make_manifest(
    name: str,
    timeout_ms: int = 15_000,
    latency: str = "bounded-long",
    cost: str = "cheap",
) -> dict[str, Any]:
    """Build a canonical READ contract without inferring positive safety properties."""
    normalized_latency = (
        latency if latency in {"interactive", "bounded-long", "background"} else "bounded-long"
    )
    return ToolManifest(
        id=name,
        name=name,
        description=f"Read-only Home Assistant capability '{name}'.",
        operation_kind="read",
        risk="low",
        determinism="environment-dependent",
        latency=cast(Literal["interactive", "bounded-long", "background"], normalized_latency),
        impact="none",
        active_state="active",
        retryable=False,
        idempotent=False,
        reversible=False,
        requires_confirmation=False,
        idempotency_key_required=False,
        authorization_scopes=["ha.read"],
        concurrency=ConcurrencyPolicy(scope="capability", limit=8),
        max_response_bytes=2 * 1024 * 1024,
        protocol_revisions=["2025-06-18"],
        extensions=_runtime_extensions(
            timeout_ms=timeout_ms,
            target="home_assistant",
            data_classification="internal",
            cost=cost,
        ),
    ).model_dump(exclude_none=True)


def _mutation_manifest(
    name: str, *, destructive: bool, timeout_ms: int, latency: str
) -> dict[str, Any]:
    operation = "destructive" if destructive else "write"
    return ToolManifest(
        id=name,
        name=name,
        description=f"{operation.title()} Home Assistant capability '{name}'.",
        operation_kind=cast(Literal["write", "destructive"], operation),
        risk="high" if destructive else "medium",
        determinism="environment-dependent",
        latency=cast(Literal["interactive", "bounded-long", "background"], latency),
        impact="external",
        active_state="active",
        retryable=False,
        idempotent=False,
        reversible=False,
        requires_confirmation=True,
        idempotency_key_required=False,
        authorization_scopes=["ha.write"],
        approval=ApprovalPolicy(
            record_ttl_seconds=300,
            binds=["principal", "capability", "target", "arguments-digest", "expires-at"],
        ),
        concurrency=ConcurrencyPolicy(scope="target", limit=1),
        max_response_bytes=2 * 1024 * 1024,
        protocol_revisions=["2025-06-18"],
        extensions={
            **_runtime_extensions(
                timeout_ms=timeout_ms,
                target="home_assistant",
                data_classification="internal",
                cost="expensive" if destructive else "moderate",
            ),
            "idempotency_mechanism": "not-claimed",
        },
    ).model_dump(exclude_none=True)


def _make_write_manifest(
    name: str, timeout_ms: int = 15_000, latency: str = "bounded-long"
) -> dict[str, Any]:
    return _mutation_manifest(name, destructive=False, timeout_ms=timeout_ms, latency=latency)


def _make_destructive_manifest(
    name: str, timeout_ms: int = 30_000, latency: str = "bounded-long"
) -> dict[str, Any]:
    return _mutation_manifest(name, destructive=True, timeout_ms=timeout_ms, latency=latency)


def auto_register_all_read_tools(tool_names: set[str]) -> None:
    """Compatibility name for fail-closed manifest coverage validation."""
    set_active_tools(tool_names)


def _raw_function(tool: Any) -> Any:
    raw = tool
    for attr in ("fn", "func", "_func", "function"):
        inner = getattr(tool, attr, None)
        if callable(inner):
            return inner
    return raw


def _inject_risk_prefixes(registered_tools: dict[str, Any]) -> None:
    prefixes = {"read": "READ", "write": "WRITE", "destructive": "DESTRUCTIVE"}
    for name, tool in registered_tools.items():
        manifest = _TOOL_MANIFESTS.get(name)
        if manifest is None:
            raise RuntimeError(f"Missing explicit manifest for {name}")
        risk = prefixes[str(manifest["operation_kind"])]
        raw_fn = _raw_function(tool)
        doc = (raw_fn.__doc__ or "").strip()
        for prefix in KNOWN_RISK_PREFIXES:
            if doc.startswith(prefix):
                doc = doc[len(prefix) :].lstrip()
                break
        new_doc = f"[{risk}] {doc}".rstrip()
        raw_fn.__doc__ = new_doc
        if hasattr(tool, "description"):
            tool.description = new_doc.split("\n", maxsplit=1)[0].rstrip(".")


def _augment_result(result: Any, tool_name: str, start: float) -> Any:
    from tools.utils import build_meta

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            KERNEL.enforce_final_result_size(tool_name, result)
            return result
        if isinstance(parsed, dict):
            parsed["_meta"] = build_meta(tool_name, start)
            encoded = json.dumps(parsed, indent=2, ensure_ascii=False)
            KERNEL.enforce_final_result_size(tool_name, encoded)
            return encoded
        KERNEL.enforce_final_result_size(tool_name, result)
        return result
    if isinstance(result, dict):
        enriched = dict(result)
        enriched["_meta"] = build_meta(tool_name, start)
        KERNEL.enforce_final_result_size(tool_name, enriched)
        return enriched
    KERNEL.enforce_final_result_size(tool_name, result)
    return result


def _make_meta_wrapper(raw_fn: Any, tool_name: str) -> Any:
    from tools.observability import increment_invocation, start_tool_context

    if inspect.iscoroutinefunction(raw_fn):

        @functools.wraps(raw_fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            start_tool_context()
            increment_invocation(tool_name)
            result = await KERNEL.invoke_async(tool_name, raw_fn, *args, **kwargs)
            return _augment_result(result, tool_name, start)

        setattr(async_wrapper, "_meta_wrapped", True)
        return async_wrapper

    @functools.wraps(raw_fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        start_tool_context()
        increment_invocation(tool_name)
        result = KERNEL.invoke_sync(tool_name, raw_fn, *args, **kwargs)
        return _augment_result(result, tool_name, start)

    setattr(sync_wrapper, "_meta_wrapped", True)
    return sync_wrapper


def _inject_meta_envelope(registered_tools: dict[str, Any]) -> None:
    for name, tool in registered_tools.items():
        if name not in _TOOL_MANIFESTS:
            raise RuntimeError(f"Missing explicit manifest for {name}")
        for attr in ("fn", "func", "_func", "function"):
            inner = getattr(tool, attr, None)
            if callable(inner) and not getattr(inner, "_meta_wrapped", False):
                setattr(tool, attr, _make_meta_wrapper(inner, name))
                break


_load_declared_manifests()
