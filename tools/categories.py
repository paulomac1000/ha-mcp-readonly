"""
Category Registry Tools
Read-only access to Home Assistant category registry for automations, scripts,
scenes, and helpers.
"""

import logging
from collections import defaultdict
from typing import Any

from tools.manifests import make_manifest, register_manifest
from tools.utils import _error_response, _success_response, load_registry

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"


def _do_list_automation_categories(
    include_entity_count: bool,
    config_path: str,
) -> dict[str, Any]:
    """Read category registry and optionally count assignments."""
    category_data = load_registry("core.category_registry", config_path)
    categories_raw = category_data.get("data", {}).get("categories", [])
    if not categories_raw:
        return {
            "success": True,
            "categories": [],
            "total": 0,
        }

    categories = []
    for cat in categories_raw:
        if isinstance(cat, str):
            categories.append(
                {
                    "category_id": cat,
                    "name": cat,
                    "icon": None,
                    "scope": None,
                }
            )
        else:
            categories.append(
                {
                    "category_id": cat.get("category_id", ""),
                    "name": cat.get("name", ""),
                    "icon": cat.get("icon", None),
                    "scope": cat.get("scope", None),
                }
            )

    empty_categories = []
    if include_entity_count and config_path:
        entity_data = load_registry("core.entity_registry", config_path)
        entities = entity_data.get("data", {}).get("entities", [])
        scope_counts: dict[str, int] = defaultdict(int)
        scope_map: dict[str, list[str]] = defaultdict(list)
        for ent in entities:
            ent_categories = ent.get("categories", {})
            if isinstance(ent_categories, dict):
                for scope, cat_id in ent_categories.items():
                    if cat_id:
                        scope_counts[cat_id] += 1
                        scope_map[cat_id].append(scope)
        for cat in categories:
            cid = cat["category_id"]
            cat["entity_count"] = scope_counts.get(cid, 0)
            if cat["entity_count"] == 0:
                empty_categories.append(cid)

    categories.sort(key=lambda x: (x.get("scope", ""), x.get("name", "")))
    return {
        "success": True,
        "categories": categories,
        "total": len(categories),
        "empty_categories": empty_categories if include_entity_count else [],
    }


def register_categories_tools(mcp, config_path: str) -> None:  # type: ignore[no-untyped-def]
    """Register category registry read-only tools.

    Args:
        mcp: FastMCP instance.
        config_path: Path to HA configuration directory.
    """
    register_manifest(
        "list_automation_categories",
        make_manifest("list_automation_categories", latency="fast"),
    )

    @mcp.tool()
    def list_automation_categories(include_entity_count: bool = True) -> str:
        """Lists all Home Assistant categories with their IDs, names, and icons.

        Reads the category registry to discover organisational categories for
        automations, scripts, scenes, and helpers. Optionally counts how many
        entities are assigned to each category.

        Args:
            include_entity_count: Whether to count entity assignments per category (default: True).

        Returns:
            JSON with categories list, total count, and empty_categories.
        """
        try:
            data = _do_list_automation_categories(include_entity_count, config_path)
            if data.get("success") is False:
                return _error_response(data.get("error", data))
            return _success_response(data)
        except Exception as exc:
            _logger.exception("list_automation_categories failed")
            return _error_response(str(exc))
