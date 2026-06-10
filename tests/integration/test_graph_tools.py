"""
Integration Tests for HA Semantic Graph Tools.

Runs against a real Home Assistant instance via MCPWrapper.
All tests verify success: True and structural response fields.

RUN:
    pytest tests/integration/test_graph_tools.py -v
"""

import json

import pytest

# Tests skip automatically via conftest.py's real_mcp fixture
# which calls pytest.skip() when HA_URL or HA_TOKEN are missing.
# The module-level skip provides a clearer reason in the test report.
try:
    from tests.integration.conftest import ha_configured
except ImportError:
    ha_configured = False

pytestmark = pytest.mark.skipif(
    not ha_configured, reason="HA_URL and HA_TOKEN must be set"
)


class TestGraphTools:
    """Integration tests for the 7 graph exploration MCP tools."""

    # ---- graph_build_index ----

    def test_graph_build_index(self, real_mcp):
        """Build or refresh the HA Semantic Graph and verify response structure."""
        result = real_mcp.call_tool("graph_build_index", force=True)
        data = json.loads(result)

        assert data["success"] is True
        assert isinstance(data["nodes_count"], int)
        assert isinstance(data["edges_count"], int)
        assert "built_at" in data
        assert "stats" in data
        assert "node_types" in data["stats"]
        assert isinstance(data["stats"]["node_types"], dict)

    # ---- graph_find_references ----

    def test_graph_find_references(self, real_mcp):
        """Find semantic references to sun.sun and verify references list."""
        result = real_mcp.call_tool("graph_find_references", entity_id="sun.sun")
        data = json.loads(result)

        assert data["success"] is True
        assert data["entity_id"] == "sun.sun"
        assert "total_references" in data
        assert isinstance(data["total_references"], int)
        assert "references" in data
        assert isinstance(data["references"], list)
        # If there are references, verify they have expected keys
        for ref in data["references"]:
            assert "source" in ref
            assert "relation" in ref
            assert "confidence" in ref

    # ---- graph_entity_impact ----

    def test_graph_entity_impact(self, real_mcp):
        """Analyze impact of sun.sun and verify risk and direct_impact fields."""
        result = real_mcp.call_tool("graph_entity_impact", entity_id="sun.sun")
        data = json.loads(result)

        assert data["success"] is True
        assert "entity_id" in data
        assert "exists" in data
        assert "risk" in data
        assert data["risk"] in ("high", "medium", "low")
        assert "direct_impact" in data
        assert isinstance(data["direct_impact"], dict)
        # Verify impact categories exist
        assert "automations_triggered_by" in data["direct_impact"]
        assert "automations_reading" in data["direct_impact"]
        assert "automations_controlling" in data["direct_impact"]
        assert "scripts_controlling" in data["direct_impact"]
        assert "dashboards_displaying" in data["direct_impact"]
        assert "other" in data["direct_impact"]
        assert "edges" in data
        assert isinstance(data["edges"], list)

    # ---- graph_get_neighbors ----

    def test_graph_get_neighbors(self, real_mcp):
        """Get subgraph around entity:sun.sun and verify nodes/edges lists."""
        result = real_mcp.call_tool(
            "graph_get_neighbors",
            node_id="entity:sun.sun",
            depth=1,
            direction="both",
        )
        data = json.loads(result)

        assert data["success"] is True
        assert "node_id" in data
        assert "found" in data
        assert "depth" in data
        assert data["depth"] == 1
        assert "direction" in data
        assert data["direction"] == "both"
        assert "nodes" in data
        assert isinstance(data["nodes"], list)
        assert "edges" in data
        assert isinstance(data["edges"], list)

        # If nodes were found, verify structure
        for node in data["nodes"]:
            assert "id" in node
            assert "type" in node
            assert "name" in node

        # If edges exist, verify structure
        for edge in data["edges"]:
            assert "source" in edge
            assert "target" in edge
            assert "relation" in edge

    # ---- graph_detect_ghost_references ----

    def test_graph_detect_ghost_references(self, real_mcp):
        """Detect ghost entity references and verify ghost_entities list."""
        result = real_mcp.call_tool("graph_detect_ghost_references")
        data = json.loads(result)

        assert data["success"] is True
        assert "total_ghosts" in data
        assert isinstance(data["total_ghosts"], int)
        assert "ghost_entities" in data
        assert isinstance(data["ghost_entities"], list)

    # ---- graph_detect_orphans ----

    def test_graph_detect_orphans(self, real_mcp):
        """Detect orphan entities and verify orphan_entities list."""
        result = real_mcp.call_tool(
            "graph_detect_orphans", ignorable_domains="update,button"
        )
        data = json.loads(result)

        assert data["success"] is True
        assert "total_orphans" in data
        assert isinstance(data["total_orphans"], int)
        assert "orphan_entities" in data
        assert isinstance(data["orphan_entities"], list)

        # If orphans exist, verify structure
        for orphan in data["orphan_entities"]:
            assert "entity_id" in orphan
            assert "name" in orphan

    # ---- graph_export_mermaid ----

    def test_graph_export_mermaid(self, real_mcp):
        """Export subgraph as Mermaid and verify mermaid string."""
        result = real_mcp.call_tool(
            "graph_export_mermaid",
            node_id="entity:sun.sun",
            depth=2,
        )
        data = json.loads(result)

        assert data["success"] is True
        assert "mermaid" in data
        assert isinstance(data["mermaid"], str)
        assert data["mermaid"].startswith("graph TD")
