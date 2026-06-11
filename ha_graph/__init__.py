"""HA Semantic Graph — public API for building and querying Home Assistant dependency graphs."""

from ha_graph.cache import GRAPH_CACHE_TTL, get_graph_index
from ha_graph.extractors import (
    extract_controlled_entities,
    extract_entities_from_data,
    extract_entities_from_template,
    extract_services,
    extract_trigger_info,
)
from ha_graph.models import (
    Confidence,
    GraphEdge,
    GraphError,
    GraphIndex,
    GraphNode,
    NodeType,
    RelationType,
)
from ha_graph.scanner import HomeAssistantGraphScanner, build_graph_index

__all__ = [
    # Models
    "Confidence",
    "GraphEdge",
    "GraphError",
    "GraphIndex",
    "GraphNode",
    "NodeType",
    "RelationType",
    # Extractors
    "extract_controlled_entities",
    "extract_entities_from_data",
    "extract_entities_from_template",
    "extract_services",
    "extract_trigger_info",
    # Scanner
    "HomeAssistantGraphScanner",
    "build_graph_index",
    # Cache (T5)
    "get_graph_index",
    "GRAPH_CACHE_TTL",
]
