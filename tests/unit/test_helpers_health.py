"""
Unit tests for tools/helpers_health.py — diagnose_stuck_helpers tool.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from tools.helpers_health import register_helpers_health_tools


class TestDiagnoseStuckHelpers:
    """Tests for diagnose_stuck_helpers — stuck helper entity detection."""

    def _make_state(self, entity_id, last_changed, state="on", friendly_name=""):
        ts = last_changed.isoformat()
        return {
            "entity_id": entity_id,
            "state": state,
            "last_changed": ts,
            "last_updated": ts,
            "attributes": {"friendly_name": friendly_name},
        }

    def _stuck_states_with_severity(self):
        now = datetime.now(UTC)
        return [
            self._make_state(
                "input_boolean.critical_one",
                now - timedelta(hours=200),
                state="on",
                friendly_name="Critical Boolean",
            ),
            self._make_state(
                "input_boolean.error_one",
                now - timedelta(hours=100),
                state="off",
                friendly_name="Error Boolean",
            ),
            self._make_state(
                "input_boolean.warning_one",
                now - timedelta(hours=48),
                state="on",
                friendly_name="Warning Boolean",
            ),
            self._make_state(
                "timer.laundry",
                now - timedelta(hours=48),
                state="idle",
                friendly_name="Laundry Timer",
            ),
            self._make_state(
                "input_boolean.active_test",
                now - timedelta(minutes=5),
                state="off",
                friendly_name="Active Boolean",
            ),
            self._make_state(
                "light.living_room",
                now - timedelta(minutes=5),
                state="on",
                friendly_name="Living Room Light",
            ),
        ]

    # ------------------------------------------------------------------
    # success path
    # ------------------------------------------------------------------

    def test_success_path(self, mock_mcp, ha_url, ha_token):
        states = self._stuck_states_with_severity()
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["success"] is True
        assert data["stale_threshold_hours"] == 24
        assert data["stuck_count"] == 4
        assert data["total_helpers_scanned"] == 5

        stuck_ids = [h["entity_id"] for h in data["stuck_helpers"]]
        assert "input_boolean.critical_one" in stuck_ids
        assert "input_boolean.error_one" in stuck_ids
        assert "input_boolean.warning_one" in stuck_ids
        assert "timer.laundry" in stuck_ids
        assert "input_boolean.active_test" not in stuck_ids
        assert "light.living_room" not in stuck_ids

        assert len(data["recommendations"]) >= 1
        assert "stuck helpers" in data["recommendations"][0]

        for h in data["stuck_helpers"]:
            assert "entity_id" in h
            assert "state" in h
            assert "last_changed" in h
            assert "hours_stuck" in h
            assert "severity" in h
            assert "domain" in h

    def test_success_path_sorted_by_hours_descending(self, mock_mcp, ha_url, ha_token):
        states = self._stuck_states_with_severity()
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        hours = [h["hours_stuck"] for h in data["stuck_helpers"]]
        assert hours == sorted(hours, reverse=True)

    # ------------------------------------------------------------------
    # empty result
    # ------------------------------------------------------------------

    def test_empty_result(self, mock_mcp, ha_url, ha_token):
        now = datetime.now(UTC)
        states = [
            self._make_state(
                "input_boolean.active_btn",
                now - timedelta(minutes=5),
                state="off",
                friendly_name="Active Button",
            ),
            self._make_state(
                "timer.active_timer",
                now - timedelta(minutes=10),
                state="active",
                friendly_name="Active Timer",
            ),
        ]
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["success"] is True
        assert data["stuck_count"] == 0
        assert data["stuck_helpers"] == []
        assert data["recommendations"] == []
        assert data["total_helpers_scanned"] == 2

    # ------------------------------------------------------------------
    # filtered by entity_ids
    # ------------------------------------------------------------------

    def test_filtered_by_entity_ids(self, mock_mcp, ha_url, ha_token):
        states = self._stuck_states_with_severity()
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(
                tool(
                    stale_hours=24,
                    entity_ids="input_boolean.warning_one",
                )
            )

        assert data["success"] is True
        assert data["stuck_count"] == 1
        stuck_ids = [h["entity_id"] for h in data["stuck_helpers"]]
        assert stuck_ids == ["input_boolean.warning_one"]
        assert data["total_helpers_scanned"] == 5

    def test_filtered_by_multiple_entity_ids(self, mock_mcp, ha_url, ha_token):
        states = self._stuck_states_with_severity()
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(
                tool(
                    stale_hours=24,
                    entity_ids="input_boolean.warning_one, timer.laundry",
                )
            )

        assert data["success"] is True
        assert data["stuck_count"] == 2
        stuck_ids = sorted(h["entity_id"] for h in data["stuck_helpers"])
        assert stuck_ids == ["input_boolean.warning_one", "timer.laundry"]

    def test_filtered_entity_not_in_states(self, mock_mcp, ha_url, ha_token):
        now = datetime.now(UTC)
        states = [
            self._make_state(
                "input_boolean.present",
                now - timedelta(hours=48),
                state="on",
                friendly_name="Present",
            ),
        ]
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(
                tool(
                    stale_hours=24,
                    entity_ids="input_boolean.not_found",
                )
            )

        assert data["success"] is True
        assert data["stuck_count"] == 0

    # ------------------------------------------------------------------
    # severity tiers
    # ------------------------------------------------------------------

    def test_severity_critical(self, mock_mcp, ha_url, ha_token):
        now = datetime.now(UTC)
        states = [
            self._make_state(
                "input_boolean.critical",
                now - timedelta(hours=200),
                state="on",
                friendly_name="Critical",
            ),
        ]
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["stuck_count"] == 1
        assert data["stuck_helpers"][0]["severity"] == "critical"

    def test_severity_critical_at_boundary(self, mock_mcp, ha_url, ha_token):
        now = datetime.now(UTC)
        states = [
            self._make_state(
                "input_boolean.at_boundary",
                now - timedelta(hours=168),
                state="on",
                friendly_name="At Boundary",
            ),
        ]
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["stuck_count"] == 1
        assert data["stuck_helpers"][0]["severity"] == "critical"

    def test_severity_error(self, mock_mcp, ha_url, ha_token):
        now = datetime.now(UTC)
        states = [
            self._make_state(
                "input_boolean.error_ent",
                now - timedelta(hours=100),
                state="on",
                friendly_name="Error Entity",
            ),
        ]
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["stuck_count"] == 1
        assert data["stuck_helpers"][0]["severity"] == "error"

    def test_severity_error_at_boundary(self, mock_mcp, ha_url, ha_token):
        now = datetime.now(UTC)
        states = [
            self._make_state(
                "input_boolean.at_72",
                now - timedelta(hours=72),
                state="on",
                friendly_name="At 72",
            ),
        ]
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["stuck_count"] == 1
        assert data["stuck_helpers"][0]["severity"] == "error"

    def test_severity_warning_default_stale(self, mock_mcp, ha_url, ha_token):
        now = datetime.now(UTC)
        states = [
            self._make_state(
                "input_boolean.warn_ent",
                now - timedelta(hours=48),
                state="off",
                friendly_name="Warning Entity",
            ),
        ]
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["stuck_count"] == 1
        assert data["stuck_helpers"][0]["severity"] == "warning"

    def test_severity_warning_at_stale_boundary(self, mock_mcp, ha_url, ha_token):
        now = datetime.now(UTC)
        states = [
            self._make_state(
                "input_boolean.at_stale",
                now - timedelta(hours=24),
                state="on",
                friendly_name="At Stale Boundary",
            ),
        ]
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["stuck_count"] == 1
        assert data["stuck_helpers"][0]["severity"] == "warning"

    def test_stale_hours_excludes_below_threshold(self, mock_mcp, ha_url, ha_token):
        now = datetime.now(UTC)
        states = [
            self._make_state(
                "input_boolean.below",
                now - timedelta(hours=23),
                state="on",
                friendly_name="Below Threshold",
            ),
        ]
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["stuck_count"] == 0

    # ------------------------------------------------------------------
    # entities dict format
    # ------------------------------------------------------------------

    def test_entities_as_dict(self, mock_mcp, ha_url, ha_token):
        now = datetime.now(UTC)
        entities_dict = {
            "entities": [
                self._make_state(
                    "input_boolean.dict_ent",
                    now - timedelta(hours=50),
                    state="on",
                    friendly_name="From Dict",
                ),
            ],
        }
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": entities_dict}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["success"] is True
        assert data["stuck_count"] == 1
        assert data["stuck_helpers"][0]["entity_id"] == "input_boolean.dict_ent"

    # ------------------------------------------------------------------
    # counter domain
    # ------------------------------------------------------------------

    def test_counter_detected(self, mock_mcp, ha_url, ha_token):
        now = datetime.now(UTC)
        states = [
            self._make_state(
                "counter.laundry_count",
                now - timedelta(hours=100),
                state="5",
                friendly_name="Laundry Counter",
            ),
        ]
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["stuck_count"] == 1
        assert data["stuck_helpers"][0]["domain"] == "counter"
        assert data["total_helpers_scanned"] == 1

    # ------------------------------------------------------------------
    # missing last_changed
    # ------------------------------------------------------------------

    def test_skips_missing_last_changed(self, mock_mcp, ha_url, ha_token):
        states = [
            {
                "entity_id": "input_boolean.no_timestamp",
                "state": "on",
                "attributes": {"friendly_name": "No Timestamp"},
            },
        ]
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["stuck_count"] == 0
        assert data["total_helpers_scanned"] == 1

    # ------------------------------------------------------------------
    # malformed last_changed
    # ------------------------------------------------------------------

    def test_malformed_last_changed_skipped(self, mock_mcp, ha_url, ha_token):
        states = [
            {
                "entity_id": "input_boolean.bad_ts",
                "state": "on",
                "last_changed": "not-a-date",
                "attributes": {"friendly_name": "Bad TS"},
            },
        ]
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["stuck_count"] == 0

    # ------------------------------------------------------------------
    # hours_stuck rounding
    # ------------------------------------------------------------------

    def test_hours_stuck_rounded(self, mock_mcp, ha_url, ha_token):
        now = datetime.now(UTC)
        states = [
            self._make_state(
                "input_boolean.rounded",
                now - timedelta(hours=48, minutes=17, seconds=23),
                state="on",
                friendly_name="Rounded",
            ),
        ]
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": True, "data": states}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool(stale_hours=24))

        assert data["stuck_count"] == 1
        h = data["stuck_helpers"][0]["hours_stuck"]
        assert isinstance(h, float)
        assert h >= 48.0

    # ------------------------------------------------------------------
    # api failure
    # ------------------------------------------------------------------

    def test_api_failure(self, mock_mcp, ha_url, ha_token):
        with patch("tools.helpers_health.make_ha_request") as mock_req:
            mock_req.return_value = {"success": False}
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool())

        assert data["success"] is False
        assert "error" in data

    # ------------------------------------------------------------------
    # exception handler
    # ------------------------------------------------------------------

    def test_exception_handler(self, mock_mcp, ha_url, ha_token):
        with patch(
            "tools.helpers_health._do_diagnose_stuck_helpers",
            side_effect=RuntimeError("test explosion"),
        ):
            register_helpers_health_tools(mock_mcp, ha_url, ha_token)
            tool = mock_mcp._tools["diagnose_stuck_helpers"]
            data = json.loads(tool())

        assert data["success"] is False
        assert "test explosion" in data.get("error", "")
