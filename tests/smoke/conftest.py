"""Smoke test conftest — minimal, no MCP wrapper needed, uses direct HTTP."""

import os
import socket
from pathlib import Path


def _load_env():
    """Load environment variables from .env file if available."""
    env_paths = [Path(".env")]
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


_load_env()

REST_API_PORT = int(os.getenv("REST_API_PORT", "9093"))
REST_API_URL = f"http://localhost:{REST_API_PORT}"

HA_TOKEN = os.getenv("HA_TOKEN", "")


def _server_running():
    """Check if MCP server is reachable on the REST API port."""
    try:
        sock = socket.create_connection(("localhost", REST_API_PORT), timeout=1)
        sock.close()
        return True
    except (OSError, ConnectionRefusedError):
        return False
