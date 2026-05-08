"""Smoke test conftest — minimal, no MCP wrapper needed, uses direct HTTP."""

import os
from pathlib import Path

import pytest

# Load .env for HA credentials
env_paths = [Path("/app/.env"), Path(".env")]
for env_path in env_paths:
    if env_path.exists():
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        except Exception:
            pass

HA_URL = os.getenv("HA_URL", "http://localhost:8123")
HA_TOKEN = os.getenv("HA_TOKEN", "")
REST_API_PORT = int(os.getenv("REST_API_PORT", "9093"))
REST_API_URL = f"http://localhost:{REST_API_PORT}"

HA_CONFIG_PATH = os.getenv("HA_CONFIG_PATH", "/config")

pytestmark = pytest.mark.skipif(not HA_TOKEN, reason="HA_TOKEN required for smoke tests")
