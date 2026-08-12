"""
Integration Tests — Composite tools against real Home Assistant.

These tests require a running HA instatece.
Skipped automatically when HA_URL / HA_TOKEN are not set.

Run:
    HA_URL=http://192.168.0.10:8123 HA_TOKEN=xxx \
        pytest tests/integration/test_composite_integration.py -v -s
"""

import json
import os
import time

import pytest

HA_URL = os.getenv("HA_URL", "")
HA_TOKEN = os.getenv("HA_TOKEN", "")
HA_CONFIG_PATH = os.getenv("HA_CONFIG_PATH", "/config")

_skip = not (HA_URL and HA_TOKEN)
pytestmark = pytest.mark.skipif(_skip, reason="HA_URL / HA_TOKEN not set")


@pytest.fixture(scope="module")
def mcp():
    """Register composite tools against real HA."""
    from fastmcp import FastMCP

    from tools.composite import register_composite_tools

    server = FastMCP("integration_test")
    register_composite_tools(server, HA_CONFIG_PATH, HA_URL, HA_TOKEN)
    return server


def _get_fn(mcp, name):
    tools = mcp._tool_manager._tools if hasattr(mcp, "_tool_manager") else {}
    tool = tools.get(name)
    if tool is None:
        pytest.skip(f"Tool {name} not available")
    return tool.fn if hasattr(tool, "fn") else tool


# ====================================================================
#  investigate_entity — real HA
# ====================================================================


class TestInvestigateEntityReal:
    @pytest.mark.asyncio
    async def test_returns_success(self, mcp):
        fn = _get_fn(mcp, "investigate_entity")
        raw = await fn(search_term="light")
        data = json.loads(raw)
        assert data["success"] is True
        assert data["summary"]["entities_found"] > 0

    @pytest.mark.asyncio
    async def test_csv_multiterm(self, mcp):
        fn = _get_fn(mcp, "investigate_entity")
        raw = await fn(search_term="light,sensor")
        data = json.loads(raw)
        assert data["success"] is True
        domains = {e.get("domain") for e in data["matched_entities"]}
        assert "light" in domains or "sensor" in domains

    @pytest.mark.asyncio
    async def test_output_size_under_budget(self, mcp):
        fn = _get_fn(mcp, "investigate_entity")
        t0 = time.time()
        raw = await fn(search_term="light")
        elapsed = time.time() - t0

        size_kb = len(raw) / 1024
        approx_tokens = len(raw) / 4

        print("\n  investigate_entity('light'):")
        print(f"    Size:   {size_kb:.1f} KB")
        print(f"    Tokens: ~{approx_tokens:.0f}")
        print(f"    Time:   {elapsed:.2f}s")

        # Should be under 50 KB / 12 500 tokens for any single query
        assert size_kb < 50, f"Output too large: {size_kb:.1f} KB"
        assert elapsed < 30, f"Too slow: {elapsed:.1f}s"


# ====================================================================
#  get_entity_with_automations — real HA
# ====================================================================


class TestGetEntityWithAutomationsReal:
    @pytest.mark.asyncio
    async def test_nonexistent_entity(self, mcp):
        fn = _get_fn(mcp, "get_entity_with_automations")
        raw = await fn(entity_id="light.definitely_does_not_exist_xyz")
        data = json.loads(raw)
        assert data["success"] is False
        assert "suggestions" in data or "error" in data


# ====================================================================
#  get_area_diagnostic — real HA
# ====================================================================


class TestGetAreaDiagnosticReal:
    @pytest.mark.asyncio
    async def test_nonexistent_area(self, mcp):
        area_name = "definitely_nonexistent_room_xyz"
        fn = _get_fn(mcp, "get_area_diagnostic")
        raw = await fn(area_name=area_name)
        data = json.loads(raw)
        assert data == {
            "success": False,
            "error": f"Area '{area_name}' not found",
        }

    @pytest.mark.asyncio
    async def test_area_output_has_warnings_field(self, mcp):
        from tools.composite import _load_registries

        _, _, areas = _load_registries(HA_CONFIG_PATH)
        if not areas:
            pytest.skip("Area registry is empty")
        area = areas[0]
        area_name = area.get("name") or area.get("id")
        assert area_name, "Area registry entry lacks both name and id"

        fn = _get_fn(mcp, "get_area_diagnostic")
        raw = await fn(area_name=area_name)
        data = json.loads(raw)
        assert data["success"] is True
        assert "warnings" in data
        assert isinstance(data["warnings"], list)


# ====================================================================
#  Cache performance — real HA
# ====================================================================


class TestCachePerformanceReal:
    def test_cache_hit_rate_after_warmup(self):
        from tools.utils import (
            get_registry_cache_stats,
            invalidate_registry_cache,
            load_registry,
        )

        invalidate_registry_cache()

        # Cold loads
        for name in (
            "core.entity_registry",
            "core.device_registry",
            "core.area_registry",
        ):
            load_registry(name, HA_CONFIG_PATH)

        stats_after_cold = get_registry_cache_stats()

        # Warm loads — should all hit cache
        for _ in range(5):
            for name in (
                "core.entity_registry",
                "core.device_registry",
                "core.area_registry",
            ):
                load_registry(name, HA_CONFIG_PATH)

        stats_after_warm = get_registry_cache_stats()
        new_hits = stats_after_warm["hits"] - stats_after_cold["hits"]
        assert new_hits >= 15
