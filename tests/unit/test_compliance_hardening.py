from __future__ import annotations

import inspect
import json
import socket
from pathlib import Path
from starlette.testclient import TestClient

import pytest
import requests

import server
from context_generator import utils as context_utils
from context_generator.config import GenerationConfig
from context_generator.provenance import ProvenanceTracker
from context_generator.snapshot import ComprehensiveSnapshotCollector
from tools import config as config_tools
from tools import filesystem_explorer
from tools.invocation import InvocationError, InvocationKernel, Principal, principal_scope
from tools.manifests import get_all_manifests, get_manifest, set_active_tools
from tools.utils import make_ha_request


def test_empty_active_catalog_stays_empty() -> None:
    set_active_tools(set())
    assert get_all_manifests(active_only=True) == {}


def test_inactive_operation_is_not_executable() -> None:
    set_active_tools(set(), {"get_all_states": "backend unavailable"})
    kernel = InvocationKernel(max_workers=1, max_queue=0)
    principal = Principal(
        subject="reader",
        transport="test",
        capabilities=frozenset({"ha.read"}),
        targets=frozenset({"home_assistant"}),
    )
    with principal_scope(principal), pytest.raises(InvocationError) as exc:
        kernel.invoke_sync("get_all_states", lambda: {"ok": True})
    assert exc.value.code == "UNAVAILABLE_DEPENDENCY"
    assert "backend unavailable" in str(exc.value)


def test_kernel_enforces_declared_target_authorization() -> None:
    kernel = InvocationKernel(max_workers=1, max_queue=0)
    principal = Principal(
        subject="targetless",
        transport="test",
        capabilities=frozenset({"filesystem.read"}),
        targets=frozenset(),
    )
    with principal_scope(principal), pytest.raises(InvocationError) as exc:
        kernel.invoke_sync("list_directory", lambda: {"ok": True})
    assert exc.value.code == "FORBIDDEN"
    assert "target" in str(exc.value).lower()


def test_shared_http_client_does_not_retry_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def timeout(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise requests.exceptions.Timeout("timeout")

    real_session = requests.Session()
    real_session.get = timeout
    real_session.trust_env = False
    monkeypatch.setattr(requests, "Session", lambda: real_session)
    monkeypatch.setattr(
        "tools.utils.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))],
    )

    result = make_ha_request("http://ha", "token", "/api/states")
    assert result["success"] is False
    assert calls == 1


def test_shared_http_client_refuses_post_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("must not send"))
    with pytest.raises(ValueError, match="POST retries"):
        make_ha_request("http://ha", "token", "/api/template", method="POST", retries=2)


def test_config_prefilter_finds_match_after_four_mib(tmp_path: Path) -> None:
    path = tmp_path / "large.yaml"
    path.write_text("x" * (4 * 1024 * 1024 + 512) + "needle_late", encoding="utf-8")
    assert config_tools._file_mentions_any(str(path), ["needle_late"]) is True


def test_search_reports_actual_bounded_file_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    names = [f"f{i}.yaml" for i in range(config_tools._SEARCH_MAX_FILES + 5)]

    def fake_walk(path: object):
        yield str(tmp_path), [], names

    monkeypatch.setattr(config_tools.os, "walk", fake_walk)
    monkeypatch.setattr(config_tools, "_file_mentions_any", lambda *a, **k: False)
    result = config_tools._do_search_config_by_params(
        entity_id="sensor.demo", config_path=str(tmp_path)
    )
    summary = result["summary"]
    assert summary["files_discovered"] == config_tools._SEARCH_MAX_FILES + 5
    assert summary["files_searched"] == config_tools._SEARCH_MAX_FILES
    assert summary["search_truncated"] is True


def test_directory_listing_keeps_large_file_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    large = tmp_path / "large.yaml"
    large.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    context = filesystem_explorer.SecurityContext([tmp_path], max_file_size=10 * 1024 * 1024)
    monkeypatch.setattr(filesystem_explorer, "SECURITY_CONTEXT", context)
    result = filesystem_explorer._do_list_directory(str(tmp_path), 10)
    assert result["success"] is True
    row = next(item for item in result["entries"] if item["name"] == "large.yaml")
    assert row["size_bytes"] == large.stat().st_size


class _FakeMCP:
    def __init__(self) -> None:
        self.functions: dict[str, object] = {}

    def tool(self, **kwargs: object):
        def decorator(fn: object) -> object:
            self.functions[str(kwargs.get("name") or getattr(fn, "__name__"))] = fn
            return fn

        return decorator


