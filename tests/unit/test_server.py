"""Tests for the optional authenticated REST compatibility adapter."""

import json
from starlette.testclient import TestClient
from unittest.mock import patch

import pytest

import server
from tests.fixtures import MOCK_SAMPLE_STATES

TOKEN = "unit-test-bearer-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _mock_make_ha_request(ha_url, ha_token, endpoint, **kwargs):
    if endpoint == "/api/states":
        return {"success": True, "data": list(MOCK_SAMPLE_STATES)}
    if endpoint.startswith("/api/states/"):
        entity_id = endpoint.rsplit("/", 1)[-1]
        return {
            "success": True,
            "data": {
                "entity_id": entity_id,
                "state": "on",
                "attributes": {},
                "last_changed": "2026-01-01T00:00:00+00:00",
                "last_updated": "2026-01-01T00:00:00+00:00",
            },
        }
    return {"success": False, "error": "unhandled test endpoint"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(server.create_rest_app(TOKEN))


def test_health_is_public_but_catalog_requires_auth(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    response = client.get("/api/tools")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_authenticated_catalog_and_manifest(client: TestClient) -> None:
    response = client.get("/api/tools?detail=full", headers=AUTH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == len(server.get_all_tools())
    assert all(item["manifest"] for item in payload["tools"])
    manifest = client.get("/api/tools/read_file/manifest", headers=AUTH).json()["manifest"]
    assert manifest["authorization_scopes"] == ["filesystem.read"]


def test_rest_invocation_uses_wrapped_kernel(client: TestClient) -> None:
    with patch("tools.states.make_ha_request", side_effect=_mock_make_ha_request):
        response = client.post(
            "/api/tools/get_entity_state",
            headers=AUTH,
            json={"entity_id": "light.living_room"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    result = payload["result"]
    if isinstance(result, str):
        result = json.loads(result)
    from version import __version__

    assert result["_meta"]["tool_version"] == __version__


def test_rest_rejects_invalid_json_and_arguments(client: TestClient) -> None:
    invalid_json = client.post(
        "/api/tools/get_entity_state",
        headers={**AUTH, "Content-Type": "application/json"},
        content=b"not-json",
    )
    assert invalid_json.status_code == 400
    invalid_args = client.post("/api/tools/get_entity_state", headers=AUTH, json={})
    assert invalid_args.status_code == 400


def test_unknown_tool_returns_stable_error(client: TestClient) -> None:
    response = client.post("/api/tools/does_not_exist", headers=AUTH, json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_cors_preflight_does_not_require_bearer_token(client: TestClient) -> None:
    response = client.options(
        "/api/tools",
        headers={
            "Origin": "http://localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost"


def test_internal_type_error_is_not_reported_as_invalid_arguments(client: TestClient) -> None:
    def broken(value: str) -> str:
        del value
        raise TypeError("internal implementation failure")

    with patch("server.get_tool", return_value=broken):
        response = client.post("/api/tools/broken", headers=AUTH, json={"value": "x"})

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "Tool invocation failed",
    }


def test_authenticated_rest_request_binds_expected_principal(client: TestClient) -> None:
    from tools.invocation import current_principal

    def report_principal() -> dict:
        principal = current_principal()
        return {"subject": principal.subject, "targets": sorted(principal.targets)}

    with patch("server.get_tool", return_value=report_principal):
        response = client.post("/api/tools/principal", headers=AUTH, json={})

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["subject"] == "authenticated-rest-client"
    assert result["targets"] == ["home_assistant", "home_assistant_config", "runtime"]


def test_sync_rest_tool_runs_off_event_loop(client: TestClient) -> None:
    import threading

    observed = {}

    def sync_tool() -> dict:
        observed["thread"] = threading.current_thread().name
        return {"ok": True}

    with patch("server.get_tool", return_value=sync_tool):
        response = client.post("/api/tools/sync", headers=AUTH, json={})

    assert response.status_code == 200
    assert response.json()["result"] == {"ok": True}
    assert "asyncio-portal" not in observed["thread"]


def test_every_registered_tool_has_metadata_and_openapi_path(client: TestClient) -> None:
    names = sorted(server.get_all_tools())
    assert len(names) == server.get_tool_count()
    for name in names:
        manifest = client.get(f"/api/tools/{name}/manifest", headers=AUTH)
        schema = client.get(f"/api/tools/{name}/schema", headers=AUTH)
        assert manifest.status_code == 200, name
        assert schema.status_code == 200, name
        assert manifest.json()["manifest"]["name"] == name
        assert schema.json()["name"] == name

    openapi = client.get("/api/openapi.json", headers=AUTH)
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    assert all(f"/api/tools/{name}" in paths for name in names)


def test_rest_rejects_oversized_body_before_json_decode(client: TestClient) -> None:
    response = client.post(
        "/api/tools/get_entity_state",
        headers={**AUTH, "Content-Type": "application/json"},
        content=b"x" * (1024 * 1024 + 1),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_context_generation_deadline_terminates_child(monkeypatch, tmp_path) -> None:
    """A timed-out generator process is terminated instead of occupying the worker forever."""

    class FakeConnection:
        def close(self) -> None:
            pass

    class FakeProcess:
        def __init__(self) -> None:
            self.alive = True
            self.terminated = False
            self.killed = False

        def start(self) -> None:
            pass

        def join(self, timeout=None) -> None:
            del timeout

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            self.terminated = True
            self.alive = False

        def kill(self) -> None:
            self.killed = True
            self.alive = False

        def close(self) -> None:
            pass

    process = FakeProcess()

    class FakeContext:
        def Pipe(self, duplex=False):
            assert duplex is False
            return FakeConnection(), FakeConnection()

        def Process(self, **kwargs):
            assert kwargs["name"] == "ha-context-generator"
            return process

    monkeypatch.setattr(server.multiprocessing, "get_context", lambda method: FakeContext())
    manager = server.ContextTaskManager(timeout_seconds=0)
    with pytest.raises(TimeoutError, match="deadline"):
        manager._generate(tmp_path, tmp_path / "context.md", "offline", {})
    assert process.terminated is True
    assert process.killed is False
    manager._executor.shutdown(wait=True)


def test_context_manager_accepts_new_task_after_timed_out_task(monkeypatch, tmp_path) -> None:
    """A terminal timeout must not permanently wedge the one-worker context subsystem."""
    config_root = tmp_path / "config"
    output_root = tmp_path / "output"
    config_root.mkdir()
    output_root.mkdir()
    monkeypatch.setattr(server, "HA_CONFIG_PATH", str(config_root))
    monkeypatch.setattr(server, "CONTEXT_OUTPUT_ROOT", str(output_root))

    manager = server.ContextTaskManager(timeout_seconds=1)
    calls = 0

    def fake_generate(config_path, output_path, mode, options):
        nonlocal calls
        del config_path, output_path, mode, options
        calls += 1
        if calls == 1:
            raise TimeoutError("Context generation exceeded its deadline")
        return {"generated": True}

    monkeypatch.setattr(manager, "_generate", fake_generate)
    first = manager.start(str(config_root), str(output_root / "first.md"), "offline", "caller")
    with pytest.raises(TimeoutError):
        first.future.result(timeout=2)
    assert manager.status("caller")["status"] == "error"

    second = manager.start(str(config_root), str(output_root / "second.md"), "offline", "caller")
    assert second.future.result(timeout=2) == {"generated": True}
    manager._executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_mcp_principal_is_bound_from_each_request_token(monkeypatch) -> None:
    """Kernel authorization must use the request token subject/scopes, not a process identity."""
    from fastmcp.server.auth import AccessToken

    from tools.invocation import current_principal

    token = AccessToken(
        token="request-token",
        client_id="client-a",
        subject="subject-a",
        scopes=["filesystem.read"],
    )
    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: token)
    observed = {}

    async def call_next(context):
        del context
        principal = current_principal()
        observed["subject"] = principal.subject
        observed["capabilities"] = principal.capabilities
        observed["targets"] = principal.targets
        return "ok"

    context = type("Context", (), {"method": "tools/call"})()
    result = await server.RequestPrincipalMiddleware()(context, call_next)
    assert result == "ok"
    assert observed == {
        "subject": "subject-a",
        "capabilities": frozenset({"filesystem.read"}),
        "targets": frozenset({"runtime", "home_assistant", "home_assistant_config"}),
    }


def test_context_generate_rejects_malformed_json(client: TestClient) -> None:
    response = client.post(
        "/api/context/generate",
        content=b"{not json",
        headers={**AUTH, "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "INVALID_ARGUMENTS"


def test_context_status_exposes_stable_error_code(monkeypatch, tmp_path) -> None:
    from concurrent.futures import Future

    manager = server.ContextTaskManager(timeout_seconds=5)
    failed: Future = Future()
    failed.set_exception(server.ContextGenerationFailed("GENERATION_FAILED"))
    monkeypatch.setattr(
        manager,
        "_task",
        server.GenerationTask(
            task_id="task-a",
            owner="caller",
            output_path=tmp_path / "context.md",
            started_at=0.0,
            future=failed,
            mode="offline",
        ),
    )

    payload = manager.status("caller")

    assert payload["status"] == "error"
    assert payload["error_code"] == "GENERATION_FAILED"


def test_context_status_maps_deadline_errors(monkeypatch, tmp_path) -> None:
    from concurrent.futures import Future

    manager = server.ContextTaskManager(timeout_seconds=5)
    timed_out: Future = Future()
    timed_out.set_exception(TimeoutError("Context generation exceeded its deadline"))
    monkeypatch.setattr(
        manager,
        "_task",
        server.GenerationTask(
            task_id="task-b",
            owner="caller",
            output_path=tmp_path / "context.md",
            started_at=0.0,
            future=timed_out,
            mode="offline",
        ),
    )

    payload = manager.status("caller")

    assert payload["status"] == "error"
    assert payload["error_code"] == "DEADLINE_EXCEEDED"
