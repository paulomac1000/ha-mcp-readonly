"""E2E test conftest — real HA, temp output dir."""

import os
import socket
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

REST_API_TOKEN = os.getenv("REST_API_TOKEN") or os.getenv("MCP_AUTH_TOKEN", "")
REST_HEADERS = {"Authorization": f"Bearer {REST_API_TOKEN}"}


def _server_running():
    """Check if MCP server is reachable on the REST API port."""
    try:
        sock = socket.create_connection(("localhost", REST_API_PORT), timeout=1)
        sock.close()
        return True
    except (OSError, ConnectionRefusedError):
        return False


def _call(name: str, **params):
    """Call a tool through the authenticated REST adapter."""
    import requests

    response = requests.post(
        f"{REST_API_URL}/api/tools/{name}",
        json=params,
        headers=REST_HEADERS,
        timeout=60,
    )
    response.raise_for_status()
    return response.json().get("result", response.json())


def _first_item(name: str, list_key: str, **params):
    """Return the first list entry for a discovery tool, or None."""
    try:
        data = _call(name, **params)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    items = data.get(list_key)
    return items[0] if isinstance(items, list) and items else None


def discover_live_context() -> dict:
    """Discover real identifiers from the live server for smoke/e2e parameters.

    The agent that authored the e2e suite had no Home Assistant access, so the
    parameter map used placeholder ids that do not exist on any real instance.
    This resolves ids from the live system so the suite runs against whatever
    HA instance the server is observing.
    """
    context: dict = {"snapshot_id": None}

    automation = _first_item("list_automations", "automations")
    if automation:
        context["automation_id"] = automation.get("id") or automation.get("alias")
        context["automation_alias"] = automation.get("alias") or automation.get("id")

    area = _first_item("get_area_registry", "areas")
    if area:
        context["area_id"] = area.get("id") or area.get("name")

    device = _first_item("search_devices", "devices")
    if device:
        context["device_id"] = device.get("device_id")

    entry = _first_item("get_config_entries", "entries")
    if entry:
        context["entry_id"] = entry.get("entry_id") or entry.get("id")

    script = _first_item("list_scripts", "scripts")
    if script:
        context["script_id"] = script.get("id") or script.get("script_id")

    scene = _first_item("list_scenes", "scenes")
    if scene:
        context["scene_id"] = scene.get("id") or scene.get("name")

    blueprint = _first_item("list_blueprints", "blueprints")
    if blueprint:
        context["blueprint_path"] = blueprint.get("path")

    template = _first_item("get_template_entities", "templates")
    if template:
        context["template_entity_id"] = template.get("entity_id")

    person = _first_item("get_persons", "persons")
    if person:
        context["person_entity_id"] = f"person.{person.get('id')}"

    domains = _first_item("list_config_entry_domains", "domains")
    if domains:
        context["integration_domain"] = domains.get("domain")

    try:
        snapshot = _call("take_entity_health_snapshot")
        if isinstance(snapshot, dict) and snapshot.get("snapshot_id"):
            context["snapshot_id"] = snapshot["snapshot_id"]
    except Exception:
        pass

    return context


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
