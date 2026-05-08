"""
Blueprint Management Tools
Tools for listing, reading, and analyzing Blueprints (automations & scripts templates).
"""

import json
import os
from pathlib import Path

from tools.yaml_utils import load_yaml_file


def register_blueprint_tools(mcp, config_path):
    """
    Registers tools for managing blueprints.
    """

    # ========================================
    # 🚀 BLUEPRINT TOOLS
    # ========================================

    @mcp.tool()
    def list_blueprints() -> str:
        """
        Fetches list of all available blueprints.
        Scans 'automation' and 'script' directories in the blueprints folder.

        Returns:
            JSON with list of blueprints (name, path, description, author, input count).

        Example:
            list_blueprints()
        """
        try:
            blueprints_dir = Path(config_path) / "blueprints"
            if not os.path.isdir(blueprints_dir):
                return json.dumps(
                    {"success": False, "error": "blueprints directory not found"},
                    indent=2,
                )

            blueprints = []

            for domain in ["automation", "script"]:
                domain_dir = blueprints_dir / domain
                if not domain_dir.exists():
                    continue

                for root, _, files in os.walk(domain_dir):
                    for file in files:
                        if not file.endswith(".yaml"):
                            continue

                        file_path = Path(root) / file
                        relative_path = file_path.relative_to(blueprints_dir)

                        data = load_yaml_file(str(file_path))
                        if not data:
                            blueprints.append(
                                {
                                    "type": domain,
                                    "path": relative_path.as_posix(),
                                    "error": "Could not parse YAML",
                                }
                            )
                            continue

                        meta = data.get("blueprint", {})

                        # Extract inputs
                        inputs = meta.get("input", {})
                        input_count = len(inputs) if isinstance(inputs, dict) else 0

                        blueprints.append(
                            {
                                "type": domain,
                                "path": relative_path.as_posix(),
                                "name": meta.get("name", file),
                                "description": (meta.get("description", "") or "")[
                                    :200
                                ],  # Truncate desc
                                "author": meta.get("author", "Unknown"),
                                "source_url": meta.get("source_url", ""),
                                "inputs_count": input_count,
                                "domain": meta.get("domain", domain),
                            }
                        )

            return json.dumps(
                {
                    "success": True,
                    "total_blueprints": len(blueprints),
                    "by_type": {
                        "automation": len([b for b in blueprints if b.get("type") == "automation"]),
                        "script": len([b for b in blueprints if b.get("type") == "script"]),
                    },
                    "blueprints": blueprints,
                },
                indent=2,
                ensure_ascii=False,
            )

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    def get_blueprint_code(blueprint_path: str) -> str:
        """
        Fetches full source YAML code of a blueprint.

        Args:
            blueprint_path: Relative path of blueprint (returned by list_blueprints),
                            e.g. "automation/homeassistant/motion_light.yaml".

        Returns:
            JSON with blueprint code or error.

        Example:
            get_blueprint_code("automation/motion_light.yaml")
        """
        try:
            blueprints_dir = Path(config_path) / "blueprints"
            full_path = blueprints_dir / blueprint_path

            # Security check - prevent path traversal
            if not str(full_path.resolve()).startswith(str(blueprints_dir.resolve())):
                return json.dumps(
                    {
                        "success": False,
                        "error": "Access denied - path traversal detected",
                    },
                    indent=2,
                )

            if not os.path.isfile(full_path):
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Blueprint '{blueprint_path}' not found",
                    },
                    indent=2,
                )

            # Check file size
            file_size = full_path.stat().st_size
            max_size = 200 * 1024  # 200KB

            with open(full_path, "r", encoding="utf-8") as f:
                if file_size > max_size:
                    content = f.read(max_size)
                    return json.dumps(
                        {
                            "success": True,
                            "blueprint_path": blueprint_path,
                            "code": content,
                            "truncated": True,
                            "file_size": file_size,
                            "warning": f"File too large ({file_size} bytes). Showing first {max_size // 1024}KB",
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                else:
                    content = f.read()
                    return json.dumps(
                        {
                            "success": True,
                            "blueprint_path": blueprint_path,
                            "code": content,
                            "truncated": False,
                            "file_size": file_size,
                        },
                        indent=2,
                        ensure_ascii=False,
                    )

        except Exception as e:
            return json.dumps(
                {"success": False, "error": f"Error reading blueprint: {str(e)}"},
                indent=2,
            )

    @mcp.tool()
    def get_blueprint_instances(blueprint_path: str) -> str:
        """
        Find all automations and scripts that use a specific blueprint.

        Args:
            blueprint_path: blueprint path (e.g. "automation/motion_light.yaml").

        Returns:
            JSON with list of instances and their configuration (inputs).

        Example:
            get_blueprint_instateces("automation/homeassistatet/motion_light.yaml")
        """
        try:
            instances = []

            # 1. Check Automations
            automations_path = Path(config_path) / "automations.yaml"
            if automations_path.exists():
                automations = load_yaml_file(str(automations_path)) or []

                # Handle both list and dict formats
                if isinstance(automations, dict):
                    automations = [automations]

                for auto in automations:
                    if not isinstance(auto, dict):
                        continue

                    if "use_blueprint" in auto:
                        bp_config = auto["use_blueprint"]

                        # Handle both string and dict formats
                        bp_path = bp_config if isinstance(bp_config, str) else bp_config.get("path")

                        if bp_path == blueprint_path:
                            instances.append(
                                {
                                    "type": "automation",
                                    "id": auto.get("id"),
                                    "alias": auto.get("alias", "Unnamed"),
                                    "inputs": bp_config.get("input", {})
                                    if isinstance(bp_config, dict)
                                    else {},
                                }
                            )

            # 2. Check Scripts
            scripts_path = Path(config_path) / "scripts.yaml"
            if scripts_path.exists():
                scripts = load_yaml_file(str(scripts_path))

                if scripts:
                    # Scripts can be dict (script_id: config) or list
                    if isinstance(scripts, dict):
                        for script_id, script_data in scripts.items():
                            if not isinstance(script_data, dict):
                                continue

                            if "use_blueprint" in script_data:
                                bp_config = script_data["use_blueprint"]
                                bp_path = (
                                    bp_config
                                    if isinstance(bp_config, str)
                                    else bp_config.get("path")
                                )

                                if bp_path == blueprint_path:
                                    instances.append(
                                        {
                                            "type": "script",
                                            "id": script_id,
                                            "alias": script_data.get("alias", script_id),
                                            "inputs": bp_config.get("input", {})
                                            if isinstance(bp_config, dict)
                                            else {},
                                        }
                                    )
                    elif isinstance(scripts, list):
                        for script in scripts:
                            if not isinstance(script, dict):
                                continue

                            if "use_blueprint" in script:
                                bp_config = script["use_blueprint"]
                                bp_path = (
                                    bp_config
                                    if isinstance(bp_config, str)
                                    else bp_config.get("path")
                                )

                                if bp_path == blueprint_path:
                                    instances.append(
                                        {
                                            "type": "script",
                                            "id": script.get("id"),
                                            "alias": script.get("alias", "Unnamed"),
                                            "inputs": bp_config.get("input", {})
                                            if isinstance(bp_config, dict)
                                            else {},
                                        }
                                    )

            return json.dumps(
                {
                    "success": True,
                    "blueprint": blueprint_path,
                    "usage_count": len(instances),
                    "instances": instances,
                    "summary": {
                        "automations": len([i for i in instances if i["type"] == "automation"]),
                        "scripts": len([i for i in instances if i["type"] == "script"]),
                    },
                },
                indent=2,
                ensure_ascii=False,
            )

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    def get_blueprint_usage_summary() -> str:
        """
        Summary of blueprint usage.

        Returns:
            JSON with summary (most used blueprints, unused, etc.)
        """
        try:
            # Get all blueprints
            list_res = json.loads(list_blueprints())
            if not list_res.get("success"):
                return json.dumps(list_res)

            all_blueprints = list_res.get("blueprints", [])
            usage_stats = []

            for bp in all_blueprints:
                path = bp.get("path")
                instances_res = json.loads(get_blueprint_instances(path))

                if instances_res.get("success"):
                    count = instances_res.get("usage_count", 0)
                    summary = instances_res.get("summary", {})

                    usage_stats.append(
                        {
                            "path": path,
                            "name": bp.get("name"),
                            "domain": bp.get("domain"),
                            "usage_count": count,
                            "automations": summary.get("automations", 0),
                            "scripts": summary.get("scripts", 0),
                        }
                    )

            # Sort by usage
            usage_stats.sort(key=lambda x: x["usage_count"], reverse=True)

            return json.dumps(
                {
                    "success": True,
                    "total_blueprints": len(all_blueprints),
                    "total_instances": sum(s["usage_count"] for s in usage_stats),
                    "most_used": usage_stats[:5],
                    "unused": [s for s in usage_stats if s["usage_count"] == 0],
                },
                indent=2,
                ensure_ascii=False,
            )

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)
