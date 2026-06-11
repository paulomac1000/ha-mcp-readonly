"""HA Semantic Graph — async-safe TTL cache."""

import asyncio
import time

from ha_graph.models import GraphIndex
from ha_graph.scanner import build_graph_index as _build_graph_index

_GRAPH_CACHE: GraphIndex | None = None
_GRAPH_CACHE_TS: float = 0
_GRAPH_LOCK = asyncio.Lock()
GRAPH_CACHE_TTL = 300  # seconds


async def get_graph_index(
    config_path: str,
    ha_url: str | None = None,
    ha_token: str | None = None,
    force: bool = False,
) -> GraphIndex:
    """Get the graph index, using cache if available and not forced."""
    global _GRAPH_CACHE, _GRAPH_CACHE_TS

    now = time.time()
    async with _GRAPH_LOCK:
        if not force and _GRAPH_CACHE is not None and now - _GRAPH_CACHE_TS < GRAPH_CACHE_TTL:
            return _GRAPH_CACHE

        _GRAPH_CACHE = _build_graph_index(config_path, ha_url, ha_token)
        _GRAPH_CACHE_TS = now
        return _GRAPH_CACHE


def build_graph_index(
    config_path: str,
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> GraphIndex:
    """Build the graph index without cache (always fresh)."""
    return _build_graph_index(config_path, ha_url, ha_token)
