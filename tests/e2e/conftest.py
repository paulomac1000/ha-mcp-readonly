"""E2E test conftest — real HA, temp output dir."""

import os
import tempfile
from pathlib import Path

import pytest

# Load .env
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
HA_CONFIG_PATH = os.getenv("HA_CONFIG_PATH", "/config")
REST_API_PORT = int(os.getenv("REST_API_PORT", "9093"))
REST_API_URL = f"http://localhost:{REST_API_PORT}"


@pytest.fixture
def tmp_output_path():
    """Temporary output path for context generation."""
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
        path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass
