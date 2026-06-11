"""HA Semantic Graph data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

NodeType = Literal[
    "entity",
    "automation",
    "script",
    "scene",
    "dashboard",
    "device",
    "area",
    "integration",
    "service",
    "file",
    "template",
    "blueprint",
    "helper",
    "unknown",
]

RelationType = Literal[
    "triggers_on",
    "reads",
    "controls",
    "calls_service",
    "calls_script",
    "activates_scene",
    "displays",
    "belongs_to_device",
    "belongs_to_area",
    "from_integration",
    "defined_in",
    "includes",
    "uses_blueprint",
    "has_entity",
    "via_device",
    "unknown_reference",
]

Confidence = Literal["exact", "inferred", "dynamic", "weak"]


class GraphError(Exception):
    """Base exception for graph operations."""


@dataclass(frozen=True)
class GraphNode:
    id: str  # prefixed: "entity:light.hall", "automation:morning_routine"
    type: NodeType
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    source: str  # source node id
    target: str | None  # target node id (None for dynamic references)
    relation: RelationType
    confidence: Confidence = "exact"
    file_path: str | None = None
    object_path: str | None = None  # JSON path like "[0].trigger[0].entity_id"
    line: int | None = None
    evidence: str | None = None  # snippet of source text
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphIndex:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    built_at: float | None = None
    stats: dict[str, Any] = field(default_factory=dict)

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        self.edges.append(edge)

    def outgoing(self, node_id: str) -> list[GraphEdge]:
        return [e for e in self.edges if e.source == node_id]

    def incoming(self, node_id: str) -> list[GraphEdge]:
        return [e for e in self.edges if e.target == node_id]
