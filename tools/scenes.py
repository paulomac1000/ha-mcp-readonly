import json
import os

import yaml


def register_scene_tools(mcp, config_path):

    @mcp.tool()
    def list_scenes() -> str:
        """
        Fetches list of all scenes from scenes.yaml.
        Returns scene names and their basic information.
        """
        try:
            scene_file = os.path.join(config_path, "scenes.yaml")

            if not os.path.exists(scene_file):
                return json.dumps({"error": "scenes.yaml not found"}, indent=2)

            with open(scene_file, "r", encoding="utf-8") as f:
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

            return json.dumps(
                {"total_scenes": len(summary), "scenes": summary},
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool()
    def get_scene_code(scene_id: str) -> str:
        """
        Fetches full code of a specific scene.

        Args:
            scene_id: Scene id (e.g. "evening_lights") or scene name
        """
        try:
            scene_file = os.path.join(config_path, "scenes.yaml")

            if not os.path.exists(scene_file):
                return "scenes.yaml not found"

            with open(scene_file, "r", encoding="utf-8") as f:
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

            return f"Scene '{scene_id}' not found"
        except Exception as e:
            return f"Error: {str(e)}"
