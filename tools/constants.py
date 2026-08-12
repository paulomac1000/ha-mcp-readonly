"""Single source of truth for runtime configuration defaults."""

from __future__ import annotations

from .settings import SETTINGS

HA_URL = SETTINGS.ha_url
HA_TOKEN = SETTINGS.ha_token
HA_CONFIG_PATH = SETTINGS.ha_config_path
HEALTH_CHECK_PORT = SETTINGS.health_check_port
MCP_PORT = SETTINGS.mcp_port
REST_API_PORT = SETTINGS.rest_api_port
MCP_TRANSPORT = SETTINGS.mcp_transport
REST_API_ENABLED = SETTINGS.rest_api_enabled
DEV_TOOLS_ENABLED = SETTINGS.dev_tools_enabled
RUN_TESTS_ON_STARTUP = SETTINGS.run_tests_on_startup
HEALTH_SERVER_ENABLED = SETTINGS.health_server_enabled
MCP_BIND_HOST = SETTINGS.mcp_bind_host
MCP_AUTH_TOKEN = SETTINGS.mcp_auth_token
REST_API_TOKEN = SETTINGS.rest_api_token
CORS_ALLOWED_ORIGINS = list(SETTINGS.cors_allowed_origins)
MCP_ALLOWED_HOSTS = list(SETTINGS.mcp_allowed_hosts)
MCP_HTTP_MAX_BODY_BYTES = SETTINGS.mcp_http_max_body_bytes
MCP_HTTP_MAX_HEADER_BYTES = SETTINGS.mcp_http_max_header_bytes
MCP_HTTP_CONNECTION_LIMIT = SETTINGS.mcp_http_connection_limit
MCP_HTTP_KEEPALIVE_SECONDS = SETTINGS.mcp_http_keepalive_seconds
MCP_HTTP_STATELESS = SETTINGS.mcp_http_stateless
OUTPUT_PATH = SETTINGS.output_path
CONTEXT_OUTPUT_ROOT = SETTINGS.context_output_root
LOG_LEVEL = SETTINGS.log_level
