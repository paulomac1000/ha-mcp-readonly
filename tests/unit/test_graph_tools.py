"""
Tests for tools/graph_tools.py — MCP tools wrapping ha_graph functions.

Tests the 7 tools defined by T8:
  graph_build_index, graph_find_references, graph_entity_impact,
  graph_get_neighbors, graph_detect_ghost_references,
  graph_detect_orphans, graph_export_mermaid.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from ha_graph.models import GraphEdge, GraphIndex, GraphNode


@pytest.fixture
def config_path(tmp_path):
    """Create minimal HA config for graph tools."""
    c = tmp_path / "config"
    c.mkdir(parents=True, exist_ok=True)
    (c / "automations.yaml").write_text("[]", encoding="utf-8")
    (c / "scripts.yaml").write_text("[]", encoding="utf-8")
    (c / "scenes.yaml").write_text("[]", encoding="utf-8")
    storage = c / ".storage"
    storage.mkdir(exist_ok=True)
    (storage / "core.entity_registry").write_text('{"data": {"entities": []}}')
    (storage / "core.device_registry").write_text('{"data": {"devices": []}}')
    (storage / "core.area_registry").write_text('{"data": {"areas": []}}')
    (storage / "core.config_entries").write_text('{"data": {"entries": []}}')
    return str(c)


@pytest.fixture
def ha_url():
    return "http://test-ha:8123"


@pytest.fixture
def ha_token():
    return "test-token"


@pytest.fixture
def mock_mcp():
    """Mock MCP server instance matching project patterns."""
    class MockMCP:
        def __init__(self):
            self._tools = {}

        def tool(self):
            def decorator(func):
                self._tools[func.__name__] = func
                return func
            return decorator

    return MockMCP()


@pytest.fixture
def sample_graph_index():
    """Build a reusable sample GraphIndex for testing."""
    g = GraphIndex()
    g.add_node(GraphNode(id="entity:sensor.temp", type="entity", name="Temperature Sensor"))
    g.add_node(GraphNode(id="entity:light.hallway", type="entity", name="Hallway Light"))
    g.add_node(GraphNode(id="automation:auto_001", type="automation", name="Motion Light"))
    g.add_edge(GraphEdge(source="automation:auto_001", target="entity:sensor.temp", relation="triggers_on"))
    g.add_edge(GraphEdge(source="automation:auto_001", target="entity:light.hallway", relation="controls"))
    return g


def _call_async(tool_fn, *args, **kwargs):
    """Helper: call an async tool function synchronously."""
    return asyncio.run(tool_fn(*args, **kwargs))


# ============================================================================
# graph_build_index
# ============================================================================

class TestGraphBuildIndex:
    """Test the graph_build_index tool."""

    def test_build_index_success(self, mock_mcp, config_path, ha_url, ha_token):
        """graph_build_index returns graph summary with nodes_count and edges_count."""
        from tools.graph_tools import register_graph_tools

        mock_index = GraphIndex()
        mock_index.add_node(GraphNode(id="entity:test", type="entity", name="Test"))
        mock_get = AsyncMock(return_value=mock_index)

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(mock_mcp._tools["graph_build_index"]))

        assert result["success"] is True
        assert result["nodes_count"] == 1
        assert result["edges_count"] == 0

    def test_build_index_with_force(self, mock_mcp, config_path, ha_url, ha_token):
        """graph_build_index with force=True passes force to get_graph_index."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(return_value=GraphIndex())

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            _call_async(mock_mcp._tools["graph_build_index"], force=True)

        mock_get.assert_called_once_with(config_path, ha_url, ha_token, force=True)

    def test_build_index_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        """Exception during get_graph_index returns success=False."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(side_effect=RuntimeError("build failed"))

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(mock_mcp._tools["graph_build_index"]))

        assert result["success"] is False
        assert "build failed" in result["error"]


# ============================================================================
# graph_find_references
# ============================================================================

class TestGraphFindReferences:
    """Test the graph_find_references tool."""

    def test_find_references_success(self, mock_mcp, config_path, ha_url, ha_token, sample_graph_index):
        """graph_find_references returns incoming references for an entity."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(return_value=sample_graph_index)

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(
                mock_mcp._tools["graph_find_references"],
                entity_id="sensor.temp",
            ))

        assert result["success"] is True
        assert result["entity_id"] == "sensor.temp"
        assert result["total_references"] >= 1

    def test_find_references_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        """Exception during entity lookup returns success=False."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(side_effect=RuntimeError("query error"))

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(
                mock_mcp._tools["graph_find_references"],
                entity_id="sensor.temp",
            ))

        assert result["success"] is False
        assert "query error" in result["error"]


# ============================================================================
# graph_entity_impact
# ============================================================================

class TestGraphEntityImpact:
    """Test the graph_entity_impact tool."""

    def test_entity_impact_success(self, mock_mcp, config_path, ha_url, ha_token, sample_graph_index):
        """graph_entity_impact returns impact analysis with risk level."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(return_value=sample_graph_index)

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(
                mock_mcp._tools["graph_entity_impact"],
                entity_id="sensor.temp",
            ))

        assert result["success"] is True
        assert "risk" in result
        assert "entity_id" in result

    def test_entity_impact_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        """Exception during impact analysis returns success=False."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(side_effect=RuntimeError("impact error"))

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(
                mock_mcp._tools["graph_entity_impact"],
                entity_id="sensor.temp",
            ))

        assert result["success"] is False
        assert "impact error" in result["error"]


# ============================================================================
# graph_get_neighbors
# ============================================================================

class TestGraphGetNeighbors:
    """Test the graph_get_neighbors tool."""

    def test_get_neighbors_success(self, mock_mcp, config_path, ha_url, ha_token, sample_graph_index):
        """graph_get_neighbors returns subgraph nodes and edges."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(return_value=sample_graph_index)

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(
                mock_mcp._tools["graph_get_neighbors"],
                node_id="entity:sensor.temp",
                depth=1,
                direction="both",
            ))

        assert result["success"] is True
        assert result.get("found") is True
        assert len(result.get("nodes", [])) >= 1

    def test_get_neighbors_invalid_direction(self, mock_mcp, config_path, ha_url, ha_token, sample_graph_index):
        """Invalid direction returns error."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(return_value=sample_graph_index)

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(
                mock_mcp._tools["graph_get_neighbors"],
                node_id="entity:sensor.temp",
                direction="sideways",
            ))

        assert result["success"] is False
        assert "direction" in result["error"]

    def test_get_neighbors_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        """Exception during neighbor lookup returns success=False."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(side_effect=RuntimeError("neighbor error"))

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(
                mock_mcp._tools["graph_get_neighbors"],
                node_id="entity:sensor.temp",
            ))

        assert result["success"] is False
        assert "neighbor error" in result["error"]


