"""
Tests for tools/history.py
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from tools.history import register_history_tools


class TestGetEntityStateHistorySummary:
    """Tests for get_entity_state_history_summary()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, ha_url, ha_token):
        self.mock_mcp = mock_mcp
        self.ha_url = ha_url
        self.ha_token = ha_token

        # Sample history data: 3 changes over 3 hours
        now = datetime.now(UTC)
        self.sample_history = [
            [
                {
                    "entity_id": "switch.test",
                    "state": "off",
                    "last_changed": (now - timedelta(hours=3)).isoformat(),
                },
                {
                    "entity_id": "switch.test",
                    "state": "on",
                    "last_changed": (now - timedelta(hours=2)).isoformat(),
                },
                {
                    "entity_id": "switch.test",
                    "state": "off",
                    "last_changed": (now - timedelta(hours=1)).isoformat(),
                },
            ]
        ]

    @pytest.mark.asyncio
    async def test_history_summary(self):
        """Test history summary calculation."""
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": self.sample_history}

            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)

            result = await self.mock_mcp._tools["get_entity_state_history_summary"](
                "switch.test", 24
            )

        data = json.loads(result)

        assert data["success"] is True
        assert data["entity_id"] == "switch.test"
        assert data["total_changes"] == 3

        # Check breakdown
        breakdown = data["states_breakdown"]
        assert "on" in breakdown
        assert "off" in breakdown
        assert breakdown["on"]["count"] == 1
        assert breakdown["off"]["count"] == 2

        # Check anomalies
        assert len(data["anomalies"]) == 0

    @pytest.mark.asyncio
    async def test_rapid_cycling_anomaly(self):
        """Test detection of rapid cycling."""
        now = datetime.now(UTC)
        rapid_history = [
            [
                {
                    "state": "on",
                    "last_changed": (now - timedelta(minutes=5)).isoformat(),
                },
                {
                    "state": "off",
                    "last_changed": (now - timedelta(minutes=4, seconds=50)).isoformat(),
                },
                {
                    "state": "on",
                    "last_changed": (now - timedelta(minutes=4, seconds=40)).isoformat(),
                },
                {
                    "state": "off",
                    "last_changed": (now - timedelta(minutes=4, seconds=30)).isoformat(),
                },
            ]
        ]

        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": rapid_history}

            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)

            result = await self.mock_mcp._tools["get_entity_state_history_summary"](
                "switch.test", 24
            )

        data = json.loads(result)
        assert len(data["anomalies"]) > 0
        assert "Rapid cycling" in data["anomalies"][0]

    @pytest.mark.asyncio
    async def test_history_api_error(self):
        """API failure → success: False."""
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": False, "error": "timeout"}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_entity_state_history_summary"](
                "switch.test", 24
            )
        data = json.loads(result)
        assert data["success"] is False
        assert "error" in data

    @pytest.mark.asyncio
    async def test_history_empty(self):
        """Empty history data → 'No history found' message."""
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": [[]]}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_entity_state_history_summary"](
                "switch.test", 24
            )
        data = json.loads(result)
        assert data["success"] is True
        assert "No history" in data.get("message", "")

    @pytest.mark.asyncio
    async def test_unavailable_states_filtered(self):
        """'unavailable' and 'unknown' states must not appear in states_breakdown."""
        now = datetime.now(UTC)
        history_with_noise = [
            [
                {
                    "state": "unavailable",
                    "last_changed": (now - timedelta(hours=3)).isoformat(),
                },
                {"state": "on", "last_changed": (now - timedelta(hours=2)).isoformat()},
                {
                    "state": "unknown",
                    "last_changed": (now - timedelta(hours=1)).isoformat(),
                },
            ]
        ]
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": history_with_noise}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_entity_state_history_summary"](
                "switch.test", 24
            )
        data = json.loads(result)
        assert data["success"] is True
        assert "unavailable" not in data["states_breakdown"]
        assert "unknown" not in data["states_breakdown"]
        assert "on" in data["states_breakdown"]

    @pytest.mark.asyncio
    async def test_detail_level_full_default_backward_compat(self):
        """detail_level='full' (default) uses minimal_response=false in the API call."""
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": [[]]}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            await self.mock_mcp._tools["get_entity_state_history_summary"](
                "switch.test", 24
            )

        called_url = mock_request.call_args[0][2]
        assert "minimal_response=false" in called_url

    @pytest.mark.asyncio
    async def test_detail_level_summary_uses_minimal_response(self):
        """detail_level='summary' uses minimal_response=true in the API call."""
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": [[]]}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            await self.mock_mcp._tools["get_entity_state_history_summary"](
                "switch.test", 24, None, "summary"
            )

        called_url = mock_request.call_args[0][2]
        assert "minimal_response=true" in called_url

    @pytest.mark.asyncio
    async def test_detail_level_invalid_returns_error(self):
        """Invalid detail_level returns error response."""
        register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
        result = await self.mock_mcp._tools["get_entity_state_history_summary"](
            "switch.test", 24, None, "minimal"
        )
        data = json.loads(result)
        assert data["success"] is False
        assert "Invalid detail_level" in data.get("error", "")


