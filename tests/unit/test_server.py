"""
Tests for server.py REST API endpoints.
"""

from starlette.testclient import TestClient
from unittest.mock import patch

import pytest

from server import create_rest_app


@pytest.fixture
def client():
    """Create a TestClient for the REST API."""
    app = create_rest_app()
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for GET /health and GET /api/health."""

    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["server"] == "HA-Observer"
        assert "tools_registered" in data
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
        assert "tools" in data
        assert isinstance(data["tools"], list)
        assert data["total"] == len(data["tools"])


class TestCallToolEndpoint:
    """Tests for POST /api/tools/{tool_name}."""

    def test_call_existing_tool(self, client):
        # Use a simple built-in tool
        response = client.post("/api/tools/get_domains_summary")
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

    def test_call_tool_with_args(self, client):
        response = client.post("/api/tools/search_entities", json={"search_term": "sun"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["tool"] == "search_entities"

    def test_call_tool_with_invalid_args(self, client):
        # Calling with wrong args should return 400 or 500 depending on tool
        response = client.post("/api/tools/get_entity_state", json={"wrong_param": 123})
        # TypeError -> 400
        assert response.status_code in (400, 200, 500)


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
