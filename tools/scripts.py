import logging
import os

import yaml

from tools.utils import _error_response, _success_response

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"


def _do_list_scripts(config_path: str) -> str:
    """Fetches list of all scripts from scripts.yaml."""
    script_file = os.path.join(config_path, "scripts.yaml")

    if not os.path.exists(script_file):
        return _error_response("scripts.yaml not found")

    with open(script_file, encoding="utf-8") as f:
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

    return _success_response({"total_scripts": len(summary), "scripts": summary})


def _do_get_script_code(script_id: str, config_path: str) -> str:
    """Fetches full code of a specific script."""
    script_file = os.path.join(config_path, "scripts.yaml")

    if not os.path.exists(script_file):
        return _error_response("scripts.yaml not found")

    with open(script_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if script_id in data:
        return yaml.dump(
            {script_id: data[script_id]},
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

    return _error_response(f"Script '{script_id}' not found")


def register_script_tools(mcp, config_path):

    @mcp.tool()
    def list_scripts() -> str:
        """[READ] Fetches list of all scripts from scripts.yaml.
        Returns script names and their descriptions.
        """
        try:
            return _do_list_scripts(config_path)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    def get_script_code(script_id: str) -> str:
        """[READ] Fetches full code of a specific script.

        Args:
            script_id: Script id (e.g. "notify_energy_price")

        Returns:
            YAML string with script configuration including sequence, mode, and fields.
        """
        try:
            return _do_get_script_code(script_id, config_path)
        except Exception as e:
            return _error_response(str(e))
