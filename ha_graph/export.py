"""HA Semantic Graph — Mermaid export."""
from collections import deque

from ha_graph.models import GraphIndex

MAX_NODES = 50


def export_mermaid(index: GraphIndex, node_id: str | None = None, depth: int = 2) -> str:
    """Export a subgraph as Mermaid flowchart syntax.

    Args:
        index: The graph index to export from.
        node_id: The starting node (REQUIRED). Full export not supported.
        depth: How many hops to traverse (default 2).

    Returns:
        Mermaid graph TD string.
    """
    if not node_id:
        raise ValueError("node_id is required. Full-graph export is not supported.")

    if node_id not in index.nodes:
        return f"graph TD\n  A[\"Node '{node_id}' not found\"]"

    visited: set[str] = set()
    edge_lines: list[str] = []
    node_counter: int = 0
    node_map: dict[str, str] = {}
    queue: deque[tuple[str, int]] = deque()
    queue.append((node_id, 0))
    visited.add(node_id)

    def _get_label(nid: str) -> str:
        node = index.nodes.get(nid)
        if node and node.name:
            # Escape quotes for Mermaid
            label = node.name.replace('"', "'")
            return f'{nid.split(":")[0]}: {label}'
        return nid

    while queue and node_counter < MAX_NODES:
        current_id, current_depth = queue.popleft()

        if current_id not in node_map:
            node_counter += 1
            node_map[current_id] = f"N{node_counter}"
            label = _get_label(current_id)
            edge_lines.insert(0, f'  {node_map[current_id]}["{label}"]')

        if current_depth >= depth:
            continue

        for edge in index.outgoing(current_id):
            if edge.target and edge.target not in visited:
                visited.add(edge.target)
                queue.append((edge.target, current_depth + 1))
            if edge.target:
                if edge.target not in node_map:
                    node_counter += 1
                    node_map[edge.target] = f"N{node_counter}"
                    label = _get_label(edge.target)
                    edge_lines.append(f'  {node_map[edge.target]}["{label}"]')
                edge_lines.append(
                    f'  {node_map[current_id]} -->|{edge.relation}| {node_map[edge.target]}'
                )

    lines = ["graph TD"] + edge_lines
    return "\n".join(lines)
