"""
Tests for tools/config_entries.py

Covers config entry detail retrieval, search, diagnostics,
and domain listing via mocked MCP tools.
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from tools.config_entries import register_config_entry_tools


# ─────────────────────────────────────────────────────────────
# TEST: get_config_entry_details
# ─────────────────────────────────────────────────────────────
class TestGetConfigEntryDetails:
    """Tests for get_config_entry_details()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data, sample_states):
        """Setup test fixtures."""
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.ha_url = ha_url
        self.ha_token = ha_token
        self.mock_registry_data = mock_registry_data
        self.sample_states = sample_states

        with ExitStack() as stack:
            mock_load = stack.enter_context(patch("tools.config_entries.load_registry"))
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            mock_entities = stack.enter_context(patch("tools.config_entries.get_registry_entities"))
            mock_entities.return_value = self.mock_registry_data["core.entity_registry"]["data"][
                "entities"
            ]

            mock_devices = stack.enter_context(patch("tools.config_entries.get_registry_devices"))
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            register_config_entry_tools(mock_mcp, config_path, ha_url, ha_token)

        self.tools = mock_mcp._tools

    @pytest.mark.asyncio
    async def test_get_existing_entry(self):
        """Test getting details of an existing config entry."""
        with ExitStack() as stack:
            mock_load = stack.enter_context(patch("tools.config_entries.load_registry"))
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            mock_entities = stack.enter_context(patch("tools.config_entries.get_registry_entities"))
            mock_entities.return_value = self.mock_registry_data["core.entity_registry"]["data"][
                "entities"
            ]

            mock_devices = stack.enter_context(patch("tools.config_entries.get_registry_devices"))
            mock_devices.return_value = self.mock_registry_data["core.device_registry"]["data"][
                "devices"
            ]

            mock_request = stack.enter_context(patch("tools.config_entries.make_ha_request"))
            mock_request.return_value = {"success": True, "data": self.sample_states}

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_config_entry_details"](
                "e01182bae2f8b20605c8317f4623d1e9"
            )

        data = json.loads(result)

        assert data["success"] is True
        assert data["entry_id"] == "e01182bae2f8b20605c8317f4623d1e9"
        assert data["domain"] == "mqtt"
        assert data["title"] == "192.168.1.100"
        assert data["disabled_by"] is None
        assert "entities" in data
        assert "devices" in data

    @pytest.mark.asyncio
    async def test_state_failed_all_unavailable(self):
        """All active entities unavailable should produce state 'failed'."""
        unavailable_states = [
            {
                "entity_id": "sensor.sonoff_button_battery",
                "state": "unavailable",
                "attributes": {},
            },
            {
                "entity_id": "binary_sensor.sonoff_button_action",
                "state": "unavailable",
                "attributes": {},
            },
        ]

        with ExitStack() as stack:
            mock_load = stack.enter_context(patch("tools.config_entries.load_registry"))
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            mock_entities = stack.enter_context(patch("tools.config_entries.get_registry_entities"))
            mock_entities.return_value = [
                e
                for e in self.mock_registry_data["core.entity_registry"]["data"]["entities"]
                if e.get("config_entry_id") == "e01182bae2f8b20605c8317f4623d1e9"
                and not e.get("disabled_by")
            ]

            mock_devices = stack.enter_context(patch("tools.config_entries.get_registry_devices"))
            mock_devices.return_value = []

            mock_request = stack.enter_context(patch("tools.config_entries.make_ha_request"))
            mock_request.return_value = {"success": True, "data": unavailable_states}

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_config_entry_details"](
                "e01182bae2f8b20605c8317f4623d1e9"
            )

        data = json.loads(result)
        assert data["success"] is True
        assert data["state"] == "failed"

    @pytest.mark.asyncio
    async def test_get_nonexistent_entry(self):
        """Test getting details of a non-existent config entry."""
        with patch("tools.config_entries.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_config_entry_details"]("nonexistent_id")

        data = json.loads(result)

        assert data["success"] is False
        assert "not found" in data["error"].lower()
        assert "suggestion" in data

    @pytest.mark.asyncio
    async def test_disabled_entry_state(self):
        """Test that disabled entries show correct state."""
        with ExitStack() as stack:
            mock_load = stack.enter_context(patch("tools.config_entries.load_registry"))
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            mock_entities = stack.enter_context(patch("tools.config_entries.get_registry_entities"))
            mock_entities.return_value = self.mock_registry_data["core.entity_registry"]["data"][
                "entities"
            ]

            mock_devices = stack.enter_context(patch("tools.config_entries.get_registry_devices"))
            mock_devices.return_value = []

            mock_request = stack.enter_context(patch("tools.config_entries.make_ha_request"))
            mock_request.return_value = {"success": True, "data": self.sample_states}

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["get_config_entry_details"](
                "gree_disabled_entry_123"
            )

        data = json.loads(result)

        assert data["success"] is True
        assert data["disabled_by"] == "user"
        assert data["state"] == "not_loaded"