# ============================================================================
# graph_detect_ghost_references
# ============================================================================

class TestGraphDetectGhostReferences:
    """Test the graph_detect_ghost_references tool."""

    def test_detect_ghosts_success(self, mock_mcp, config_path, ha_url, ha_token, sample_graph_index):
        """graph_detect_ghost_references returns ghost entity list."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(return_value=sample_graph_index)

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(mock_mcp._tools["graph_detect_ghost_references"]))

        assert result["success"] is True
        assert "total_ghosts" in result
        assert "ghost_entities" in result

    def test_detect_ghosts_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        """Exception during ghost detection returns success=False."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(side_effect=RuntimeError("ghost error"))

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(mock_mcp._tools["graph_detect_ghost_references"]))

        assert result["success"] is False
        assert "ghost error" in result["error"]


# ============================================================================
# graph_detect_orphans
# ============================================================================

class TestGraphDetectOrphans:
    """Test the graph_detect_orphans tool."""

    def test_detect_orphans_success(self, mock_mcp, config_path, ha_url, ha_token, sample_graph_index):
        """graph_detect_orphans returns orphan entity list."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(return_value=sample_graph_index)

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(mock_mcp._tools["graph_detect_orphans"]))

        assert result["success"] is True
        assert "total_orphans" in result
        assert "orphan_entities" in result

    def test_detect_orphans_with_domains(self, mock_mcp, config_path, ha_url, ha_token, sample_graph_index):
        """graph_detect_orphans with ignorable_domains parses comma-separated string."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(return_value=sample_graph_index)

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(
                mock_mcp._tools["graph_detect_orphans"],
                ignorable_domains="sun,update",
            ))

        assert result["success"] is True

    def test_detect_orphans_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        """Exception during orphan detection returns success=False."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(side_effect=RuntimeError("orphan error"))

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(mock_mcp._tools["graph_detect_orphans"]))

        assert result["success"] is False
        assert "orphan error" in result["error"]


# ============================================================================
# graph_export_mermaid
# ============================================================================

class TestGraphExportMermaid:
    """Test the graph_export_mermaid tool."""

    def test_export_mermaid_success(self, mock_mcp, config_path, ha_url, ha_token, sample_graph_index):
        """graph_export_mermaid returns Mermaid diagram for a subgraph."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(return_value=sample_graph_index)

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(
                mock_mcp._tools["graph_export_mermaid"],
                node_id="entity:sensor.temp",
                depth=1,
            ))

        assert result["success"] is True
        assert "mermaid" in result
        assert "graph TD" in result["mermaid"]

    def test_export_mermaid_node_not_found(self, mock_mcp, config_path, ha_url, ha_token, sample_graph_index):
        """Export with nonexistent node returns Mermaid error note (still success=True)."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(return_value=sample_graph_index)

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(
                mock_mcp._tools["graph_export_mermaid"],
                node_id="entity:nonexistent",
            ))

        # export_mermaid returns "Node not found" as a valid Mermaid string, not an error
        assert result["success"] is True
        assert "not found" in result["mermaid"]

    def test_export_mermaid_exception_handler(self, mock_mcp, config_path, ha_url, ha_token):
        """Exception during Mermaid export returns success=False."""
        from tools.graph_tools import register_graph_tools

        mock_get = AsyncMock(side_effect=RuntimeError("export failed"))

        with patch("tools.graph_tools.get_graph_index", mock_get):
            register_graph_tools(mock_mcp, config_path, ha_url, ha_token)
            result = json.loads(_call_async(
                mock_mcp._tools["graph_export_mermaid"],
                node_id="entity:test",
            ))

        assert result["success"] is False
        assert "export failed" in result["error"]