def test_filesystem_defaults_follow_custom_config_root(tmp_path: Path) -> None:
    fake = _FakeMCP()
    filesystem_explorer.register_filesystem_tools(fake, str(tmp_path))
    list_fn = fake.functions["list_directory"]
    search_fn = fake.functions["search_files"]
    assert inspect.signature(list_fn).parameters["path"].default == str(tmp_path)
    assert inspect.signature(search_fn).parameters["search_path"].default == str(tmp_path)


def test_storage_snapshot_uses_positive_allowlist(tmp_path: Path) -> None:
    storage = tmp_path / ".storage"
    storage.mkdir()
    (storage / "core.area_registry").write_text(
        json.dumps(
            {
                "version": 1,
                "data": {
                    "areas": [
                        {
                            "id": "kitchen",
                            "name": "Kitchen",
                            "aliases": ["Cook room"],
                            "unexpected_secret": "must-not-appear",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (storage / "custom.integration").write_text(
        json.dumps({"data": {"opaque_credential": "supersecret"}}), encoding="utf-8"
    )
    config = GenerationConfig(
        config_path=tmp_path,
        output_path=tmp_path / "out.md",
        ha_url="",
        ha_token="",
        mode="offline",
    )
    provenance = ProvenanceTracker()
    snapshot = ComprehensiveSnapshotCollector(config, provenance).collect()
    files = snapshot["files"]["config_tree"]
    assert ".storage/core.area_registry" in files
    assert ".storage/custom.integration" not in files
    rendered = json.dumps(files)
    assert "unexpected_secret" not in rendered
    assert "must-not-appear" not in rendered
    assert "supersecret" not in rendered


def test_context_registry_loader_sanitizes_every_model_visible_storage_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / ".storage"
    storage.mkdir()
    (storage / "core.config_entries").write_text(
        json.dumps(
            {
                "version": 1,
                "data": {
                    "entries": [
                        {
                            "entry_id": "safe-id",
                            "domain": "demo",
                            "title": "Demo",
                            "data": {"api_key": "must-not-leak"},
                            "options": {"password": "must-not-leak-either"},
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (storage / "cloud").write_text(
        json.dumps({"data": {"refresh_token": "opaque-secret"}}), encoding="utf-8"
    )
    config = GenerationConfig(
        config_path=tmp_path,
        output_path=tmp_path / "out.md",
        ha_url="",
        ha_token="",
        mode="offline",
    )
    context_utils.invalidate_registry_cache()
    from context_generator.provenance import ProvenanceTracker
    from context_generator.runtime import GenerationRuntime, generation_scope

    with generation_scope(GenerationRuntime(config, ProvenanceTracker())):
        safe = context_utils.load_registry("core.config_entries", use_cache=False)
        blocked = context_utils.load_registry("cloud", use_cache=False)
    rendered = json.dumps(safe)
    assert "safe-id" in rendered
    assert "must-not-leak" not in rendered
    assert "password" not in rendered
    assert blocked == {}


def test_public_health_is_minimal_and_details_are_authenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(server.HEALTH_STATE, "ready", True)
    monkeypatch.setitem(
        server.HEALTH_STATE,
        "components",
        {
            "backend": "degraded",
            "catalog": "ready",
            "transport": "ready",
            "filesystem": "ready",
            "rest": "ready",
        },
    )
    app = server.create_rest_app(auth_token="test-token")
    with TestClient(app) as client:
        public = client.get("/api/health")
        assert public.status_code == 200
        assert public.json() == {"status": "ready", "version": server.__version__}
        assert client.get("/api/health/details").status_code == 401
        details = client.get("/api/health/details", headers={"Authorization": "Bearer test-token"})
        assert details.status_code == 200
        assert "components" in details.json()


def test_rest_invokes_application_owned_operation_registry() -> None:
    app = server.create_rest_app(auth_token="test-token")
    with TestClient(app) as client:
        response = client.post(
            "/api/tools/describe_ha_capabilities",
            headers={"Authorization": "Bearer test-token"},
            json={},
        )
    assert response.status_code == 200
    assert response.json()["success"] is True
    operation = server.get_tool("describe_ha_capabilities")
    assert operation is not None
    assert callable(operation.fn)
    assert callable(operation.raw_fn)


def test_manifest_protocol_claim_matches_fastmcp_lane() -> None:
    revisions = {tuple(m["protocol_revisions"]) for m in get_all_manifests().values()}
    assert revisions == {("2025-11-25",)}
    assert get_manifest("describe_ha_capabilities") is not None
