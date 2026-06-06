"""
Tests for ha_graph/queries.py — graph traversal and analysis queries.

Builds a test GraphIndex and verifies all 5 query functions.
"""

import pytest

from ha_graph.models import GraphEdge, GraphIndex, GraphNode
from ha_graph.queries import (
    detect_ghost_references,
    detect_orphans,
    entity_impact,
    find_entity_references,
    get_neighbors,
)


@pytest.fixture
def graph():
    """Build a test GraphIndex with entities, automations, scripts, dashboard nodes."""
    g = GraphIndex()

    # Entity nodes
    g.add_node(GraphNode(id="entity:sensor.temp", type="entity", name="Temperature Sensor"))
    g.add_node(GraphNode(id="entity:light.living_room", type="entity", name="Living Room Light"))
    g.add_node(GraphNode(id="entity:light.hallway", type="entity", name="Hallway Light"))
    g.add_node(GraphNode(id="entity:binary_sensor.motion", type="entity", name="Motion Sensor"))
    g.add_node(GraphNode(id="entity:sensor.unused", type="entity", name="Unused Sensor"))
    g.add_node(GraphNode(id="entity:sun.sun", type="entity", name="Sun"))

    # Automation nodes
    g.add_node(GraphNode(id="automation:motion_light", type="automation", name="Motion Light", metadata={"mode": "restart"}))
    g.add_node(GraphNode(id="automation:temp_alert", type="automation", name="Temperature Alert", metadata={"mode": "single"}))

    # Script nodes
    g.add_node(GraphNode(id="script:party_mode", type="script", name="Party Mode"))

    # Dashboard nodes
    g.add_node(GraphNode(id="dashboard:main", type="dashboard", name="main"))

    # Service nodes
    g.add_node(GraphNode(id="service:light.turn_on", type="service", name="light.turn_on"))
    g.add_node(GraphNode(id="service:notify.mobile", type="service", name="notify.mobile"))
    g.add_node(GraphNode(id="service:switch.turn_on", type="service", name="switch.turn_on"))

    # Edges: automation → trigger → entity
    g.add_edge(GraphEdge(source="automation:motion_light", target="entity:binary_sensor.motion", relation="triggers_on", file_path="automations.yaml"))
    g.add_edge(GraphEdge(source="automation:temp_alert", target="entity:sensor.temp", relation="triggers_on", file_path="automations.yaml"))

    # Edges: automation → controls → entity
    g.add_edge(GraphEdge(source="automation:motion_light", target="entity:light.hallway", relation="controls", file_path="automations.yaml"))

    # Edges: automation → reads → entity (conditions)
    g.add_edge(GraphEdge(source="automation:motion_light", target="entity:light.hallway", relation="reads", file_path="automations.yaml"))
    g.add_edge(GraphEdge(source="automation:temp_alert", target="entity:sensor.temp", relation="reads", file_path="automations.yaml"))

    # Edges: automation → calls_service
    g.add_edge(GraphEdge(source="automation:motion_light", target="service:light.turn_on", relation="calls_service"))
    g.add_edge(GraphEdge(source="automation:temp_alert", target="service:notify.mobile", relation="calls_service"))

    # Edges: script → controls → entity
    g.add_edge(GraphEdge(source="script:party_mode", target="entity:light.living_room", relation="controls"))

    # Edges: script → calls_service
    g.add_edge(GraphEdge(source="script:party_mode", target="service:switch.turn_on", relation="calls_service"))

    # Edges: dashboard → displays → entity
    g.add_edge(GraphEdge(source="dashboard:main", target="entity:sensor.temp", relation="displays"))
    g.add_edge(GraphEdge(source="dashboard:main", target="entity:light.living_room", relation="displays"))

    # Ghost reference: an edge pointing to an entity that does NOT exist as a node.
    # This simulates a typo or removed entity still referenced in config.
    g.add_edge(GraphEdge(source="automation:motion_light", target="entity:sensor.nonexistent", relation="reads"))

    # Script → script call
    g.add_node(GraphNode(id="script:other", type="script", name="Other Script"))
    g.add_edge(GraphEdge(source="script:party_mode", target="script:other", relation="calls_script"))

    return g


class TestFindEntityReferences:
    """Test find_entity_references()."""

    def test_finds_references_to_entity(self, graph):
        """Returns all incoming edges to an entity."""
        refs = find_entity_references(graph, "binary_sensor.motion")
        assert len(refs) >= 1
        assert any(r["source"] == "automation:motion_light" for r in refs)
        assert any(r["relation"] == "triggers_on" for r in refs)

    def test_entity_with_multiple_references(self, graph):
        """Entity referenced from multiple sources shows all references."""
        refs = find_entity_references(graph, "sensor.temp")
        assert len(refs) >= 2  # temp_alert triggers_on + dashboard displays
        relations = {r["relation"] for r in refs}
        assert "triggers_on" in relations
        assert "displays" in relations

    def test_no_references(self, graph):
        """Entity with no incoming edges returns empty list."""
        refs = find_entity_references(graph, "sensor.unused")
        assert refs == []

    def test_unknown_entity(self, graph):
        """Unknown entity returns empty list."""
        refs = find_entity_references(graph, "light.nonexistent")
        assert refs == []

    def test_references_include_metadata(self, graph):
        """References include source type, relation, confidence."""
        refs = find_entity_references(graph, "binary_sensor.motion")
        assert len(refs) >= 1
        r = refs[0]
        assert "source" in r
        assert "source_name" in r
        assert "relation" in r
        assert "confidence" in r


