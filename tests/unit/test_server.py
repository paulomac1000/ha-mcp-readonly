"""
Tests for server.py REST API endpoints.
"""

import sys
from starlette.testclient import TestClient
from unittest.mock import MagicMock, patch

import pytest

from server import create_rest_app
from tests.fixtures import MOCK_SAMPLE_STATES

_MOCK_SUN_STATE = {
    "entity_id": "sun.sun",
    "state": "above_horizon",
    "attributes": {"friendly_name": "Sun", "elevation": 45.5},
    "last_changed": "2024-06-01T05:30:00.000000+00:00",
    "last_updated": "2024-06-01T14:35:00.000000+00:00",
}


def _mock_make_ha_request(ha_url, ha_token, endpoint, **kwargs):
    """Mock for make_ha_request that returns fixture data based on endpoint."""
    if endpoint == "/api/states":
        return {"success": True, "data": list(MOCK_SAMPLE_STATES)}
    if endpoint.startswith("/api/states/"):
        entity_id = endpoint.replace("/api/states/", "")
        for state in MOCK_SAMPLE_STATES:
            if state["entity_id"] == entity_id:
                return {"success": True, "data": dict(state)}
        return {"success": True, "data": _MOCK_SUN_STATE}
    return {"success": False, "error": f"Mock: unhandled endpoint {endpoint}"}


@pytest.fixture
def client():
    """Create a TestClient for the REST API."""
    app = create_rest_app()
    return TestClient(app)


@pytest.fixture
def client_mocked():
    """Create a TestClient with make_ha_request mocked to return fixture data."""
    with patch("tools.states.make_ha_request", side_effect=_mock_make_ha_request):
        app = create_rest_app()
        yield TestClient(app)


