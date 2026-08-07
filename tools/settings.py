"""Immutable runtime settings loaded once before application composition."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    ha_url: str
    ha_token: str
    ha_config_path: str
    health_check_port: int
    mcp_port: int
    rest_api_port: int
    mcp_transport: Literal["stdio", "http"]
    rest_api_enabled: bool
    dev_tools_enabled: bool
    run_tests_on_startup: bool
    health_server_enabled: bool
    backend_required_for_ready: bool
    mcp_bind_host: str
    mcp_auth_token: str
    rest_api_token: str
    cors_allowed_origins: tuple[str, ...]
    output_path: str
    context_output_root: str
    log_level: str

    @classmethod
    def from_env(cls) -> RuntimeSettings:
        transport = os.getenv("MCP_TRANSPORT", "stdio").strip().casefold()
        if transport == "streamable-http":
            transport = "http"
        if transport == "sse":
            raise ValueError("Legacy SSE transport has been removed; use stdio or Streamable HTTP")
        if transport not in {"stdio", "http"}:
            raise ValueError("MCP_TRANSPORT must be one of: stdio, http")
        bind_host = os.getenv("MCP_BIND_HOST", "127.0.0.1").strip()
        if not bind_host:
            raise ValueError("MCP_BIND_HOST cannot be empty")
        origins = tuple(
            origin.strip()
            for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost").split(",")
            if origin.strip()
        )
        if "*" in origins:
            raise ValueError("Wildcard CORS origins are not allowed")
        output_path = os.getenv("OUTPUT_PATH", "/app/output/ha-ai-context.md")
        return cls(
            ha_url=os.getenv("HA_URL", "http://homeassistant:8123"),
            ha_token=os.getenv("HA_TOKEN", ""),
            ha_config_path=os.getenv("HA_CONFIG_PATH", "/config"),
            health_check_port=int(os.getenv("HEALTH_CHECK_PORT", "9091")),
            mcp_port=int(os.getenv("MCP_PORT", "9092")),
            rest_api_port=int(os.getenv("REST_API_PORT", "9093")),
            mcp_transport=transport,  # type: ignore[arg-type]
            rest_api_enabled=_env_bool("REST_API_ENABLED", False),
            dev_tools_enabled=_env_bool("MCP_DEV_TOOLS_ENABLED", False),
            run_tests_on_startup=_env_bool("RUN_TESTS_ON_STARTUP", False),
            health_server_enabled=_env_bool("HEALTH_SERVER_ENABLED", transport != "stdio"),
            backend_required_for_ready=_env_bool("HA_BACKEND_REQUIRED_FOR_READY", False),
            mcp_bind_host=bind_host,
            mcp_auth_token=os.getenv("MCP_AUTH_TOKEN", ""),
            rest_api_token=os.getenv("REST_API_TOKEN", os.getenv("MCP_AUTH_TOKEN", "")),
            cors_allowed_origins=origins,
            output_path=output_path,
            context_output_root=os.getenv("CONTEXT_OUTPUT_ROOT", str(Path(output_path).parent)),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


SETTINGS = RuntimeSettings.from_env()
