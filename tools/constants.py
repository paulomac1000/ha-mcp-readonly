"""Single Source of Truth for all environment variable defaults."""

import os

HA_URL = os.getenv("HA_URL", "http://homeassistant:8123")
HA_TOKEN = os.getenv("HA_TOKEN", "")
HA_CONFIG_PATH = os.getenv("HA_CONFIG_PATH", "/config")

# Server ports
HEALTH_CHECK_PORT = int(os.getenv("HEALTH_CHECK_PORT", "9091"))
MCP_SSE_PORT = int(os.getenv("MCP_SSE_PORT", "9092"))
MCP_PORT = MCP_SSE_PORT  # backward-compatible alias
REST_API_PORT = int(os.getenv("REST_API_PORT", "9093"))

# Feature flags
DEV_TOOLS_ENABLED = os.getenv("MCP_DEV_TOOLS_ENABLED", "1").lower() in ("1", "true", "yes")
RUN_TESTS_ON_STARTUP = os.getenv("RUN_TESTS_ON_STARTUP", "0").lower() in ("1", "true", "yes")

# Security: SSE/HTTP bind address
MCP_UNSAFE_PUBLIC_ACCESS_CONFIRMED = os.getenv(
    "MCP_UNSAFE_PUBLIC_ACCESS_CONFIRMED", "0"
).lower() in ("1", "true", "yes")
MCP_BIND_HOST = "0.0.0.0" if MCP_UNSAFE_PUBLIC_ACCESS_CONFIRMED else "127.0.0.1"  # nosec B104

# Security: REST API CORS allowed origins (comma-separated). Default localhost
# only -- a wildcard "*" would let any site call the REST bridge in a browser.
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost").split(",") if o.strip()
]

# Context generator
OUTPUT_PATH = os.getenv("OUTPUT_PATH", "/app/output/ha-ai-context.md")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
