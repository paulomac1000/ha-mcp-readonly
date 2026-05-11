"""
Tests for Lovelace / Dashboard tools.
Covers: get_lovelace_dashboards, get_lovelace_config, get_lovelace_resources,
search_lovelace_config, get_lovelace_config_summary, diagnose_lovelace_setup.
"""

import json
from unittest.mock import patch

import pytest

from tools.storage import register_storage_tools


@pytest.fixture
def config_path(tmp_path) -> str:
    return str(tmp_path)


@pytest.fixture
def mock_mcp():
    class MockMCP:
        def __init__(self):
            self._tools = {}

        def tool(self):
            def decorator(func):
                self._tools[func.__name__] = func
                return func

            return decorator

    return MockMCP()


# ── mock data ──────────────────────────────────────────────────────────────

MOCK_DASHBOARDS_ITEMS = [
    {
        "id": "dashboard_ulanska",
        "url_path": "dashboard-ulanska",
        "title": "Forteczna",
        "mode": "storage",
        "show_in_sidebar": True,
        "icon": "mdi:home",
    },
    {
        "id": "lovelace",
        "url_path": "lovelace",
        "title": "Dom",
        "mode": "storage",
        "show_in_sidebar": True,
        "icon": "mdi:home",
    },
    {
        "id": "wallpanel_downstairs",
        "url_path": "wallpanel-downstairs",
        "title": "Wallpanel",
        "mode": "storage",
        "show_in_sidebar": True,
        "icon": "mdi:tablet",
    },
    {
        "id": "map",
        "url_path": "map",
        "title": "Mapa",
        "mode": "storage",
        "show_in_sidebar": False,
        "icon": "mdi:map",
    },
]

MOCK_DASHBOARD_ULANSKA_CONFIG = {
    "version": 1,
    "minor_version": 1,
    "key": "lovelace.dashboard_ulanska",
    "data": {
        "config": {
            "title": "Dom",
            "views": [
                {
                    "title": "Living Room",
                    "path": "living-room-dash",
                    "cards": [
                        {
                            "type": "tile",
                            "entity": "light.living_room_main",
                            "icon": "mdi:lightbulb",
                        },
                        {
                            "type": "entities",
                            "entities": [
                                "sensor.temp_living_room",
                                {"entity": "sensor.humidity_living_room"},
                            ],
                        },
                        {
                            "type": "markdown",
                            "content": "## Welcome to living room",
                        },
                        {
                            "type": "custom:mushroom-light-card",
                            "entity": "light.kitchen_main",
                            "name": "Kitchen Main Light",
                        },
                    ],
                    "badges": [
                        {"entity": "sensor.outdoor_temp"},
                    ],
                },
                {
                    "title": "Bedroom",
                    "path": "bedroom-dash",
                    "cards": [
                        {
                            "type": "tile",
                            "entity": "light.bedroom_main",
                            "icon": "mdi:bed",
                        },
                        {
                            "type": "entities",
                            "entities": ["switch.radio"],
                        },
                    ],
                },
            ],
        }
    },
}

MOCK_DASHBOARD_LOVELACE_STRATEGY = {
    "version": 1,
    "minor_version": 1,
    "key": "lovelace.lovelace",
    "data": {
        "config": {
            "strategy": {"type": "original-states"},
        }
    },
}

MOCK_DASHBOARD_WALLPANEL_CONFIG = {
    "version": 1,
    "minor_version": 1,
    "key": "lovelace.wallpanel_downstairs",
    "data": {
        "config": {
            "title": "Wall",
            "views": [
                {
                    "title": "Panel",
                    "cards": [
                        {
                            "type": "tile",
                            "entity": "sensor.temp_living_room",
                        },
                        {
                            "type": "tile",
                            "entity": "light.living_room_main",
                        },
                    ],
                }
            ],
        }
    },
}

