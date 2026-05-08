"""
Root conftest — environment loading only.
Mock data: tests/fixtures.py
Unit fixtures: tests/unit/conftest.py
Integration fixtures: tests/integration/conftest.py
"""

import os
from pathlib import Path


def load_env():
    """Load environment variables from .env file if available."""
    env_paths = [
        Path(".env"),
    ]
    for env_path in env_paths:
        if env_path.exists():
            try:
                with open(env_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
                return
            except Exception:
                pass


load_env()
