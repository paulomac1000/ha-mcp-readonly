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
    def history_setup(self, mock_mcp, ha_url, ha_token):
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
        assert data["recorder_coverage"]["status"] == "UNVERIFIED"

    @pytest.mark.asyncio
    async def test_unavailable_states_retained(self):
        """Recorder failures must remain visible, with explicit unverified coverage."""
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
        assert "unavailable" in data["states_breakdown"]
        assert "unknown" in data["states_breakdown"]
        assert "on" in data["states_breakdown"]
        assert data["total_changes"] == 3
        assert data["recorder_coverage"]["status"] == "UNVERIFIED"
        assert data["recorder_coverage"]["unknown_unavailable_retained"] is True

    @pytest.mark.asyncio
    async def test_hour_grouping_keeps_legacy_keys_and_exposes_utc(self):
        from datetime import timezone

        sample = (datetime.now(UTC) - timedelta(days=1)).replace(hour=12, minute=5)
        source_time = sample.astimezone(timezone(timedelta(hours=2)))
        rows = [[{"state": "21", "last_changed": source_time.isoformat()}]]
        with patch("tools.history.make_ha_request", return_value={"success": True, "data": rows}):
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_entity_state_history_summary"](
                "sensor.temperature", 48, "hour"
            )

        data = json.loads(result)
        day = sample.date().isoformat()
        assert data["grouped"][f"{day}T14:00"]["avg"] == 21.0
        assert data["grouped_utc"][f"{day}T12:00Z"]["avg"] == 21.0

    @pytest.mark.asyncio
    async def test_out_of_order_duplicates_are_normalized_before_duration_math(self):
        now = datetime.now(UTC).replace(microsecond=0)
        rows = [
            [
                {"state": "on", "last_changed": (now - timedelta(minutes=2)).isoformat()},
                {"state": "off", "last_changed": (now - timedelta(minutes=5)).isoformat()},
                {"state": "off", "last_changed": (now - timedelta(minutes=5)).isoformat()},
            ]
        ]
        with patch("tools.history.make_ha_request", return_value={"success": True, "data": rows}):
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_entity_state_history_summary"](
                "switch.test", 1
            )

        data = json.loads(result)
        assert data["total_changes"] == 2
        assert data["states_breakdown"]["off"]["total_duration_min"] == 3.0
        assert data["states_breakdown"]["on"]["total_duration_min"] == 2.0
        assert data["recorder_coverage"]["duplicate_rows"] == 1
        assert (
            data["recorder_coverage"]["observed_start_utc"]
            == (now - timedelta(minutes=5)).isoformat()
        )

    @pytest.mark.asyncio
    async def test_missing_state_is_not_observed_unknown(self):
        now = datetime.now(UTC)
        rows = [
            {"state": "unknown", "last_changed": (now - timedelta(minutes=10)).isoformat()},
            {"last_changed": (now - timedelta(minutes=5)).isoformat()},
        ]
        with patch("tools.history.make_ha_request", return_value={"success": True, "data": [rows]}):
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_entity_state_history_summary"](
                "switch.test", 1
            )

        data = json.loads(result)
        assert data["states_breakdown"]["unknown"]["count"] == 1
        assert data["states_breakdown"]["unknown"]["total_duration_min"] == 5.0
        assert data["recorder_coverage"]["missing_state_rows"] == 1
        assert data["current_state_duration_min"] == 0

    @pytest.mark.asyncio
    async def test_missing_timestamp_never_proves_completeness(self):
        now = datetime.now(UTC)
        rows = [
            [
                {"state": "on", "last_changed": (now - timedelta(minutes=5)).isoformat()},
                {"state": "unavailable", "last_changed": "not-a-date"},
            ]
        ]
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": rows}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_entity_state_history_summary"](
                "switch.test", 24
            )
        data = json.loads(result)
        assert data["success"] is True
        assert data["recorder_coverage"]["invalid_timestamps"] == 1
        assert data["recorder_coverage"]["status"] == "UNVERIFIED"

    @pytest.mark.asyncio
    async def test_pre_window_duration_is_clamped(self):
        now = datetime.now(UTC)
        rows = [
            [
                {"state": "on", "last_changed": (now - timedelta(hours=48)).isoformat()},
                {"state": "off", "last_changed": (now - timedelta(hours=1)).isoformat()},
            ]
        ]
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": rows}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_entity_state_history_summary"](
                "switch.test", 24
            )
        data = json.loads(result)
        assert data["success"] is True
        assert 22.9 * 60 <= data["states_breakdown"]["on"]["total_duration_min"] <= 23.1 * 60
        assert data["recorder_coverage"]["status"] == "UNVERIFIED"

    @pytest.mark.asyncio
    async def test_high_cardinality_state_breakdown_is_bounded(self):
        now = datetime.now(UTC)
        rows = [
            [
                {"state": str(i), "last_changed": (now - timedelta(minutes=80 - i)).isoformat()}
                for i in range(80)
            ]
        ]
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": rows}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_entity_state_history_summary"](
                "sensor.test", 24
            )
        data = json.loads(result)
        assert data["success"] is True
        assert len(data["states_breakdown"]) == 32
        assert data["recorder_coverage"]["state_breakdown_truncated"] is True
        assert data["recorder_coverage"]["omitted_state_labels"] == 48

    @pytest.mark.asyncio
    async def test_unavailable_survives_high_cardinality_truncation(self):
        now = datetime.now(UTC)
        rows = [
            [
                {
                    "state": str(i),
                    "last_changed": (now - timedelta(minutes=81 - i)).isoformat(),
                }
                for i in range(80)
            ]
            + [{"state": "unavailable", "last_changed": now.isoformat()}]
        ]
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": rows}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_entity_state_history_summary"](
                "sensor.test", 24
            )
        data = json.loads(result)
        assert data["success"] is True
        assert len(data["states_breakdown"]) == 32
        assert "unavailable" in data["states_breakdown"]
        assert data["recorder_coverage"]["omitted_state_labels"] == 49

    @pytest.mark.asyncio
    async def test_detail_level_full_default_backward_compat(self):
        """detail_level='full' (default) uses minimal_response=false in the API call."""
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": [[]]}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            await self.mock_mcp._tools["get_entity_state_history_summary"]("switch.test", 24)

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

    @pytest.mark.asyncio
    async def test_compact_true_forces_minimal_response(self):
        """compact=True forces minimal_response=true in the API call."""
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": [[]]}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            await self.mock_mcp._tools["get_entity_state_history_summary"](
                "switch.test", 24, None, "full", True
            )

        called_url = mock_request.call_args[0][2]
        assert "minimal_response=true" in called_url

    @pytest.mark.asyncio
    async def test_compact_false_default_backward_compat(self):
        """compact=False (default) uses minimal_response=false (with detail_level='full')."""
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": [[]]}
            register_history_tools(self.mock_mcp, self.ha_url, self.ha_token)
            await self.mock_mcp._tools["get_entity_state_history_summary"]("switch.test", 24)

        called_url = mock_request.call_args[0][2]
        assert "minimal_response=false" in called_url


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