MOCK_RESOURCES = {
    "version": 1,
    "minor_version": 1,
    "key": "lovelace_resources",
    "data": {
        "items": [
            {
                "url": "/hacsfiles/button-card/button-card.js",
                "type": "module",
                "id": "r1",
            },
            {
                "url": "/hacsfiles/mushroom/mushroom.js",
                "type": "module",
                "id": "r2",
            },
            {
                "url": "/local/my-styles.css",
                "type": "css",
                "id": "r3",
            },
        ]
    },
}

MOCK_ENTITY_REGISTRY = {
    "data": {
        "entities": [
            {"entity_id": "light.living_room_main", "name": "Living Room Light", "platform": "hue"},
            {"entity_id": "light.kitchen_main", "name": "Kitchen Main Light", "platform": "hue"},
            {"entity_id": "light.bedroom_main", "name": "Bedroom Main Light", "platform": "hue"},
            {"entity_id": "sensor.temp_living_room", "name": "Temp", "platform": "mqtt"},
            {"entity_id": "sensor.humidity_living_room", "name": "Humidity", "platform": "mqtt"},
            {"entity_id": "sensor.outdoor_temp", "name": "Outdoor", "platform": "mqtt"},
            {"entity_id": "switch.radio", "name": "Radio", "platform": "mqtt"},
        ]
    },
}


def _make_mock_load(mock_data):
    """Build a side_effect for load_registry patches."""
    return lambda name, path, use_cache=True: mock_data.get(name, {})


# ════════════════════════════════════════════════════════════════════════════
# TESTS: get_lovelace_dashboards & get_lovelace_config
# ════════════════════════════════════════════════════════════════════════════


