"""MCP tools for HA Semantic Graph queries.

Provides 7 read-only tools for exploring the Home Assistant semantic graph:
graph_build_index, graph_find_references, graph_entity_impact,
graph_get_neighbors, graph_detect_ghost_references, graph_detect_orphans,
graph_export_mermaid.
"""

import logging
from typing import Any

from ha_graph.cache import get_graph_index
from ha_graph.export import export_mermaid
from ha_graph.queries import (
    detect_ghost_references,
    detect_orphans,
    entity_impact,
    find_entity_references,
    get_neighbors,
)
from tools.utils import _error_response, _success_response

_logger = logging.getLogger(__name__)


def register_graph_tools(
    mcp: Any, config_path: str, ha_url: str | None, ha_token: str | None
) -> None:
    """Register all 7 graph exploration MCP tools."""

    @mcp.tool()
    async def graph_build_index(force: bool = False) -> str:
        """[READ] Build or refresh the HA Semantic Graph from configuration files and registries.

        Args:
            force: If True, rebuild the graph ignoring cache. Default False.

        Returns:
            JSON with nodes_count, edges_count, built_at timestamp, and stats.
        """
        try:
            index = await get_graph_index(config_path, ha_url, ha_token, force=force)
            return _success_response({
                "nodes_count": len(index.nodes),
                "edges_count": len(index.edges),
                "built_at": index.built_at,
                "stats": index.stats,
            })
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def graph_find_references(entity_id: str) -> str:
        """[READ] Find all semantic references to an entity in the HA graph.

        Args:
            entity_id: The entity ID to search for (e.g. 'light.living_room').

        Returns:
            JSON with references list containing source, relation, confidence.
        """
        try:
            index = await get_graph_index(config_path, ha_url, ha_token)
            refs = find_entity_references(index, entity_id)
            return _success_response({
                "entity_id": entity_id,
                "total_references": len(refs),
                "references": refs,
            })
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def graph_entity_impact(entity_id: str) -> str:
        """[READ] Analyze the potential impact of modifying or removing an entity.

        Args:
            entity_id: The entity ID to analyze (e.g. 'light.living_room').

        Returns:
            JSON with risk level and categorized impact by automation, script, dashboard usage.
        """
        try:
            index = await get_graph_index(config_path, ha_url, ha_token)
            result = entity_impact(index, entity_id)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def graph_get_neighbors(
        node_id: str, depth: int = 1, direction: str = "both"
    ) -> str:
        """[READ] Get the subgraph around a specific node in the HA semantic graph.

        Args:
            node_id: The node ID with prefix (e.g. 'entity:light.living_room' or 'automation:morning_routine').
            depth: How many hops to traverse (default 1, max 5).
            direction: Traversal direction — 'incoming', 'outgoing', or 'both' (default).

        Returns:
            JSON with list of neighbor nodes and connecting edges.
        """
        try:
            depth = min(max(depth, 1), 5)
            if direction not in ("incoming", "outgoing", "both"):
                return _error_response(
                    "direction must be 'incoming', 'outgoing', or 'both'"
                )
            index = await get_graph_index(config_path, ha_url, ha_token)
            result = get_neighbors(index, node_id, depth=depth, direction=direction)
            return _success_response(result)
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def graph_detect_ghost_references() -> str:
        """[READ] Detect entity references that no longer exist in the entity registry.

        Returns:
            JSON with list of ghost entity IDs (referenced in config but not in registry).
        """
        try:
            index = await get_graph_index(config_path, ha_url, ha_token)
            ghosts = detect_ghost_references(index)
            return _success_response({
                "total_ghosts": len(ghosts),
                "ghost_entities": ghosts,
            })
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def graph_detect_orphans(ignorable_domains: str | None = None) -> str:
        """[READ] Detect entities in the registry with no incoming edges.

        Args:
            ignorable_domains: Optional comma-separated list of domains to exclude (e.g. 'sun,update').

        Returns:
            JSON with list of orphan entities.
        """
        try:
            domains: set[str] | None = None
            if ignorable_domains:
                domains = {
                    d.strip() for d in ignorable_domains.split(",") if d.strip()
                }
            index = await get_graph_index(config_path, ha_url, ha_token)
            orphans = detect_orphans(index, ignorable_domains=domains)
            return _success_response({
                "total_orphans": len(orphans),
                "orphan_entities": orphans,
            })
        except Exception as e:
            return _error_response(str(e))

    @mcp.tool()
    async def graph_export_mermaid(node_id: str, depth: int = 2) -> str:
        """[READ] Export a subgraph as Mermaid flowchart syntax for visualization.

        Args:
            node_id: Starting node with prefix (e.g. 'entity:light.living_room'). Required.
                     Full-graph export not supported.
            depth: How many hops to traverse (default 2, max 4).

        Returns:
            JSON with 'mermaid' field containing Mermaid graph TD string.
        """
        try:
            depth = min(max(depth, 1), 4)
            index = await get_graph_index(config_path, ha_url, ha_token)
            mermaid = export_mermaid(index, node_id=node_id, depth=depth)
            return _success_response({"mermaid": mermaid})
        except Exception as e:
            return _error_response(str(e))