class TestExactUtcTimeline:
    """Issue #53 exact-window and provenance contract."""

    def _history(self):
        return [
            [
                {
                    "entity_id": "switch.one",
                    "state": "off",
                    "last_changed": "2025-12-31T23:50:00+00:00",
                },
                {
                    "entity_id": "switch.one",
                    "state": "on",
                    "last_changed": "2026-01-01T00:30:00+00:00",
                },
                {
                    "entity_id": "switch.one",
                    "state": "off",
                    "last_changed": "2026-01-01T01:30:00+00:00",
                },
                {
                    "entity_id": "switch.one",
                    "state": "on",
                    "last_changed": "2026-01-01T03:00:00+00:00",
                },
            ]
        ]

    @pytest.mark.asyncio
    async def test_exact_window_reports_coverage_and_continuation(self, mock_mcp, ha_url, ha_token):
        register_history_tools(mock_mcp, ha_url, ha_token)
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": self._history()}
            result = await mock_mcp._tools["get_recent_state_changes"](
                entity_ids="switch.one",
                start_utc="2026-01-01T00:00:00+00:00",
                end_utc="2026-01-01T02:00:00+00:00",
                limit=1,
            )
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_changes"] == 2
        assert len(data["changes"]) == 1
        assert data["changes"][0]["utc_timestamp"] == "2026-01-01T01:30:00+00:00"
        assert data["changes"][0]["from_state"] == "on"
        assert data["covered_utc_window"]["start_utc"] == "2026-01-01T00:30:00+00:00"
        assert data["covered_utc_window"]["end_utc"] == "2026-01-01T01:30:00+00:00"
        assert data["covered_utc_window"]["completeness"] == "PARTIAL"
        assert data["covered_utc_window"]["retention_coverage"] == "UNKNOWN"
        assert data["event_classes"]["transient_events"] == "EVENT_BUS_NOT_PERSISTED"
        assert data["event_classes"]["automation_trace"] == "TRACE_UNAVAILABLE"
        assert data["continuation"]["truncated"] is True
        assert data["continuation"]["next_cursor"] == "2026-01-01T01:30:00+00:00"
        assert data["evidence_digest"].startswith("sha256:")
        called_url = mock_request.call_args[0][2]
        assert "filter_entity_id=switch.one" in called_url
        assert "end_time=2026-01-01T02:00:00+00:00" in called_url

    @pytest.mark.asyncio
    async def test_start_only_exact_window_defaults_end_to_now(self, mock_mcp, ha_url, ha_token):
        register_history_tools(mock_mcp, ha_url, ha_token)
        with patch("tools.history.make_ha_request") as mock_request:
            mock_request.return_value = {"success": True, "data": self._history()}
            result = await mock_mcp._tools["get_recent_state_changes"](
                entity_ids="switch.one",
                start_utc="2026-01-01T00:00:00+00:00",
            )
        data = json.loads(result)
        assert data["success"] is True
        called_url = mock_request.call_args[0][2]
        assert "end_time=" in called_url
        assert "end_time=2026-01-01T02%3A00%3A00%2B00%3A00" not in called_url

    @pytest.mark.asyncio
    async def test_naive_exact_boundary_fails_before_api(self, mock_mcp, ha_url, ha_token):
        register_history_tools(mock_mcp, ha_url, ha_token)
        with patch("tools.history.make_ha_request") as mock_request:
            result = await mock_mcp._tools["get_recent_state_changes"](
                start_utc="2026-01-01T00:00:00"
            )
        data = json.loads(result)
        assert data["success"] is False
        mock_request.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
