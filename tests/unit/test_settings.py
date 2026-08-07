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


def test_settings_are_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    settings = RuntimeSettings.from_env()
    with pytest.raises(FrozenInstanceError):
        settings.mcp_port = 9999  # type: ignore[misc]


def test_wildcard_cors_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
    with pytest.raises(ValueError, match="Wildcard CORS"):
        RuntimeSettings.from_env()
