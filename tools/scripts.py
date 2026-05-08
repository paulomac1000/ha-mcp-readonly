import json
import os

import yaml


def register_script_tools(mcp, config_path):

    @mcp.tool()
    def list_scripts() -> str:
        """
        Fetches list of all scripts from scripts.yaml.
        Returns script names and their descriptions.
        """
        try:
            script_file = os.path.join(config_path, "scripts.yaml")

            if not os.path.exists(script_file):
                return json.dumps({"error": "scripts.yaml not found"}, indent=2)

            with open(script_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            summary = []
            for script_id, script_data in data.items():
                if isinstance(script_data, dict):
                    summary.append(
                        {
                            "id": script_id,
                            "alias": script_data.get("alias", script_id),
                            "description": script_data.get("description", ""),
                            "mode": script_data.get("mode", "single"),
                            "fields": list(script_data.get("fields", {}).keys())
                            if "fields" in script_data
                            else [],
                        }
                    )

            return json.dumps(
                {"total_scripts": len(summary), "scripts": summary},
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool()
    def get_script_code(script_id: str) -> str:
        """Fetches full code of a specific script.

        Args:
            script_id: Script id (e.g. "notify_energy_price")

        Returns:
            YAML string with script configuration including sequence, mode, and fields.
        """
        try:
            script_file = os.path.join(config_path, "scripts.yaml")

            if not os.path.exists(script_file):
                return "scripts.yaml not found"

            with open(script_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            if script_id in data:
                return yaml.dump(
                    {script_id: data[script_id]},
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )

            return f"Script '{script_id}' not found"
        except Exception as e:
            return f"Error: {str(e)}"