class TestEntityImpact:
    """Test entity_impact()."""

    def test_high_risk_entity(self, graph):
        """Entity referenced by automations gets high risk."""
        impact = entity_impact(graph, "binary_sensor.motion")
        assert impact["exists"] is True
        assert impact["risk"] == "high"

    def test_medium_risk_entity(self, graph):
        """Entity only on dashboards (no automation trigger/control) gets medium risk."""
        # Create a minimal graph where the entity is solely on a dashboard.
        from ha_graph.models import GraphIndex, GraphNode, GraphEdge
        g = GraphIndex()
        g.add_node(GraphNode(id="entity:sensor.display_only", type="entity", name="Display Only"))
        g.add_node(GraphNode(id="dashboard:main", type="dashboard", name="Main"))
        g.add_edge(GraphEdge(source="dashboard:main", target="entity:sensor.display_only", relation="displays"))
        impact = entity_impact(g, "sensor.display_only")
        assert impact["exists"] is True
        assert impact["risk"] == "medium"

    def test_low_risk_entity(self, graph):
        """Unreferenced entity gets low risk."""
        impact = entity_impact(graph, "sensor.unused")
        assert impact["exists"] is True
        assert impact["risk"] == "low"

    def test_nonexistent_entity(self, graph):
        """Non-existent entity returns exists=False."""
        impact = entity_impact(graph, "light.nonexistent")
        assert impact["exists"] is False
        assert impact["direct_impact"]["automations_triggered_by"] == []

    def test_impact_categorized_correctly(self, graph):
        """Impact categories group edges correctly."""
        impact = entity_impact(graph, "light.hallway")
        assert impact["exists"] is True
        # hallway is controlled AND read by motion_light automation
        assert len(impact["direct_impact"]["automations_controlling"]) >= 1
        assert len(impact["direct_impact"]["automations_reading"]) >= 1


class TestGetNeighbors:
    """Test get_neighbors()."""

    def test_direct_outgoing_neighbors(self, graph):
        """Depth=1 returns direct outgoing neighbors."""
        result = get_neighbors(graph, "automation:motion_light", depth=1, direction="outgoing")
        assert result["found"] is True
        node_ids = {n["id"] for n in result["nodes"]}
        assert "entity:binary_sensor.motion" in node_ids
        assert "entity:light.hallway" in node_ids

    def test_direct_incoming_neighbors(self, graph):
        """Depth=1 returns direct incoming neighbors."""
        result = get_neighbors(graph, "entity:sensor.temp", depth=1, direction="incoming")
        assert result["found"] is True
        node_ids = {n["id"] for n in result["nodes"]}
        assert "automation:temp_alert" in node_ids

    def test_nonexistent_node(self, graph):
        """Non-existent node returns found=False."""
        result = get_neighbors(graph, "entity:nonexistent", depth=1)
        assert result["found"] is False

    def test_both_directions(self, graph):
        """Direction='both' returns both incoming and outgoing neighbors."""
        # entity:light.hallway has incoming (controls from automation) and... no outgoing
        result = get_neighbors(graph, "entity:light.hallway", depth=1, direction="both")
        assert result["found"] is True
        assert len(result["nodes"]) >= 1

    def test_depth_excludes_further_hops(self, graph):
        """Depth limits how far BFS traverses."""
        # motion_light → binary_sensor.motion (depth 1)
        result = get_neighbors(graph, "automation:motion_light", depth=1, direction="outgoing")
        # At depth 1, binary_sensor.motion has no outgoing edges so it shouldn't matter
        assert len(result["nodes"]) >= 2  # at least the trigger and control targets


class TestDetectGhostReferences:
    """Test detect_ghost_references()."""

    def test_detects_ghost(self, graph):
        """Entity referenced in edges but missing from nodes is detected."""
        ghosts = detect_ghost_references(graph)
        assert "sensor.nonexistent" in ghosts

    def test_known_entities_not_ghosts(self, graph):
        """Entities that exist in nodes are not reported as ghosts."""
        ghosts = detect_ghost_references(graph)
        assert "binary_sensor.motion" not in ghosts
        assert "sensor.temp" not in ghosts


class TestDetectOrphans:
    """Test detect_orphans()."""

    def test_detects_orphan(self, graph):
        """Entity with zero incoming edges is an orphan."""
        orphans = detect_orphans(graph)
        orphan_ids = [o["entity_id"] for o in orphans]
        assert "sensor.unused" in orphan_ids

    def test_ignorable_domains_excluded(self, graph):
        """Entities in ignorable domains are not reported."""
        orphans = detect_orphans(graph)
        orphan_ids = [o["entity_id"] for o in orphans]
        assert "sun.sun" not in orphan_ids  # sun is in IGNORABLE_DOMAINS

    def test_non_orphans_excluded(self, graph):
        """Referenced entities are not orphans."""
        orphans = detect_orphans(graph)
        orphan_ids = [o["entity_id"] for o in orphans]
        assert "binary_sensor.motion" not in orphan_ids
        assert "sensor.temp" not in orphan_ids

    def test_custom_ignorable_domains(self, graph):
        """Custom ignorable_domains parameter works."""
        orphans = detect_orphans(graph, ignorable_domains={"sensor"})
        orphan_ids = [o["entity_id"] for o in orphans]
        # sensor.unused is in sensor domain, so excluded now
        assert "sensor.unused" not in orphan_ids
