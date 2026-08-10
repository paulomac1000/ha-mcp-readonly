"""Tests for immutable runtime settings and retired legacy transports."""

from dataclasses import FrozenInstanceError

import pytest

from tools.settings import RuntimeSettings


def test_legacy_sse_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "sse")
    with pytest.raises(ValueError, match="Legacy SSE transport has been removed"):
        RuntimeSettings.from_env()


def test_streamable_http_alias_normalizes_to_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    settings = RuntimeSettings.from_env()
    assert settings.mcp_transport == "http"


def test_streamable_http_defaults_to_stateful_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    monkeypatch.delenv("MCP_HTTP_STATELESS", raising=False)
    settings = RuntimeSettings.from_env()
    assert settings.mcp_http_stateless is False


def test_stateless_http_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    monkeypatch.setenv("MCP_HTTP_STATELESS", "1")
    settings = RuntimeSettings.from_env()
    assert settings.mcp_http_stateless is True


def test_settings_are_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    settings = RuntimeSettings.from_env()
    with pytest.raises(FrozenInstanceError):
        settings.mcp_port = 9999  # type: ignore[misc]


def test_wildcard_cors_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
    with pytest.raises(ValueError, match="Wildcard CORS"):
        RuntimeSettings.from_env()


def test_invalid_port_names_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("MCP_PORT", "not-a-port")
    with pytest.raises(ValueError, match="MCP_PORT must be an integer"):
        RuntimeSettings.from_env()


def test_out_of_range_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("REST_API_PORT", "70000")
    with pytest.raises(ValueError, match="REST_API_PORT must be between 1 and 65535"):
        RuntimeSettings.from_env()


def test_wildcard_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "*")
    with pytest.raises(ValueError, match="explicit hosts"):
        RuntimeSettings.from_env()
