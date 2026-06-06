"""
Tests for ha_graph/models.py — GraphNode, GraphEdge, GraphIndex.
"""

import pytest

from ha_graph.models import (
    Confidence,
    GraphEdge,
    GraphError,
    GraphIndex,
    GraphNode,
    NodeType,
    RelationType,
)


class TestGraphNode:
    """Test GraphNode creation and immutability."""

    def test_create_basic_node(self):
        """A basic GraphNode with id and type is created correctly."""
        node = GraphNode(id="entity:light.living_room", type="entity", name="Living Room Light")
        assert node.id == "entity:light.living_room"
        assert node.type == "entity"
        assert node.name == "Living Room Light"
        assert node.metadata == {}

    def test_node_with_metadata(self):
        """GraphNode stores extra metadata."""
        node = GraphNode(
            id="automation:123",
            type="automation",
            name="Motion Light",
            metadata={"mode": "restart", "source": "yaml"},
        )
        assert node.metadata["mode"] == "restart"
        assert node.metadata["source"] == "yaml"

    def test_node_is_frozen(self):
        """GraphNode is a frozen dataclass and cannot be mutated."""
        node = GraphNode(id="entity:sensor.temp", type="entity")
        with pytest.raises(Exception):  # noqa: PT011
            node.name = "Changed"  # type: ignore[misc]

    def test_node_types_are_valid_literals(self):
        """NodeType accepts all expected literal values."""
        for nt in ("entity", "automation", "script", "scene", "dashboard",
                   "device", "area", "integration", "service", "file",
                   "template", "blueprint", "helper", "unknown"):
            node = GraphNode(id=f"{nt}:test", type=nt)  # type: ignore[arg-type]
            assert node.type == nt


class TestGraphEdge:
    """Test GraphEdge creation and fields."""

    def test_create_basic_edge(self):
        """A basic GraphEdge stores source, target, and relation."""
        edge = GraphEdge(
            source="automation:123",
            target="entity:light.living_room",
            relation="controls",
        )
        assert edge.source == "automation:123"
        assert edge.target == "entity:light.living_room"
        assert edge.relation == "controls"
        assert edge.confidence == "exact"

    def test_edge_with_all_fields(self):
        """GraphEdge stores optional fields correctly."""
        edge = GraphEdge(
            source="automation:123",
            target="entity:sensor.temp",
            relation="reads",
            confidence="inferred",
            file_path="automations.yaml",
            object_path="[0].condition",
            line=42,
            evidence="platform:state",
            metadata={"key": "val"},
        )
        assert edge.confidence == "inferred"
        assert edge.file_path == "automations.yaml"
        assert edge.object_path == "[0].condition"
        assert edge.line == 42
        assert edge.evidence == "platform:state"
        assert edge.metadata == {"key": "val"}

    def test_edge_is_frozen(self):
        """GraphEdge is a frozen dataclass and cannot be mutated."""
        edge = GraphEdge(source="a", target="b", relation="reads")
        with pytest.raises(Exception):  # noqa: PT011
            edge.source = "changed"  # type: ignore[misc]


class TestGraphIndex:
    """Test GraphIndex operations."""

    def test_empty_index(self):
        """A fresh GraphIndex has no nodes or edges."""
        index = GraphIndex()
        assert index.nodes == {}
        assert index.edges == []
        assert index.built_at is None

    def test_add_node(self):
        """add_node stores nodes keyed by id."""
        index = GraphIndex()
        node = GraphNode(id="entity:light.hall", type="entity", name="Hall Light")
        index.add_node(node)
        assert index.nodes["entity:light.hall"] == node

    def test_add_edge(self):
        """add_edge appends edges to the list."""
        index = GraphIndex()
        edge = GraphEdge(source="a", target="b", relation="controls")
        index.add_edge(edge)
        assert len(index.edges) == 1
        assert index.edges[0] == edge

    def test_outgoing(self):
        """outgoing returns edges where this node is the source."""
        index = GraphIndex()
        index.add_edge(GraphEdge(source="a", target="b", relation="controls"))
        index.add_edge(GraphEdge(source="a", target="c", relation="reads"))
        index.add_edge(GraphEdge(source="d", target="a", relation="triggers_on"))
        out = index.outgoing("a")
        assert len(out) == 2
        assert all(e.source == "a" for e in out)

    def test_incoming(self):
        """incoming returns edges where this node is the target."""
        index = GraphIndex()
        index.add_edge(GraphEdge(source="a", target="b", relation="controls"))
        index.add_edge(GraphEdge(source="c", target="b", relation="reads"))
        index.add_edge(GraphEdge(source="b", target="d", relation="triggers_on"))
        inc = index.incoming("b")
        assert len(inc) == 2
        assert all(e.target == "b" for e in inc)

    def test_add_node_overwrites_same_id(self):
        """add_node overwrites an existing node with the same id."""
        index = GraphIndex()
        n1 = GraphNode(id="entity:test", type="entity", name="First")
        n2 = GraphNode(id="entity:test", type="entity", name="Second")
        index.add_node(n1)
        index.add_node(n2)
        assert index.nodes["entity:test"].name == "Second"

    def test_stats_defaults(self):
        """GraphIndex.stats defaults to empty dict."""
        index = GraphIndex()
        assert index.stats == {}