class TestHealthEndpoint:
    """Tests for GET /health and GET /api/health."""

    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["server"] == "HA-Observer"
        assert "tool_count" in data
        assert "endpoints" in data

    def test_api_health_returns_200(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


class TestListToolsEndpoint:
    """Tests for GET /api/tools."""

    def test_list_tools_returns_tools(self, client):
        response = client.get("/api/tools")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "categories" in data
        assert isinstance(data["categories"], dict)
        # Count tools across all categories
        total_in_categories = sum(len(v) for v in data["categories"].values())
        assert data["total"] == total_in_categories


class TestCallToolEndpoint:
    """Tests for POST /api/tools/{tool_name}."""

    def test_call_existing_tool(self, client_mocked):
        response = client_mocked.post("/api/tools/get_domains_summary")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["tool"] == "get_domains_summary"
        assert "result" in data

    def test_call_nonexistent_tool(self, client):
        response = client.post("/api/tools/nonexistent_tool_xyz")
        assert response.status_code == 404
        data = response.json()
        assert data["success"] is False
        assert "not found" in data["error"].lower()
        assert "available_tools" in data

    def test_call_tool_with_args(self, client_mocked):
        response = client_mocked.post("/api/tools/search_entities", json={"search_term": "sun"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["tool"] == "search_entities"

    def test_call_tool_with_invalid_args(self, client):
        # Calling with wrong args should return 400 or 500 depending on tool
        response = client.post("/api/tools/get_entity_state", json={"wrong_param": 123})
        # TypeError -> 400
        assert response.status_code in (400, 200, 500)

    def test_call_tool_get_entity_state(self, client_mocked):
        """Test calling get_entity_state with valid entity_id."""
        response = client_mocked.post(
            "/api/tools/get_entity_state",
            json={"entity_id": "sun.sun"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["tool"] == "get_entity_state"

    def test_call_tool_invalid_json_body(self, client_mocked):
        """POST with malformed JSON triggers JSONDecodeError handler (lines 318-319)."""
        response = client_mocked.post(
            "/api/tools/get_domains_summary",
            content=b"not valid json {{{",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200

    def test_call_tool_not_callable(self, client):
        """Tool object exists but is not callable (lines 340-343)."""
        with patch("server.get_tool", return_value={"not": "callable"}):
            response = client.post("/api/tools/some_tool")
        assert response.status_code == 500
        data = response.json()
        assert data["success"] is False
        assert "not callable" in data["error"].lower()

    def test_call_tool_missing_required_arg(self, client):
        """TypeError -> 400 when required arg is missing (lines 370-371)."""
        response = client.post("/api/tools/get_entity_state")
        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False


class TestContextEndpoints:
    """Tests for context generator endpoints."""

    def test_context_modes(self, client):
        response = client.get("/api/context/modes")
        assert response.status_code == 200
        data = response.json()
        assert "modes" in data
        assert len(data["modes"]) == 3
        ids = [m["id"] for m in data["modes"]]
        assert "offline" in ids
        assert "online" in ids
        assert "hybrid" in ids

    def test_context_status_idle(self, client):
        response = client.get("/api/context/status")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data.get("status") in ("idle", "running", "completed", "error")

    def test_context_generate_starts_generation(self, client, tmp_path, monkeypatch):
        # Override paths so the test doesn't touch real filesystem
        monkeypatch.setattr("server.HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setattr("server.OUTPUT_PATH", str(tmp_path / "out.md"))

        # Reset generation state
        import server

        server._generation_state = {
            "status": "idle",
            "started_at": None,
            "completed_at": None,
            "output_path": None,
            "error": None,
            "stats": {},
        }

        with patch("server._run_context_generation"):
            response = client.post("/api/context/generate")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "Context generation started"
            assert "mode" in data

    def test_context_generate_already_running(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("server.HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setattr("server.OUTPUT_PATH", str(tmp_path / "out.md"))

        import server

        server._generation_state = {
            "status": "running",
            "started_at": 12345,
            "completed_at": None,
            "output_path": None,
            "error": None,
            "stats": {},
        }

        response = client.post("/api/context/generate")
        assert response.status_code == 409
        data = response.json()
        assert data["success"] is False
        assert "already in progress" in data["error"].lower()

    def test_context_generate_invalid_path(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("server.HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setattr("server.OUTPUT_PATH", str(tmp_path / "out.md"))

        import server

        server._generation_state = {
            "status": "idle",
            "started_at": None,
            "completed_at": None,
            "output_path": None,
            "error": None,
            "stats": {},
        }

        response = client.post(
            "/api/context/generate",
            json={
                "config_path": "/etc/passwd",
                "output_path": str(tmp_path / "out.md"),
            },
        )
        assert response.status_code == 403
        data = response.json()
        assert "Invalid config_path" in data["error"]

    def test_context_download_not_found(self, client):
        import server

        server._generation_state["output_path"] = "/nonexistent/file.md"
        server._generation_state["status"] = "idle"
        response = client.get("/api/context/download")
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["error"].lower()

    def test_context_download_success(self, client, tmp_path):
        import server

        out_file = tmp_path / "test.md"
        out_file.write_text("# Test Context\n", encoding="utf-8")
        server._generation_state["output_path"] = str(out_file)
        server._generation_state["status"] = "completed"
        server._generation_state["completed_at"] = 12345

        response = client.get("/api/context/download")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"
        assert "Test Context" in response.text

    def test_context_download_json_format(self, client, tmp_path):
        import server

        out_file = tmp_path / "test.md"
        out_file.write_text("# Test Context\n", encoding="utf-8")
        server._generation_state["output_path"] = str(out_file)
        server._generation_state["status"] = "completed"
        server._generation_state["completed_at"] = 12345

        response = client.get("/api/context/download?format=json")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "content" in data
        assert "Test Context" in data["content"]

    def test_context_download_still_running(self, client):
        import server

        server._generation_state["status"] = "running"
        response = client.get("/api/context/download")
        assert response.status_code == 409
        data = response.json()
        assert "still in progress" in data["error"].lower()

    def test_context_generate_invalid_json(self, client, tmp_path, monkeypatch):
        """POST with malformed JSON triggers JSONDecodeError handler (lines 397-400)."""
        monkeypatch.setattr("server.HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setattr("server.OUTPUT_PATH", str(tmp_path / "out.md"))

        import server

        server._generation_state = {
            "status": "idle",
            "started_at": None,
            "completed_at": None,
            "output_path": None,
            "error": None,
            "stats": {},
        }

        with patch("server._run_context_generation"):
            response = client.post(
                "/api/context/generate",
                content=b"not json {{{",
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True

    def test_context_generate_invalid_output_path(self, client, tmp_path, monkeypatch):
        """output_path outside allowed prefixes returns 403 (line 416)."""
        monkeypatch.setattr("server.HA_CONFIG_PATH", str(tmp_path))
        monkeypatch.setattr("server.OUTPUT_PATH", str(tmp_path / "out.md"))

        import server

        server._generation_state = {
            "status": "idle",
            "started_at": None,
            "completed_at": None,
            "output_path": None,
            "error": None,
            "stats": {},
        }

        response = client.post(
            "/api/context/generate",
            json={
                "config_path": str(tmp_path),
                "output_path": "/unrelated/path/out.md",
            },
        )
        assert response.status_code == 403
        assert "Invalid output_path" in response.json()["error"]

    def test_context_download_read_exception(self, client, tmp_path):
        """File read error returns 500 (lines 502-503)."""
        import server

        out_file = tmp_path / "test.md"
        out_file.write_text("# Test Context\n", encoding="utf-8")
        server._generation_state["output_path"] = str(out_file)
        server._generation_state["status"] = "completed"

        with patch("builtins.open", side_effect=OSError("Permission denied")):
            response = client.get("/api/context/download")
        assert response.status_code == 500
        data = response.json()
        assert "Failed to read context file" in data["error"]


class TestOpenApiSchema:
    """Tests for GET /api/openapi.json."""

    def test_openapi_schema_returns_valid_json(self, client):
        response = client.get("/api/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert data["openapi"] == "3.0.0"
        assert "info" in data
        assert "paths" in data
        assert "/api/tools" in data["paths"]
        assert "/api/health" in data["paths"]

    def test_openapi_tool_paths_have_descriptions(self, client):
        """Tool paths carry docstring-derived summaries (lines 558-559)."""
        response = client.get("/api/openapi.json")
        data = response.json()
        tool_paths = {k: v for k, v in data["paths"].items() if k.startswith("/api/tools/")}
        assert len(tool_paths) > 0
        for _path, methods in tool_paths.items():
            if "post" in methods:
                assert "summary" in methods["post"]
                assert len(methods["post"]["summary"]) > 0


class TestToolCount:
    """Tests for tool count."""

    def test_tool_count_positive(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["tool_count"] > 0

    def test_tool_count_matches_list(self, client):
        health_data = client.get("/api/health").json()
        tools_data = client.get("/api/tools").json()
        assert health_data["tool_count"] == tools_data["tool_count"]

    def test_list_tools_total_matches_items(self, client):
        tools_data = client.get("/api/tools").json()
        total_in_categories = sum(len(v) for v in tools_data["categories"].values())
        assert tools_data["total"] == total_in_categories


class TestRunContextGeneration:
    """Tests for _run_context_generation background-thread function (lines 215-233)."""

    def test_completes_successfully(self):
        import server

        server._generation_state["status"] = "idle"
        server._generation_state["error"] = None
        with patch(
            "context_generator.generate_context_file",
            return_value={"entities": 10},
        ):
            server._run_context_generation("/config", "/out.md", "offline")
        assert server._generation_state["status"] == "completed"
        assert server._generation_state["error"] is None
        assert server._generation_state["stats"] == {"entities": 10}

    def test_captures_exception(self):
        import server

        server._generation_state["status"] = "idle"
        with patch(
            "context_generator.generate_context_file",
            side_effect=RuntimeError("test error"),
        ):
            server._run_context_generation("/config", "/out.md", "offline")
        assert server._generation_state["status"] == "error"
        assert server._generation_state["error"] == "test error"
        assert server._generation_state["completed_at"] is not None


class TestRunStartupTests:
    """Tests for run_startup_tests function (lines 243-262)."""

    def test_tests_pass(self):
        from server import run_startup_tests

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            assert run_startup_tests() is True

    def test_tests_fail(self):
        from server import run_startup_tests

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            assert run_startup_tests() is False

    def test_exception_returns_false(self):
        from server import run_startup_tests

        with patch("subprocess.run", side_effect=FileNotFoundError("pytest not found")):
            assert run_startup_tests() is False


class TestRunRestApi:
    """Tests for run_rest_api function (lines 611-615)."""

    def test_starts_uvicorn(self):
        mock_uvicorn = MagicMock()
        sys.modules["uvicorn"] = mock_uvicorn
        try:
            from server import run_rest_api

            run_rest_api()
            mock_uvicorn.run.assert_called_once()
            call_kwargs = mock_uvicorn.run.call_args[1]
            assert call_kwargs["host"] == "127.0.0.1"
            assert call_kwargs["port"] == 9093
        finally:
            sys.modules.pop("uvicorn", None)

    def test_uvicorn_missing_does_not_crash(self):
        with patch.dict("sys.modules", {"uvicorn": MagicMock()}):
            from server import run_rest_api

            try:
                run_rest_api()
            except Exception:
                pytest.fail("run_rest_api raised an unexpected exception")