class TestLovelaceDashboards:
    @pytest.mark.asyncio
    async def test_get_lovelace_dashboards(self, mock_mcp, config_path):
        """Happy path — returns dashboard list from lovelace_dashboards."""
        mock_data = {"lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}}}
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_dashboards"]())
        assert data["total_dashboards"] == 4
        assert data["dashboards"][0]["id"] == "dashboard_ulanska"

    @pytest.mark.asyncio
    async def test_get_lovelace_dashboards_empty(self, mock_mcp, config_path):
        """No dashboards — returns empty list."""
        with patch("tools.storage.load_registry", return_value={}):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_dashboards"]())
        assert data["total_dashboards"] == 0
        assert data["dashboards"] == []

    @pytest.mark.asyncio
    async def test_get_lovelace_config_by_url_path(self, mock_mcp, config_path):
        """Resolve dashboard by url_path (e.g. 'dashboard-ulanska')."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_config"]("dashboard-ulanska"))
        assert data["key"] == "lovelace.dashboard_ulanska"

    @pytest.mark.asyncio
    async def test_get_lovelace_config_by_id(self, mock_mcp, config_path):
        """Resolve dashboard by id (e.g. 'dashboard_ulanska')."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_config"]("dashboard_ulanska"))
        assert data["key"] == "lovelace.dashboard_ulanska"

    @pytest.mark.asyncio
    async def test_get_lovelace_config_default(self, mock_mcp, config_path):
        """Default 'lovelace' resolves to the main dashboard (lovelace.lovelace)."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.lovelace": MOCK_DASHBOARD_LOVELACE_STRATEGY,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_config"]())
        assert data["key"] == "lovelace.lovelace"

    @pytest.mark.asyncio
    async def test_get_lovelace_config_not_found(self, mock_mcp, config_path):
        """Dashboard not in registry."""
        mock_data = {"lovelace_dashboards": {"data": {"items": []}}}
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_config"]("nonexistent"))
        assert "error" in data
        assert "not found" in data["error"].lower()


# ════════════════════════════════════════════════════════════════════════════
# TESTS: get_lovelace_resources
# ════════════════════════════════════════════════════════════════════════════


class TestLovelaceResources:
    @pytest.mark.asyncio
    async def test_get_lovelace_resources(self, mock_mcp, config_path):
        """Happy path — returns resources with type breakdown."""
        mock_data = {"lovelace_resources": MOCK_RESOURCES}
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_resources"]())
        assert data["success"] is True
        assert data["total_resources"] == 3
        assert data["by_type"]["module"] == 2
        assert data["by_type"]["css"] == 1

    @pytest.mark.asyncio
    async def test_get_lovelace_resources_empty(self, mock_mcp, config_path):
        """No resources file — returns empty list."""
        with patch("tools.storage.load_registry", return_value={}):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_resources"]())
        assert data["success"] is True
        assert data["total_resources"] == 0
        assert data["by_type"] == {}


# ════════════════════════════════════════════════════════════════════════════
# TESTS: search_lovelace_config
# ════════════════════════════════════════════════════════════════════════════


class TestSearchLovelaceConfig:
    @pytest.mark.asyncio
    async def test_search_by_entity(self, mock_mcp, config_path):
        """Find cards by entity_id."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
            "lovelace.lovelace": MOCK_DASHBOARD_LOVELACE_STRATEGY,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(
                await mock_mcp._tools["search_lovelace_config"](entity_id="light.living_room_main")
            )
        assert data["success"] is True
        assert data["matched_count"] >= 1
        match_ids = [m["card_type"] for m in data["matches"]]
        assert "tile" in match_ids

    @pytest.mark.asyncio
    async def test_search_by_card_type(self, mock_mcp, config_path):
        """Find cards by type."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["search_lovelace_config"](card_type="markdown"))
        assert data["success"] is True
        assert data["matched_count"] == 1
        assert data["matches"][0]["card_type"] == "markdown"

    @pytest.mark.asyncio
    async def test_search_by_search_term(self, mock_mcp, config_path):
        """Free-text search in card content."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(
                await mock_mcp._tools["search_lovelace_config"](search_term="welcome")
            )
        assert data["success"] is True
        assert data["matched_count"] == 1

    @pytest.mark.asyncio
    async def test_search_dashboard_filter(self, mock_mcp, config_path):
        """Limit search to a specific dashboard."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
            "lovelace.wallpanel_downstairs": MOCK_DASHBOARD_WALLPANEL_CONFIG,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(
                await mock_mcp._tools["search_lovelace_config"](
                    entity_id="light.living_room_main",
                    dashboard="wallpanel-downstairs",
                )
            )
        assert data["success"] is True
        assert data["matched_count"] == 1
        assert data["matches"][0]["dashboard"] == "wallpanel_downstairs"

    @pytest.mark.asyncio
    async def test_search_strategy_dashboard_warning(self, mock_mcp, config_path):
        """Strategy-based dashboards are skipped with a warning."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS[:2]}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
            "lovelace.lovelace": MOCK_DASHBOARD_LOVELACE_STRATEGY,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(
                await mock_mcp._tools["search_lovelace_config"](entity_id="light.living_room_main")
            )
        assert data["success"] is True
        assert any("strategy" in w.lower() for w in (data.get("warnings") or []))

    @pytest.mark.asyncio
    async def test_search_no_match(self, mock_mcp, config_path):
        """No results found."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(
                await mock_mcp._tools["search_lovelace_config"](entity_id="light.nonexistent")
            )
        assert data["success"] is True
        assert data["matched_count"] == 0
        assert data["matches"] == []

    @pytest.mark.asyncio
    async def test_search_dashboard_not_found(self, mock_mcp, config_path):
        """Requested dashboard doesn't exist."""
        mock_data = {"lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}}}
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(
                await mock_mcp._tools["search_lovelace_config"](dashboard="no-such-dashboard")
            )
        assert data["success"] is True
        assert data["matched_count"] == 0
        assert data["warnings"]


# ════════════════════════════════════════════════════════════════════════════
# TESTS: get_lovelace_config_summary
# ════════════════════════════════════════════════════════════════════════════


class TestLovelaceConfigSummary:
    @pytest.mark.asyncio
    async def test_summary_single_dashboard(self, mock_mcp, config_path):
        """Summary of a specific dashboard with cards."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(
                await mock_mcp._tools["get_lovelace_config_summary"]("dashboard-ulanska")
            )
        assert data["success"] is True
        assert data["dashboard"]["id"] == "dashboard_ulanska"
        assert data["dashboard"]["total_views"] == 2
        assert data["dashboard"]["total_cards"] == 6
        breakdown = data["dashboard"]["card_types_breakdown"]
        assert "tile" in breakdown

    @pytest.mark.asyncio
    async def test_summary_all_dashboards(self, mock_mcp, config_path):
        """Global summary of all dashboards."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
            "lovelace.lovelace": MOCK_DASHBOARD_LOVELACE_STRATEGY,
            "lovelace.wallpanel_downstairs": MOCK_DASHBOARD_WALLPANEL_CONFIG,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_config_summary"]())
        assert data["success"] is True
        assert data["total_dashboards"] == 4
        assert data["global_stats"]["total_cards"] > 0
        assert len(data["global_stats"]["strategy_dashboards"]) >= 1

    @pytest.mark.asyncio
    async def test_summary_strategy_dashboard(self, mock_mcp, config_path):
        """Strategy-based dashboard shows strategy type instead of cards."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.lovelace": MOCK_DASHBOARD_LOVELACE_STRATEGY,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_config_summary"]("lovelace"))
        assert data["success"] is True
        assert data["dashboard"]["strategy"] == "original-states"
        assert data["dashboard"]["total_cards"] == 0

    @pytest.mark.asyncio
    async def test_summary_not_found(self, mock_mcp, config_path):
        """Requested dashboard doesn't exist."""
        mock_data = {"lovelace_dashboards": {"data": {"items": []}}}
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_config_summary"]("nonexistent"))
        assert data["success"] is False
        assert "not found" in data["error"].lower()


# ════════════════════════════════════════════════════════════════════════════
# TESTS: diagnose_lovelace_setup
# ════════════════════════════════════════════════════════════════════════════


class TestDiagnoseLovelaceSetup:
    @pytest.mark.asyncio
    async def test_full_diagnostic(self, mock_mcp, config_path):
        """Full diagnostic with dashboards, resources, and entities."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
            "lovelace.lovelace": MOCK_DASHBOARD_LOVELACE_STRATEGY,
            "lovelace.wallpanel_downstairs": MOCK_DASHBOARD_WALLPANEL_CONFIG,
            "lovelace_resources": MOCK_RESOURCES,
            "core.entity_registry": MOCK_ENTITY_REGISTRY,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["diagnose_lovelace_setup"]())
        assert data["success"] is True
        assert len(data["dashboards"]) == 4
        assert data["resources"]["total"] == 3
        assert "strategy_dashboards" in data["health_checks"]

    @pytest.mark.asyncio
    async def test_missing_entity_detection(self, mock_mcp, config_path):
        """Detects entities referenced in dashboards but missing from registry."""
        dash_config = {
            "version": 1,
            "minor_version": 1,
            "key": "lovelace.test_dash",
            "data": {
                "config": {
                    "views": [
                        {
                            "cards": [
                                {"type": "tile", "entity": "light.missing_bulb"},
                                {"type": "tile", "entity": "light.living_room_main"},
                            ]
                        }
                    ]
                }
            },
        }
        mock_data = {
            "lovelace_dashboards": {
                "data": {
                    "items": [
                        {"id": "test_dash", "url_path": "test", "title": "Test", "mode": "storage"}
                    ]
                }
            },
            "lovelace.test_dash": dash_config,
            "lovelace_resources": {"data": {"items": []}},
            "core.entity_registry": MOCK_ENTITY_REGISTRY,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["diagnose_lovelace_setup"]())
        assert data["success"] is True
        missing = data["health_checks"]["missing_entity_references"]
        assert any(m["entity_id"] == "light.missing_bulb" for m in missing)

    @pytest.mark.asyncio
    async def test_strategy_detection(self, mock_mcp, config_path):
        """Strategy dashboards are detected and reported."""
        mock_data = {
            "lovelace_dashboards": {
                "data": {
                    "items": [
                        {
                            "id": "lovelace",
                            "url_path": "lovelace",
                            "title": "Dom",
                            "mode": "storage",
                        }
                    ]
                }
            },
            "lovelace.lovelace": MOCK_DASHBOARD_LOVELACE_STRATEGY,
            "lovelace_resources": {"data": {"items": []}},
            "core.entity_registry": {"data": {"entities": []}},
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["diagnose_lovelace_setup"]())
        assert data["success"] is True
        assert "lovelace" in data["health_checks"]["strategy_dashboards"]

    @pytest.mark.asyncio
    async def test_empty_setup(self, mock_mcp, config_path):
        """No dashboards at all — graceful response."""
        with patch("tools.storage.load_registry", return_value={}):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["diagnose_lovelace_setup"]())
        assert data["success"] is True
        assert data["dashboards"] == []
        assert data["resources"]["total"] == 0


# ════════════════════════════════════════════════════════════════════════════
# TESTS: Edge cases
# ════════════════════════════════════════════════════════════════════════════


class TestLovelaceEdgeCases:
    @pytest.mark.asyncio
    async def test_dashboards_with_badges(self, mock_mcp, config_path):
        """Dashboard with badges — badges included in search and cards list."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(
                await mock_mcp._tools["search_lovelace_config"](entity_id="sensor.outdoor_temp")
            )
        assert data["success"] is True
        badge_matches = [m for m in data["matches"] if m.get("is_badge")]
        assert len(badge_matches) >= 1

    @pytest.mark.asyncio
    async def test_search_multiple_criteria(self, mock_mcp, config_path):
        """Multiple criteria — both are reflected in matched_by when they match."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(
                await mock_mcp._tools["search_lovelace_config"](
                    entity_id="light.living_room_main", card_type="tile"
                )
            )
        assert data["success"] is True
        assert data["matched_count"] >= 1
        match_with_both = [
            m
            for m in data["matches"]
            if "entity_id" in m["matched_by"] and "card_type" in m["matched_by"]
        ]
        assert len(match_with_both) >= 1, (
            f"Expected at least one match with both entity_id and card_type, "
            f"got matches: {data['matches']}"
        )

    @pytest.mark.asyncio
    async def test_max_results_limit(self, mock_mcp, config_path):
        """max_results parameter limits output."""
        mock_data = {
            "lovelace_dashboards": {"data": {"items": MOCK_DASHBOARDS_ITEMS}},
            "lovelace.dashboard_ulanska": MOCK_DASHBOARD_ULANSKA_CONFIG,
            "lovelace.wallpanel_downstairs": MOCK_DASHBOARD_WALLPANEL_CONFIG,
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(
                await mock_mcp._tools["search_lovelace_config"](
                    entity_id="light.living_room_main", max_results=1
                )
            )
        assert data["matched_count"] <= 1

    @pytest.mark.asyncio
    async def test_yaml_mode_detection_in_summary(self, mock_mcp, config_path):
        """YAML mode dashboard shows mode=yaml."""
        yaml_dash = {
            "id": "yaml_dash",
            "url_path": "yaml-panel",
            "title": "YAML Panel",
            "mode": "yaml",
            "show_in_sidebar": True,
            "icon": "mdi:file",
        }
        mock_data = {
            "lovelace_dashboards": {"data": {"items": [yaml_dash]}},
        }
        with patch("tools.storage.load_registry", side_effect=_make_mock_load(mock_data)):
            register_storage_tools(mock_mcp, config_path)
            data = json.loads(await mock_mcp._tools["get_lovelace_config_summary"]())
        assert data["success"] is True
        assert "yaml_dash" in data["global_stats"]["yaml_mode_dashboards"]