class TestGetRecentStateChanges:
    """Tests for get_recent_state_changes()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, ha_url, ha_token):
        self.mock_mcp = mock_mcp
        self.ha_url = ha_url
        self.ha_token = ha_token

        now = datetime.now(UTC)
        self.sample_history = [
            # Entity 1 history
            [
                {
                    "entity_id": "switch.one",
                    "state": "on",
                    "last_changed": (now - timedelta(minutes=5)).isoformat(),
                },
                {
                    "entity_id": "switch.one",
                    "state": "off",
                    "last_changed": (now - timedelta(minutes=2)).isoformat(),
                },
            ],
            # Entity 2 history
            [
                {
                    "entity_id": "sensor.temp",
                    "state": "20",
                    "last_changed": (now - timedelta(minutes=8)).isoformat(),
                },
                {
                    "entity_id": "sensor.temp",
                    "state": "21",
                    "last_changed": (now - timedelta(minutes=1)).isoformat(),
                },
            ],
        ]

    @pytest.mark.asyncio
    async def test_recent_changes(self):
        """Test getting recent changes."""
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": self.sample_history}

            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)

            result = await self.mock_mcp._tools["get_recent_state_changes"](10)

        data = json.loads(result)

        assert data["success"] is True
        assert data["period_minutes"] == 10
        # 4 events total
        assert data["total_changes"] == 4

        # Check sorting (newest first)
        changes = data["changes"]
        assert changes[0]["entity_id"] == "sensor.temp"  # 1 min ago
        assert changes[1]["entity_id"] == "switch.one"  # 2 min ago

    @pytest.mark.asyncio
    async def test_recent_changes_filter_domain(self):
        """Test filtering recent changes by domain."""
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": self.sample_history}

            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)

            result = await self.mock_mcp._tools["get_recent_state_changes"](10, domains="switch")

        data = json.loads(result)

        assert data["success"] is True
        # Only switch.one events should remain
        assert data["total_changes"] == 2
        for change in data["changes"]:
            assert change["entity_id"].startswith("switch.")

    @pytest.mark.asyncio
    async def test_recent_changes_api_error(self):
        """API failure → success: False."""
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": False, "error": "timeout"}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_recent_state_changes"](10)
        data = json.loads(result)
        assert data["success"] is False
        assert "error" in data

    @pytest.mark.asyncio
    async def test_recent_changes_truncation(self):
        """More than 50 changes → truncated to 50 and 'note' field present."""
        now = datetime.now(UTC)
        # Build 60 change events for one entity
        many_history = [
            [
                {
                    "entity_id": "switch.flood",
                    "state": "on" if i % 2 == 0 else "off",
                    "last_changed": (now - timedelta(seconds=i * 10)).isoformat(),
                }
                for i in range(60)
            ]
        ]
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": many_history}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_recent_state_changes"](60)
        data = json.loads(result)
        assert data["success"] is True
        assert len(data["changes"]) == 50
        assert "note" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
