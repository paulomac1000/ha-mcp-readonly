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


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


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
    mcp_allowed_hosts: tuple[str, ...]
    mcp_http_max_body_bytes: int
    mcp_http_max_header_bytes: int
    mcp_http_connection_limit: int
    mcp_http_keepalive_seconds: int
    mcp_http_stateless: bool
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

        allowed_hosts = tuple(
            host.strip()
            for host in os.getenv("MCP_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
            if host.strip()
        )
        if not allowed_hosts or "*" in allowed_hosts:
            raise ValueError("MCP_ALLOWED_HOSTS must contain explicit hosts and cannot use '*'")
        if bind_host not in {"0.0.0.0", "::"} and bind_host not in allowed_hosts:  # nosec B104 -- comparison only; no socket is bound here
            allowed_hosts = (*allowed_hosts, bind_host)

        output_path = os.getenv("OUTPUT_PATH", "/app/output/ha-ai-context.md")
        return cls(
            # No implicit default host: an empty URL keeps Home Assistant
            # integration and context network collection disabled so a set
            # HA_TOKEN can never be transmitted to an implicit plain-HTTP
            # endpoint (CWE-319).
            ha_url=os.getenv("HA_URL", ""),
            ha_token=os.getenv("HA_TOKEN", ""),
            ha_config_path=os.getenv("HA_CONFIG_PATH", "/config"),
            health_check_port=_env_int("HEALTH_CHECK_PORT", 9091, minimum=1, maximum=65535),
            mcp_port=_env_int("MCP_PORT", 9092, minimum=1, maximum=65535),
            rest_api_port=_env_int("REST_API_PORT", 9093, minimum=1, maximum=65535),
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
            mcp_allowed_hosts=allowed_hosts,
            mcp_http_max_body_bytes=_env_int(
                "MCP_HTTP_MAX_BODY_BYTES", 1024 * 1024, minimum=1024, maximum=16 * 1024 * 1024
            ),
            mcp_http_max_header_bytes=_env_int(
                "MCP_HTTP_MAX_HEADER_BYTES", 64 * 1024, minimum=4096, maximum=1024 * 1024
            ),
            mcp_http_connection_limit=_env_int(
                "MCP_HTTP_CONNECTION_LIMIT", 64, minimum=1, maximum=10_000
            ),
            mcp_http_keepalive_seconds=_env_int(
                "MCP_HTTP_KEEPALIVE_SECONDS", 5, minimum=1, maximum=300
            ),
            # FastMCP 3.x is the session-based 2025-11-25 protocol lane. Stateful
            # HTTP is the conservative default; stateless mode remains an explicit
            # operator opt-in and needs its own exact-client evidence before use.
            mcp_http_stateless=_env_bool("MCP_HTTP_STATELESS", False),
            output_path=output_path,
            context_output_root=os.getenv("CONTEXT_OUTPUT_ROOT", str(Path(output_path).parent)),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


SETTINGS = RuntimeSettings.from_env()
