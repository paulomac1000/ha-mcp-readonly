"""HA Semantic Graph — graph traversal and analysis query functions.

Provides five core queries for exploring the semantic graph:
  1. find_entity_references — what references a given entity?
  2. entity_impact — categorized impact analysis by relation type
  3. get_neighbors — BFS subgraph within N hops
  4. detect_ghost_references — entities in edges but not in the entity registry
  5. detect_orphans — entities in registry with zero incoming semantic edges

All queries are read-only — they never mutate the GraphIndex.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from ha_graph.models import GraphEdge, GraphIndex

IGNORABLE_DOMAINS: set[str] = {
    "sun",
    "update",
    "persistent_notification",
    "zone",
    "scene",
    "script",
}


def find_entity_references(index: GraphIndex, entity_id: str) -> list[dict[str, Any]]:
    """Find all semantic references to an entity in the graph.

    Returns a list of reference entries, each describing which node
    references the entity, on what relation, and with what confidence.

    Args:
        index: The built GraphIndex to query.
        entity_id: The entity ID to look up (e.g. ``"light.living_room"``
            or ``"entity:light.living_room"``).

    Returns:
        A list of dicts, each with keys: ``source``, ``source_name``,
        ``relation``, ``confidence``, ``file_path``, ``object_path``.
    """
    node_id = f"entity:{entity_id}" if not entity_id.startswith("entity:") else entity_id
    incoming = index.incoming(node_id)
    results: list[dict[str, Any]] = []
    for edge in incoming:
        source_node = index.nodes.get(edge.source)
        results.append(
            {
                "source": edge.source,
                "source_name": source_node.name if source_node else edge.source,
                "relation": edge.relation,
                "confidence": edge.confidence,
                "file_path": edge.file_path,
                "object_path": edge.object_path,
            }
        )
    return results


def entity_impact(index: GraphIndex, entity_id: str) -> dict[str, Any]:
    """Analyze the impact of changing or removing an entity.

    Categorizes all incoming edges by relation type and source node type,
    computing a risk level (``"high"``, ``"medium"``, ``"low"``) based on
    whether the entity is referenced by automations, scripts, or dashboards.

    Args:
        index: The built GraphIndex to query.
        entity_id: The entity ID to analyze.

    Returns:
        A dict with keys: ``entity_id``, ``exists``, ``risk``,
        ``direct_impact`` (dict of categorized references), ``edges`` (flat list).
    """
    node_id = f"entity:{entity_id}" if not entity_id.startswith("entity:") else entity_id
    impact: dict[str, list[dict[str, Any]]] = {
        "automations_triggered_by": [],
        "automations_reading": [],
        "automations_controlling": [],
        "scripts_controlling": [],
        "dashboards_displaying": [],
        "other": [],
    }
    node = index.nodes.get(node_id)
    if not node:
        return {
            "entity_id": entity_id,
            "exists": False,
            "direct_impact": impact,
            "edges": [],
        }

    incoming = index.incoming(node_id)
    edges_list: list[dict[str, Any]] = []
    for edge in incoming:
        source_node = index.nodes.get(edge.source)
        entry: dict[str, Any] = {
            "source": edge.source,
            "source_name": source_node.name if source_node else edge.source,
            "relation": edge.relation,
            "confidence": edge.confidence,
        }
        edges_list.append(entry)
        source_type = source_node.type if source_node else ""
        if edge.relation == "triggers_on" and source_type == "automation":
            impact["automations_triggered_by"].append(entry)
        elif edge.relation == "reads" and source_type == "automation":
            impact["automations_reading"].append(entry)
        elif edge.relation == "controls":
            if source_type == "automation":
                impact["automations_controlling"].append(entry)
            elif source_type == "script":
                impact["scripts_controlling"].append(entry)
        elif edge.relation == "displays" and source_type == "dashboard":
            impact["dashboards_displaying"].append(entry)
        else:
            impact["other"].append(entry)

    risk = "low"
    if impact["automations_triggered_by"] or impact["automations_controlling"]:
        risk = "high"
    elif impact["dashboards_displaying"]:
        risk = "medium"

    return {
        "entity_id": entity_id,
        "exists": True,
        "risk": risk,
        "direct_impact": impact,
        "edges": edges_list,
    }


def get_neighbors(
    index: GraphIndex,
    node_id: str,
    depth: int = 1,
    direction: str = "both",
) -> dict[str, Any]:
    """Get the subgraph around a node within N hops using BFS.

    Args:
        index: The built GraphIndex to query.
        node_id: The starting node ID (prefixed, e.g. ``"entity:light.living_room"``).
        depth: Maximum number of hops from the start node (default ``1``).
        direction: Traversal direction — ``"outgoing"``, ``"incoming"``,
            or ``"both"`` (default ``"both"``).

    Returns:
        A dict with keys: ``node_id``, ``found``, ``depth``, ``direction``,
        ``nodes`` (list of node dicts), ``edges`` (list of edge dicts).
    """
    if node_id not in index.nodes:
        return {"node_id": node_id, "found": False, "nodes": [], "edges": []}

    visited: set[str] = set()
    subgraph_nodes: dict[str, Any] = {}
    subgraph_edges: list[dict[str, Any]] = []
    queue: deque[tuple[str, int]] = deque()
    queue.append((node_id, 0))
    visited.add(node_id)

    while queue:
        current_id, current_depth = queue.popleft()
        node = index.nodes.get(current_id)
        if node:
            subgraph_nodes[current_id] = {
                "id": current_id,
                "type": node.type,
                "name": node.name,
            }

        if current_depth >= depth:
            continue

        # Choose traversal direction
        candidates: list[GraphEdge] = []
        if direction in ("outgoing", "both"):
            candidates.extend(index.outgoing(current_id))
        if direction in ("incoming", "both"):
            candidates.extend(index.incoming(current_id))

        for edge in candidates:
            neighbor_id = edge.target if edge.source == current_id else edge.source
            if neighbor_id and neighbor_id not in visited:
                visited.add(neighbor_id)
                queue.append((neighbor_id, current_depth + 1))
            subgraph_edges.append(
                {
                    "source": edge.source,
                    "target": edge.target,
                    "relation": edge.relation,
                }
            )

    return {
        "node_id": node_id,
        "found": True,
        "depth": depth,
        "direction": direction,
        "nodes": list(subgraph_nodes.values()),
        "edges": subgraph_edges,
    }


def detect_ghost_references(index: GraphIndex) -> list[str]:
    """Find entity IDs referenced in graph edges but not present in the entity registry.

    Ghost references typically indicate:
    - A typo in an automation or script entity_id
    - A device/entity that was removed but still referenced in configuration
    - A newly-added entity reference where the entity hasn't been registered yet

    Args:
        index: The built GraphIndex to scan.

    Returns:
        Sorted list of entity IDs (without ``"entity:"`` prefix) that are
        referenced by at least one edge but have no corresponding node
        of type ``"entity"`` in the node registry.
    """
    entity_node_ids = {nid for nid, n in index.nodes.items() if n.type == "entity"}
    referenced_entity_ids: set[str] = set()
    for edge in index.edges:
        if edge.target and edge.target.startswith("entity:"):
            referenced_entity_ids.add(edge.target.removeprefix("entity:"))
    ghosts: list[str] = []
    for eid in referenced_entity_ids:
        entity_key = f"entity:{eid}"
        if entity_key not in entity_node_ids:
            ghosts.append(eid)
    return sorted(ghosts)


def detect_orphans(
    index: GraphIndex,
    ignorable_domains: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Find entities in the entity registry that have zero incoming semantic edges.

    Orphan entities may be:
    - Unused sensors or redundant configuration entries
    - Entities that exist but are not referenced by any automation, script,
      or dashboard — candidates for cleanup.

    Args:
        index: The built GraphIndex to scan.
        ignorable_domains: Set of domains to exclude from orphan detection
            (defaults to ``IGNORABLE_DOMAINS`` — sun, update, persistent_notification,
            zone, scene, script).

    Returns:
        Sorted list of dicts, each with keys ``entity_id`` and ``name``.
    """
    if ignorable_domains is None:
        ignorable_domains = IGNORABLE_DOMAINS
    entity_node_ids = {nid for nid, n in index.nodes.items() if n.type == "entity"}
    entities_with_incoming: set[str] = set()
    for edge in index.edges:
        if edge.target and edge.target.startswith("entity:"):
            entities_with_incoming.add(edge.target)
    orphans: list[dict[str, Any]] = []
    for nid in entity_node_ids:
        if nid not in entities_with_incoming:
            eid = nid.removeprefix("entity:")
            domain = eid.split(".")[0] if "." in eid else ""
            if domain not in ignorable_domains:
                node = index.nodes[nid]
                orphans.append({"entity_id": eid, "name": node.name})
    return sorted(orphans, key=lambda x: x["entity_id"])
