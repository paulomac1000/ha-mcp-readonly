"""E2E tests for authenticated network adapters against a real HA instance."""

from __future__ import annotations

import os
import socket
import time
from typing import Any

import pytest
import requests

from .conftest import HA_TOKEN, REST_API_URL, _server_running

REST_API_TOKEN = os.getenv("REST_API_TOKEN") or os.getenv("MCP_AUTH_TOKEN", "")
REST_HEADERS = {"Authorization": f"Bearer {REST_API_TOKEN}"}

pytestmark = pytest.mark.skipif(
    not HA_TOKEN or not REST_API_TOKEN or not _server_running(),
    reason="HA_TOKEN, REST_API_TOKEN and a running REST adapter are required",
)


def _get(path: str, **kwargs: Any) -> requests.Response:
    headers = {**REST_HEADERS, **kwargs.pop("headers", {})}
    return requests.get(f"{REST_API_URL}{path}", headers=headers, **kwargs)


def _post(path: str, **kwargs: Any) -> requests.Response:
    headers = {**REST_HEADERS, **kwargs.pop("headers", {})}
    return requests.post(f"{REST_API_URL}{path}", headers=headers, **kwargs)


class TestRESTAdapter:
    """REST compatibility adapter must authenticate and use the shared catalog."""

    def test_public_health_endpoint(self):
        response = requests.get(f"{REST_API_URL}/health", timeout=5)
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] in {"ready", "live"}
        assert payload["version"]
        details = _get("/api/health/details", timeout=5).json()
        assert details["tool_count"] > 100

    def test_anonymous_catalog_is_rejected(self):
        response = requests.get(f"{REST_API_URL}/api/tools", timeout=5)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    def test_catalog_and_schema_are_authenticated(self):
        catalog = _get("/api/tools", timeout=10)
        assert catalog.status_code == 200
        tools = catalog.json()["tools"]
        assert len(tools) > 100
        names = {entry["name"] for entry in tools}
        assert {"get_entity_state", "list_automations", "diagnose_system_health"} <= names

        schema = _get("/api/openapi.json", timeout=10)
        assert schema.status_code == 200
        payload = schema.json()
        assert payload["openapi"].startswith("3.")
        assert "/api/tools/get_entity_state" in payload["paths"]

    def test_tool_invocation_and_validation(self):
        response = _post(
            "/api/tools/get_entity_state",
            json={"entity_id": "sun.sun"},
            timeout=30,
        )
        assert response.status_code == 200
        assert response.json()["tool"] == "get_entity_state"

        invalid = _post("/api/tools/get_entity_state", json={}, timeout=10)
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "INVALID_ARGUMENTS"

    def test_unknown_tool_and_invalid_json_are_stable(self):
        missing = _post("/api/tools/not_a_real_tool", json={}, timeout=10)
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "NOT_FOUND"

        invalid = _post(
            "/api/tools/get_entity_state",
            data="not-json",
            headers={"Content-Type": "text/plain"},
            timeout=10,
        )
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "INVALID_JSON"


class TestContextArtifacts:
    """Generated context artifacts are bound to the authenticated principal."""

    def test_modes_and_offline_generation(self):
        modes = _get("/api/context/modes", timeout=5)
        assert modes.status_code == 200
        assert modes.json()["modes"] == ["offline", "online", "hybrid"]

        generation = _post(
            "/api/context/generate",
            json={"mode": "offline"},
            timeout=10,
        )
        assert generation.status_code in (202, 409)
        if generation.status_code == 202:
            assert generation.json()["status"] == "running"

    def test_status_and_download_contract(self):
        _post("/api/context/generate", json={"mode": "offline"}, timeout=10)
        terminal = {"completed", "error", "deadline_exceeded"}
        status_payload: dict[str, Any] = {}
        for _ in range(30):
            status = _get("/api/context/status", timeout=5)
            assert status.status_code == 200
            status_payload = status.json()
            if status_payload.get("status") in terminal:
                break
            time.sleep(1)

        download = _get("/api/context/download", timeout=30)
        if status_payload.get("status") == "completed":
            assert download.status_code == 200
            assert download.headers["content-type"].startswith("text/markdown")
        else:
            assert download.status_code == 404


class TestStreamableHTTPTransport:
    """Network MCP is exposed at /mcp and requires bearer authentication."""

    def test_mcp_endpoint_rejects_anonymous_requests(self):
        mcp_port = int(os.getenv("MCP_PORT", "9092"))
        response = requests.get(f"http://localhost:{mcp_port}/mcp", timeout=5)
        assert response.status_code in (401, 403)

    def test_mcp_port_is_listening(self):
        mcp_port = int(os.getenv("MCP_PORT", "9092"))
        with socket.create_connection(("localhost", mcp_port), timeout=3):
            pass
