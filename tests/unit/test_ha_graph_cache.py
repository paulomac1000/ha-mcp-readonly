"""
Tests for ha_graph/cache.py — async-safe TTL cache for GraphIndex.

Each test in TestGetGraphIndex is isolated by resetting the module-level
cache globals before every test via an autouse fixture.
"""

import time
from unittest.mock import patch

import pytest

from ha_graph.cache import GRAPH_CACHE_TTL, build_graph_index, get_graph_index
from ha_graph.models import GraphIndex

TEST_CONFIG_PATH = "/tmp/test_ha_config"
TEST_HA_URL = "http://localhost:8123"
TEST_HA_TOKEN = "test_token"


class TestBuildGraphIndexModule:
    """Test the module-level build_graph_index (always fresh)."""

    def test_returns_graph_index(self):
        """build_graph_index returns a GraphIndex even with missing config."""
        index = build_graph_index(TEST_CONFIG_PATH)
        assert isinstance(index, GraphIndex)

    def test_calls_blocking_scanner(self):
        """build_graph_index delegates to the blocking scanner."""
        with patch("ha_graph.cache._build_graph_index") as mock_scanner:
            mock_scanner.return_value = GraphIndex()
            result = build_graph_index(TEST_CONFIG_PATH)
            assert isinstance(result, GraphIndex)
            mock_scanner.assert_called_once_with(TEST_CONFIG_PATH, None, None)


class TestGetGraphIndex:
    """Test the async get_graph_index with caching."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset module-level cache globals before each test."""
        import ha_graph.cache as cache_mod

        cache_mod._GRAPH_CACHE = None
        cache_mod._GRAPH_CACHE_TS = 0

    @pytest.mark.asyncio
    async def test_first_call_creates_graph(self):
        """First call to get_graph_index builds the graph."""
        with patch("ha_graph.cache._build_graph_index") as mock_scanner:
            mock_scanner.return_value = GraphIndex()
            result = await get_graph_index(TEST_CONFIG_PATH)
            assert isinstance(result, GraphIndex)
            mock_scanner.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self):
        """Second call (within TTL) uses cached graph."""
        with patch("ha_graph.cache._build_graph_index") as mock_scanner:
            mock_scanner.return_value = GraphIndex()

            await get_graph_index(TEST_CONFIG_PATH)
            assert mock_scanner.call_count == 1

            await get_graph_index(TEST_CONFIG_PATH)
            assert mock_scanner.call_count == 1

    @pytest.mark.asyncio
    async def test_force_rebuild(self):
        """force=True bypasses cache and rebuilds."""
        with patch("ha_graph.cache._build_graph_index") as mock_scanner:
            mock_scanner.return_value = GraphIndex()

            await get_graph_index(TEST_CONFIG_PATH)
            assert mock_scanner.call_count == 1

            await get_graph_index(TEST_CONFIG_PATH, force=True)
            assert mock_scanner.call_count == 2

    @pytest.mark.asyncio
    async def test_ttl_expiry_triggers_rebuild(self):
        """After TTL expires, next call rebuilds."""
        import ha_graph.cache as cache_mod

        original_ttl = cache_mod.GRAPH_CACHE_TTL
        cache_mod.GRAPH_CACHE_TTL = 0  # Expire immediately
        try:
            with patch("ha_graph.cache._build_graph_index") as mock_scanner:
                mock_scanner.return_value = GraphIndex()

                await get_graph_index(TEST_CONFIG_PATH)
                assert mock_scanner.call_count == 1

                time.sleep(0.01)

                await get_graph_index(TEST_CONFIG_PATH)
                assert mock_scanner.call_count == 2
        finally:
            cache_mod.GRAPH_CACHE_TTL = original_ttl

    @pytest.mark.asyncio
    async def test_concurrent_access_safe(self):
        """Concurrent calls do not cause race conditions."""
        with patch("ha_graph.cache._build_graph_index") as mock_scanner:
            mock_scanner.return_value = GraphIndex()

            import asyncio

            results = await asyncio.gather(
                get_graph_index(TEST_CONFIG_PATH),
                get_graph_index(TEST_CONFIG_PATH),
            )

            assert len(results) == 2
            assert all(isinstance(r, GraphIndex) for r in results)
            assert mock_scanner.call_count == 1

    @pytest.mark.asyncio
    async def test_passes_ha_params(self):
        """HA url/token are forwarded to the scanner."""
        with patch("ha_graph.cache._build_graph_index") as mock_scanner:
            mock_scanner.return_value = GraphIndex()
            await get_graph_index(TEST_CONFIG_PATH, TEST_HA_URL, TEST_HA_TOKEN)
            mock_scanner.assert_called_once_with(TEST_CONFIG_PATH, TEST_HA_URL, TEST_HA_TOKEN)

    def test_graph_cache_ttl_constant(self):
        """GRAPH_CACHE_TTL is 300 seconds."""
        assert GRAPH_CACHE_TTL == 300
