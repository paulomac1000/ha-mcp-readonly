"""Smoke tests: basic connectivity to HA and MCP server."""

import os
from pathlib import Path

import pytest
import requests

_HA_URL = os.getenv("HA_URL") or "http://192.168.0.101:8123"
_HA_TOKEN = os.getenv("HA_TOKEN") or ""
_HA_CONFIG_PATH = os.getenv("HA_CONFIG_PATH") or "/var/apps/hassio/data/hassio"

REST_API_PORT = int(os.getenv("REST_API_PORT", "9093"))
REST_API_URL = f"http://localhost:{REST_API_PORT}"

pytestmark = pytest.mark.skipif(not _HA_TOKEN, reason="HA_TOKEN required for smoke tests")


class TestHAConnectivity:
    """Verify Home Assistant is reachable."""

    def test_ha_api_reachable(self):
        """HA REST API should respond."""
        resp = requests.get(
            f"{_HA_URL}/api/",
            headers={"Authorization": f"Bearer {_HA_TOKEN}"},
            timeout=10,
        )
        assert resp.status_code == 200
        assert "message" in resp.json()

    def test_ha_states_endpoint(self):
        """HA /api/states should return entity list."""
        resp = requests.get(
            f"{_HA_URL}/api/states",
            headers={"Authorization": f"Bearer {_HA_TOKEN}"},
            timeout=10,
        )
        assert resp.status_code == 200
        states = resp.json()
        assert isinstance(states, list)
        assert len(states) > 0

    def test_config_directory_exists(self):
        """Config directory should exist and contain .storage."""
        config = Path(_HA_CONFIG_PATH)
        assert config.is_dir()
        assert (config / ".storage").is_dir()


class TestMCPServerHealth:
    """Verify the MCP server itself is running."""

    def test_health_endpoint_returns_healthy(self):
        """MCP server health endpoint should return healthy status."""
        resp = requests.get(f"{REST_API_URL}/health", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "healthy"

    def test_tools_list_returns_tools(self):
        """Tools endpoint should list registered tools."""
        resp = requests.get(f"{REST_API_URL}/api/tools", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert data.get("total", 0) > 100

    def test_openapi_schema(self):
        """OpenAPI schema should be valid JSON."""
        resp = requests.get(f"{REST_API_URL}/api/openapi.json", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("openapi") == "3.0.0"
        assert "paths" in data


class TestAuthAndPorts:
    """Token validation and port availability."""

    def test_ha_token_is_valid(self):
        """Valid token should get 200, invalid token should get 401."""
        resp = requests.get(
            f"{_HA_URL}/api/",
            headers={"Authorization": f"Bearer {_HA_TOKEN}"},
            timeout=10,
        )
        assert resp.status_code == 200

        resp_bad = requests.get(
            f"{_HA_URL}/api/",
            headers={"Authorization": "Bearer invalid_token_xyz"},
            timeout=10,
        )
        assert resp_bad.status_code in (401, 403)

    def test_health_port_9091_responding(self):
        """Health check port 9091 should return healthy."""
        health_port = int(os.getenv("HEALTH_CHECK_PORT", "9091"))
        resp = requests.get(f"http://localhost:{health_port}/health", timeout=5)
        assert resp.status_code == 200
        assert resp.json().get("status") == "healthy"

    def test_mcp_sse_port_9092_listening(self):
        """MCP SSE port 9092 should accept connections."""
        import socket

        mcp_port = int(os.getenv("MCP_SSE_PORT", "9092"))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex(("localhost", mcp_port))
        sock.close()
        assert result == 0, f"Port {mcp_port} not listening"