# ─────────────────────────────────────────────────────────────
# TEST: search_config_entries
# ─────────────────────────────────────────────────────────────
class TestSearchConfigEntries:
    """Tests for search_config_entries()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data, sample_states):
        """Setup test fixtures."""
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.ha_url = ha_url
        self.ha_token = ha_token
        self.mock_registry_data = mock_registry_data
        self.sample_states = sample_states

    @pytest.mark.asyncio
    async def test_search_by_domain(self):
        """Test searching config entries by domain."""
        with ExitStack() as stack:
            mock_load = stack.enter_context(patch("tools.config_entries.load_registry"))
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            mock_entities = stack.enter_context(patch("tools.config_entries.get_registry_entities"))
            mock_entities.return_value = []

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["search_config_entries"](domain="mqtt")

        data = json.loads(result)

        assert data["success"] is True
        assert data["matched_count"] == 1
        assert data["entries"][0]["domain"] == "mqtt"

    @pytest.mark.asyncio
    async def test_search_by_title(self):
        """Test searching config entries by title."""
        with ExitStack() as stack:
            mock_load = stack.enter_context(patch("tools.config_entries.load_registry"))
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            mock_entities = stack.enter_context(patch("tools.config_entries.get_registry_entities"))
            mock_entities.return_value = []

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["search_config_entries"](title="bedroom")

        data = json.loads(result)

        assert data["success"] is True
        assert data["matched_count"] == 1
        assert "bedroom" in data["entries"][0]["title"].lower()

    @pytest.mark.asyncio
    async def test_search_disabled_only(self):
        """Test searching only disabled config entries."""
        with ExitStack() as stack:
            mock_load = stack.enter_context(patch("tools.config_entries.load_registry"))
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            mock_entities = stack.enter_context(patch("tools.config_entries.get_registry_entities"))
            mock_entities.return_value = []

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["search_config_entries"](disabled_only=True)

        data = json.loads(result)

        assert data["success"] is True
        assert data["matched_count"] == 1
        assert data["entries"][0]["disabled_by"] is not None

    @pytest.mark.asyncio
    async def test_search_no_results(self):
        """Test search with no matching results."""
        with ExitStack() as stack:
            mock_load = stack.enter_context(patch("tools.config_entries.load_registry"))
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            mock_entities = stack.enter_context(patch("tools.config_entries.get_registry_entities"))
            mock_entities.return_value = []

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["search_config_entries"](
                domain="nonexistent_domain"
            )

        data = json.loads(result)

        assert data["success"] is True
        assert data["matched_count"] == 0
        assert len(data["entries"]) == 0

    @pytest.mark.asyncio
    async def test_search_with_entities_count(self):
        """with_entities=True should add entities_count to each result."""
        with ExitStack() as stack:
            mock_load = stack.enter_context(patch("tools.config_entries.load_registry"))
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            mock_entities = stack.enter_context(patch("tools.config_entries.get_registry_entities"))
            mock_entities.return_value = self.mock_registry_data["core.entity_registry"]["data"][
                "entities"
            ]

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["search_config_entries"](
                domain="mqtt", with_entities=True
            )

        data = json.loads(result)
        assert data["success"] is True
        assert data["matched_count"] == 1
        assert "entities_count" in data["entries"][0]


# ─────────────────────────────────────────────────────────────
# TEST: diagnose_config_entry
# ─────────────────────────────────────────────────────────────
class TestDiagnoseConfigEntry:
    """Tests for diagnose_config_entry()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data, sample_states):
        """Setup test fixtures."""
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.ha_url = ha_url
        self.ha_token = ha_token
        self.mock_registry_data = mock_registry_data
        self.sample_states = sample_states

    @pytest.mark.asyncio
    async def test_diagnose_healthy_entry(self):
        """Test diagnosing a healthy config entry."""
        with ExitStack() as stack:
            mock_load = stack.enter_context(patch("tools.config_entries.load_registry"))
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            mock_entities = stack.enter_context(patch("tools.config_entries.get_registry_entities"))
            mock_entities.return_value = [
                e
                for e in self.mock_registry_data["core.entity_registry"]["data"]["entities"]
                if e.get("config_entry_id") == "e01182bae2f8b20605c8317f4623d1e9"
            ]

            mock_devices = stack.enter_context(patch("tools.config_entries.get_registry_devices"))
            mock_devices.return_value = []

            mock_request = stack.enter_context(patch("tools.config_entries.make_ha_request"))
            mock_request.return_value = {"success": True, "data": self.sample_states}

            mock_logs = stack.enter_context(patch("tools.config_entries.tail_log_file"))
            mock_logs.return_value = []

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["diagnose_config_entry"](
                "e01182bae2f8b20605c8317f4623d1e9"
            )

        data = json.loads(result)

        assert data["success"] is True
        assert "entry_info" in data
        assert "entities_status" in data
        assert "issues" in data
        assert "recommendations" in data

    @pytest.mark.asyncio
    async def test_diagnose_disabled_entry(self):
        """Test diagnosing a disabled config entry."""
        with ExitStack() as stack:
            mock_load = stack.enter_context(patch("tools.config_entries.load_registry"))
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            mock_entities = stack.enter_context(patch("tools.config_entries.get_registry_entities"))
            mock_entities.return_value = [
                e
                for e in self.mock_registry_data["core.entity_registry"]["data"]["entities"]
                if e.get("config_entry_id") == "gree_disabled_entry_123"
            ]

            mock_devices = stack.enter_context(patch("tools.config_entries.get_registry_devices"))
            mock_devices.return_value = []

            mock_request = stack.enter_context(patch("tools.config_entries.make_ha_request"))
            mock_request.return_value = {"success": True, "data": self.sample_states}

            mock_logs = stack.enter_context(patch("tools.config_entries.tail_log_file"))
            mock_logs.return_value = []

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["diagnose_config_entry"]("gree_disabled_entry_123")

        data = json.loads(result)

        assert data["success"] is True
        assert data["entry_info"]["disabled_by"] == "user"

        issue_types = [i["type"] for i in data["issues"]]
        assert "entry_disabled" in issue_types

    @pytest.mark.asyncio
    async def test_diagnose_with_log_errors(self):
        """Test diagnosing entry with log errors."""
        log_lines = [
            "2026-01-21 14:24:55.286 ERROR (MainThread) [homeassistant.components.mqtt] Connection failed",
            "2026-01-21 14:24:56.000 WARNING (MainThread) [mqtt] Reconnecting...",
        ]

        with ExitStack() as stack:
            mock_load = stack.enter_context(patch("tools.config_entries.load_registry"))
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            mock_entities = stack.enter_context(patch("tools.config_entries.get_registry_entities"))
            mock_entities.return_value = []

            mock_devices = stack.enter_context(patch("tools.config_entries.get_registry_devices"))
            mock_devices.return_value = []

            mock_request = stack.enter_context(patch("tools.config_entries.make_ha_request"))
            mock_request.return_value = {"success": True, "data": []}

            mock_logs = stack.enter_context(patch("tools.config_entries.tail_log_file"))
            mock_logs.return_value = log_lines

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["diagnose_config_entry"](
                "e01182bae2f8b20605c8317f4623d1e9"
            )

        data = json.loads(result)

        assert data["success"] is True
        assert len(data["log_errors"]) > 0

    @pytest.mark.asyncio
    async def test_diagnose_not_found(self):
        """Non-existent entry id should return success=False."""
        with patch("tools.config_entries.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["diagnose_config_entry"]("nonexistent_id")

        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_diagnose_unavailable_entities_issue(self):
        """Unavailable entities should produce an entities_unavailable issue."""
        unavailable_states = [
            {
                "entity_id": "sensor.sonoff_button_battery",
                "state": "unavailable",
                "attributes": {},
            },
            {
                "entity_id": "binary_sensor.sonoff_button_action",
                "state": "unavailable",
                "attributes": {},
            },
        ]

        with ExitStack() as stack:
            mock_load = stack.enter_context(patch("tools.config_entries.load_registry"))
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            mock_entities = stack.enter_context(patch("tools.config_entries.get_registry_entities"))
            mock_entities.return_value = [
                e
                for e in self.mock_registry_data["core.entity_registry"]["data"]["entities"]
                if e.get("config_entry_id") == "e01182bae2f8b20605c8317f4623d1e9"
            ]

            mock_devices = stack.enter_context(patch("tools.config_entries.get_registry_devices"))
            mock_devices.return_value = []

            mock_request = stack.enter_context(patch("tools.config_entries.make_ha_request"))
            mock_request.return_value = {"success": True, "data": unavailable_states}

            mock_logs = stack.enter_context(patch("tools.config_entries.tail_log_file"))
            mock_logs.return_value = []

            register_config_entry_tools(self.mock_mcp, self.config_path, self.ha_url, self.ha_token)
            result = await self.mock_mcp._tools["diagnose_config_entry"](
                "e01182bae2f8b20605c8317f4623d1e9"
            )

        data = json.loads(result)
        assert data["success"] is True
        issue_types = [i["type"] for i in data["issues"]]
        assert "entities_unavailable" in issue_types


# ─────────────────────────────────────────────────────────────
# TEST: list_config_entry_domains
# ─────────────────────────────────────────────────────────────
class TestListConfigEntryDomains:
    """Tests for list_config_entry_domains()."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, config_path, ha_url, ha_token, mock_registry_data):
        """Setup test fixtures."""
        self.mock_mcp = mock_mcp
        self.config_path = config_path
        self.mock_registry_data = mock_registry_data

    @pytest.mark.asyncio
    async def test_list_domains(self):
        """Test listing all config entry domains."""
        with patch("tools.config_entries.load_registry") as mock_load:
            mock_load.side_effect = lambda name, path: self.mock_registry_data.get(name, {})

            register_config_entry_tools(self.mock_mcp, self.config_path, "http://test", "token")
            result = await self.mock_mcp._tools["list_config_entry_domains"]()

        data = json.loads(result)

        assert data["success"] is True
        assert data["total_entries"] == 4
        assert data["total_domains"] == 4

        domains = [d["domain"] for d in data["domains"]]
        assert "mqtt" in domains
        assert "gree" in domains


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
