import logging
import os

import yaml

from tools.utils import _error_response, _success_response

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"


def _do_list_scenes(config_path: str) -> str:
    """Fetches list of all scenes from scenes.yaml."""
    scene_file = os.path.join(config_path, "scenes.yaml")

    if not os.path.exists(scene_file):
        return _error_response("scenes.yaml not found")

    with open(scene_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or []

    summary = []
    for scene in data:
        if isinstance(scene, dict):
            entities = scene.get("entities", {})
            summary.append(
                {
                    "id": scene.get("id", "no_id"),
                    "name": scene.get("name", "No Name"),
                    "icon": scene.get("icon", ""),
                    "entity_count": len(entities) if isinstance(entities, dict) else 0,
                    "entities": list(entities.keys()) if isinstance(entities, dict) else [],
                }
            )

    return _success_response({"total_scenes": len(summary), "scenes": summary})


def _do_get_scene_code(scene_id: str, config_path: str) -> str:
    """Fetches full code of a specific scene."""
    scene_file = os.path.join(config_path, "scenes.yaml")

    if not os.path.exists(scene_file):
        return _error_response("scenes.yaml not found")

    with open(scene_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or []

    for scene in data:
        if isinstance(scene, dict):
            if scene.get("id") == scene_id or scene.get("name") == scene_id:
                return yaml.dump(
                    scene,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )

    return _error_response(f"Scene '{scene_id}' not found")


def register_scene_tools(mcp, config_path):

    @mcp.tool()
    def list_scenes() -> str:
        """[READ] Fetches list of all scenes from scenes.yaml.
        Returns scene names and their basic information.
        """
        try:
            return _do_list_scenes(config_path)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    def get_scene_code(scene_id: str) -> str:
        """[READ] Fetches full code of a specific scene.

        Args:
            scene_id: Scene id (e.g. "evening_lights") or scene name

        Returns:
            YAML string with scene configuration including entities and settings.
        """
        try:
            return _do_get_scene_code(scene_id, config_path)
        except Exception as e:
            return _error_response(str(e))
