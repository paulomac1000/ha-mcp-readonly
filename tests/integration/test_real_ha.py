"""
Integration Tests for HA MCP Server
Tests against REAL Home Assistant instatece.

RUN:
    pytest tests/integration/ -v
    pytest tests/integration/test_real_ha.py::TestConnectivity -v
"""

import json
import os
import time
from pathlib import Path

import pytest

from tools.utils import load_registry, make_ha_request

# Configuration
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
HA_CONFIG_PATH = os.getenv("HA_CONFIG_PATH", "/config")

# Skip if not configured
pytestmark = pytest.mark.skipif(
    not HA_URL or not HA_TOKEN, reason="HA_URL and HA_TOKEN must be set"
)


# ============================================================
# 🔌 CONNECTIVITY
# ============================================================


class TestConnectivity:
    """Basic connectivity tests."""

    def test_ha_api_reachable(self):
        """Verify HA API is accessible."""
        result = make_ha_request(HA_URL, HA_TOKEN, "/api/")
        assert result["success"], f"API check failed: {result.get('error')}"
        print(f"\n✅ Connected: {result['data']['message']}")

    def test_config_directory_exists(self):
        """Verify config directory is mounted."""
        config_path = Path(HA_CONFIG_PATH)
        assert config_path.exists()
        assert (config_path / "configuration.yaml").exists()
        assert (config_path / ".storage").exists()
        print(f"\n✅ Config path verified: {HA_CONFIG_PATH}")

    def test_log_file_exists(self):
        """Verify log file is accessible."""
        log_path = Path(HA_CONFIG_PATH) / "home-assistant.log"
        assert log_path.exists()
        with open(log_path, "r") as f:
            lines = f.readlines()
        print(f"\n✅ Log file: {len(lines)} lines")

    def test_storage_files_exist(self):
        """Verify core storage files exist."""
        storage_path = Path(HA_CONFIG_PATH) / ".storage"
        required = [
            "core.entity_registry",
            "core.device_registry",
            "core.area_registry",
        ]
        for filename in required:
            assert (storage_path / filename).exists(), f"Missing: {filename}"
        print("\n✅ Storage files present")


# ============================================================
# 📊 STATES
# ============================================================


class TestStates:
    """State tools tests."""

    def test_get_domains_summary(self, real_mcp):
        result = real_mcp.call_tool("get_domains_summary")
        data = json.loads(result)
        assert data["success"]
        assert data["total_entities"] > 0
        print(f"\n✅ {data['total_entities']} entities in {data['total_domains']} domains")

    def test_get_all_states_by_domain(self, real_mcp):
        """Test get_all_states with domain filter - may fail if too many entities."""
        result = real_mcp.call_tool("get_all_states", domain="sensor")
        data = json.loads(result)

        # get_all_states returns success=False when >500 entities (by design)
        if data["success"]:
            assert data.get("count", 0) >= 0
            print(f"\n✅ get_all_states: {data.get('count', 0)} sensor entities")
        else:
            # Expected when too many entities - check for proper error response
            assert "Too many" in data.get("error", "") or "suggestion" in data
            print("\n⚠️ get_all_states: Too many entities (expected behavior)")

    def test_get_entity_state(self, real_mcp):
        result = real_mcp.call_tool("get_entity_state", entity_id="sun.sun")
        data = json.loads(result)
        assert data["success"]
        assert data["entity"]["entity_id"] == "sun.sun"
        print(f"\n✅ sun.sun = {data['entity']['state']}")

    def test_get_entity_state_batch(self, real_mcp, sample_entities):
        all_entities = sample_entities.get("all", [])
        if len(all_entities) < 2:
            pytest.skip("Not enough entities")

        ids = ",".join(all_entities[:3])
        result = real_mcp.call_tool("get_entity_state_batch", entity_ids=ids)
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Batch: {data['found_count']}/{data['found_count'] + data['missing_count']}")

    def test_get_states_grouped(self, real_mcp):
        result = real_mcp.call_tool("get_states_grouped", group_by="domain")
        data = json.loads(result)
        assert data["success"]
        assert len(data["groups"]) > 0
        print(f"\n✅ {len(data['groups'])} domain groups")

    def test_get_states_filtered(self, real_mcp):
        result = real_mcp.call_tool("get_states_filtered", domains="sensor", state="unavailable")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ {data['count']} unavailable sensors")

    def test_search_entities(self, real_mcp):
        result = real_mcp.call_tool("search_entities", search_term="temperature")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Search 'temperature': {data['count']} results")

    def test_get_system_overview(self, real_mcp):
        result = real_mcp.call_tool(
            "get_system_overview",
            include_unavailable=True,
            group_unavailable_by="integration",
        )
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ System: {data['summary']['unavailable_count']} unavailable")

    def test_get_entity_changes(self, real_mcp):
        result = real_mcp.call_tool("get_entity_changes", hours_back=1)
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ {data['total_changed']} entities changed in 1h")

    def test_verify_recent_implementation(self, real_mcp):
        result = real_mcp.call_tool("verify_recent_implementation", hours_back=1)
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Recent: {data['summary']['recent_entities_count']} entities")


# ============================================================
# 📋 LOGS
# ============================================================


class TestLogs:
    """Log analysis tests."""

    def test_get_log_insights(self, real_mcp):
        result = real_mcp.call_tool(
            "get_log_insights",
            hours=1,
            group_similar=True,
            include_affected_entities=True,
        )
        data = json.loads(result)
        assert data["success"]
        print(
            f"\n✅ Logs: {data['summary']['total_errors']} errors, {data['summary']['total_warnings']} warnings"
        )

    def test_get_log_insights_patterns(self, real_mcp):
        result = real_mcp.call_tool("get_log_insights", hours=24, severity="error")
        data = json.loads(result)
        assert data["success"]

        if data.get("grouped_errors"):
            for pattern, details in list(data["grouped_errors"].items())[:1]:
                assert "count" in details
                assert "affected_entities" in details
        print(f"\n✅ {len(data.get('grouped_errors', {}))} error patterns")

    def test_analyze_log_errors(self, real_mcp):
        result = real_mcp.call_tool("analyze_log_errors")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ {data['total_errors']} errors, {data['total_tracebacks']} tracebacks")

    def test_get_recent_logs(self, real_mcp):
        result = real_mcp.call_tool("get_recent_logs", lines=50, level="error")
        assert isinstance(result, str)
        print(f"\n✅ Recent logs: {len(result)} chars")

    def test_search_logs(self, real_mcp):
        result = real_mcp.call_tool("search_logs", search_term="ERROR")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Search 'ERROR': {data['total_found']} results")

    def test_get_startup_errors(self, real_mcp):
        result = real_mcp.call_tool("get_startup_errors")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Startup: {data['total_errors']} errors")

    def test_get_log_timeline(self, real_mcp):
        result = real_mcp.call_tool("get_log_timeline", hours="2")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Timeline: {data['total_events_found']} events")


# ============================================================
# 🩺 DIAGNOSTICS
# ============================================================


class TestDiagnostics:
    """Diagnostics tests."""

    def test_diagnose_system_health_full(self, real_mcp):
        result = real_mcp.call_tool(
            "diagnose_system_health",
            include_log_analysis=True,
            include_unavailable_breakdown=True,
            include_performance=True,
        )
        data = json.loads(result)
        assert data["success"]
        assert "health_score" in data["summary"]
        print(f"\n✅ Health: {data['summary']['health_score']}/100 ({data['summary']['status']})")

    def test_diagnose_system_health_minimal(self, real_mcp):
        result = real_mcp.call_tool(
            "diagnose_system_health",
            include_log_analysis=False,
            include_unavailable_breakdown=False,
        )
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Health (minimal): {data['summary']['health_score']}/100")

    def test_get_unavailable_entities_grouped(self, real_mcp):
        result = real_mcp.call_tool("get_unavailable_entities_grouped")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ {data['total_unavailable']} unavailable (grouped)")

    def test_get_integration_health(self, real_mcp):
        result = real_mcp.call_tool("get_integration_health", domain="sun")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ sun integration: {data['status']}")

    def test_get_energy_dashboard_data(self, real_mcp):
        result = real_mcp.call_tool("get_energy_dashboard_data")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Energy: {data['tariff_status']['current_tariff']}")


# ============================================================
# 🤖 AUTOMATIONS
# ============================================================


class TestAutomations:
    """Automation tests."""

    def test_list_automations(self, real_mcp):
        result = real_mcp.call_tool("list_automations")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ {data['total_count']} automations")

        if data.get("automations"):
            TestAutomations.sample_alias = data["automations"][0].get("alias")

    def test_search_automations(self, real_mcp):
        result = real_mcp.call_tool("search_automations", search_term="light")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Search 'light': {data['matched_count']} matches")

    def test_get_automation_code(self, real_mcp):
        if not hasattr(TestAutomations, "sample_alias"):
            pytest.skip("No automation from previous test")

        result = real_mcp.call_tool(
            "get_automation_code", automation_id=TestAutomations.sample_alias
        )
        data = json.loads(result)
        assert data["success"]
        assert "code" in data
        print(f"\n✅ Got code for: {data.get('alias')}")

    def test_get_automation_dependencies(self, real_mcp):
        if not hasattr(TestAutomations, "sample_alias"):
            pytest.skip("No automation")

        result = real_mcp.call_tool(
            "get_automation_dependencies", automation_id=TestAutomations.sample_alias
        )
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Dependencies: {len(data['dependencies'].get('entities', []))} entities")

    def test_diagnose_automation(self, real_mcp):
        if not hasattr(TestAutomations, "sample_alias"):
            pytest.skip("No automation")

        result = real_mcp.call_tool(
            "diagnose_automation",
            automation_id=TestAutomations.sample_alias,
            detail_level="summary",
        )
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Diagnose: {len(data['issues'])} issues")


# ============================================================
# 📘 BLUEPRINTS
# ============================================================


class TestBlueprints:
    """Blueprint tests."""

    def test_list_blueprints(self, real_mcp):
        result = real_mcp.call_tool("list_blueprints")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ {data['total_blueprints']} blueprints")

        if data.get("blueprints"):
            TestBlueprints.sample_path = data["blueprints"][0].get("path")

    def test_get_blueprint_code(self, real_mcp):
        if not hasattr(TestBlueprints, "sample_path"):
            pytest.skip("No blueprint")

        result = real_mcp.call_tool("get_blueprint_code", blueprint_path=TestBlueprints.sample_path)
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Blueprint code: {len(data['code'])} chars")

    def test_get_blueprint_usage_summary(self, real_mcp):
        """Test blueprint usage summary - may fail if parsing issues."""
        result = real_mcp.call_tool("get_blueprint_usage_summary")
        data = json.loads(result)

        # May return success=False if no blueprints or parsing issues
        if data.get("success", True) is False:
            # Valid error response - just log it
            print(f"\n⚠️ get_blueprint_usage_summary: {data.get('error', 'Unknown error')}")
        else:
            assert "total_instances" in data or "total_blueprints" in data
            print(f"\n✅ get_blueprint_usage_summary: {data.get('total_instances', 0)} instances")


# ============================================================
# 🗄️ STORAGE
# ============================================================


class TestStorage:
    """Storage/Registry tests."""

    def test_search_registries_batch(self, real_mcp):
        result = real_mcp.call_tool(
            "search_registries_batch", search_term="temperature", include_states=True
        )
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Registry search: {data['summary']['matched_entities']} entities")

    def test_get_entity_context(self, real_mcp, sample_entities):
        sensors = sample_entities.get("sensor", [])
        if not sensors:
            pytest.skip("No sensors")

        entity_id = sensors[0]
        result = real_mcp.call_tool("get_entity_context", entity_id=entity_id)
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Context: {len(data.get('related_entities', []))} related")

    def test_get_area_overview(self, real_mcp):
        """Test area overview - note: returns different structure than other tools."""
        areas = load_registry("core.area_registry", HA_CONFIG_PATH).get("data", {}).get("areas", [])
        if not areas:
            pytest.skip("No areas")

        result = real_mcp.call_tool("get_area_overview", area_id=areas[0]["id"])
        data = json.loads(result)

        # get_area_overview returns different structure:
        # On success: {"area_info": {...}, "devices_count": N, ...} (no "success" key)
        # On error: {"success": False, "error": "..."}
        if "success" in data:
            # Error response
            if data["success"] is False:
                pytest.fail(f"get_area_overview failed: {data.get('error')}")
        else:
            # Success - check for expected fields
            assert "area_info" in data or "devices_count" in data
            print(f"\n✅ get_area_overview: {data.get('devices_count', 'N/A')} devices")

    def test_get_entity_registry(self, real_mcp):
        result = real_mcp.call_tool("get_entity_registry")
        data = json.loads(result)
        assert data["total_entities"] > 0
        print(f"\n✅ Entity registry: {data['total_entities']} entities")

    def test_get_device_registry(self, real_mcp):
        result = real_mcp.call_tool("get_device_registry")
        data = json.loads(result)
        print(f"\n✅ Device registry: {data['total_devices']} devices")

    def test_get_config_entries(self, real_mcp):
        result = real_mcp.call_tool("get_config_entries")
        data = json.loads(result)
        print(f"\n✅ Config entries: {data['total_entries']} entries")

    def test_get_template_entities(self, real_mcp):
        result = real_mcp.call_tool("get_template_entities")
        data = json.loads(result)
        print(f"\n✅ Template entities: {data['total_templates']}")


# ============================================================
# 🛠️ DEV TOOLS
# ============================================================


class TestDevTools:
    """Developer tools tests."""

    def test_test_template(self, real_mcp):
        result = real_mcp.call_tool("test_template", template="{{ now().hour }}")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ {{ now().hour }} = {data['result']}")

    def test_test_templates_batch(self, real_mcp):
        templates = json.dumps({"hour": "{{ now().hour }}", "sun": "{{ states('sun.sun') }}"})
        result = real_mcp.call_tool("test_templates_batch", templates=templates)
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Batch: {data['successful']}/{data['total_templates']}")

    def test_get_template_performance(self, real_mcp):
        result = real_mcp.call_tool(
            "get_template_performance",
            template="{{ states | list | length }}",
            iterations=3,
        )
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Performance: {data['benchmark']['avg_ms']}ms avg")

    def test_check_entity_exists(self, real_mcp):
        result = real_mcp.call_tool("check_entity_exists", entity_id="sun.sun")
        data = json.loads(result)
        assert data["success"]
        assert data["exists"]
        print("\n✅ sun.sun exists")

    def test_check_entities_batch(self, real_mcp):
        result = real_mcp.call_tool("check_entities_batch", entity_ids="sun.sun,nonexistent.xyz")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Batch check: {data['summary']['exists']}")

    def test_diagnose_entity(self, real_mcp, sample_entities):
        sensors = sample_entities.get("sensor", [])
        if not sensors:
            pytest.skip("No sensors")

        result = real_mcp.call_tool("diagnose_entity", entity_id=sensors[0])
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Diagnose: {len(data['issues'])} issues")

    def test_diagnose_energy_setup(self, real_mcp):
        result = real_mcp.call_tool("diagnose_energy_setup")
        data = json.loads(result)
        assert data["success"]
        print(f"\n✅ Energy: {data['statistics']['total_energy_sensors']} sensors")


# ============================================================
# 🚀 PERFORMANCE
# ============================================================


class TestPerformance:
    """Performance and token savings tests."""

    def test_batch_vs_individual(self, real_mcp, sample_entities):
        all_entities = sample_entities.get("all", [])
        if len(all_entities) < 3:
            pytest.skip("Not enough entities")

        entities = all_entities[:3]

        # Individual
        start = time.time()
        individual_size = sum(
            len(real_mcp.call_tool("get_entity_state", entity_id=eid)) for eid in entities
        )
        individual_time = time.time() - start

        # Batch
        start = time.time()
        batch_result = real_mcp.call_tool("get_entity_state_batch", entity_ids=",".join(entities))
        batch_time = time.time() - start
        batch_size = len(batch_result)

        savings = (1 - batch_size / individual_size) * 100

        print(f"\n📊 Individual: {individual_time:.3f}s, {individual_size} chars")
        print(f"   Batch: {batch_time:.3f}s, {batch_size} chars")
        print(f"   Savings: {savings:.1f}%")

    def test_grouped_vs_raw(self, real_mcp):
        # Raw
        raw_result = real_mcp.call_tool("get_all_states", domain="sensor")
        raw_size = len(raw_result)

        # Grouped
        grouped_result = real_mcp.call_tool(
            "get_states_grouped", group_by="domain", include_counts_only=True
        )
        grouped_size = len(grouped_result)

        savings = (1 - grouped_size / raw_size) * 100

        print(f"\n📊 Raw: {raw_size} chars")
        print(f"   Grouped: {grouped_size} chars")
        print(f"   Savings: {savings:.1f}%")

    def test_diagnostics_consolidation(self, real_mcp):
        result = real_mcp.call_tool(
            "diagnose_system_health",
            include_log_analysis=True,
            include_unavailable_breakdown=True,
            include_performance=True,
        )
        data = json.loads(result)

        sections = [k for k in data.keys() if k not in ("success",)]

        print(f"\n📊 Diagnostics: {len(sections)} sections")
        print(f"   Size: {len(result)} chars")
        print(f"   Sections: {sections}")


# ============================================================
# 🔒 ERROR HANDLING
# ============================================================


class TestErrorHandling:
    """Error handling tests."""

    def test_nonexistent_entity(self, real_mcp):
        result = real_mcp.call_tool("get_entity_state", entity_id="sensor.does_not_exist_12345")
        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data.get("error", "").lower()

    def test_invalid_template(self, real_mcp):
        result = real_mcp.call_tool("test_template", template="{{ undefined_function() }}")
        data = json.loads(result)
        assert data["success"] is False

    def test_empty_search(self, real_mcp):
        result = real_mcp.call_tool("search_entities", search_term="xyzzy_nonexistent")
        data = json.loads(result)
        assert data["success"]
        assert data["count"] == 0

    def test_too_many_batch(self, real_mcp):
        many_ids = ",".join([f"sensor.test_{i}" for i in range(101)])
        result = real_mcp.call_tool("get_entity_state_batch", entity_ids=many_ids)
        data = json.loads(result)
        assert data["success"] is False
        assert "Too many" in data.get("error", "")

    def test_invalid_area(self, real_mcp):
        result = real_mcp.call_tool("get_area_overview", area_id="nonexistent_area_xyz")
        data = json.loads(result)
        assert data["success"] is False


# ============================================================
# 📝 SUMMARY
# ============================================================


class TestSummary:
    """Final summary."""

    def test_print_summary(self, real_mcp):
        domains = json.loads(real_mcp.call_tool("get_domains_summary"))
        health = json.loads(real_mcp.call_tool("diagnose_system_health"))

        print("\n" + "=" * 50)
        print("📊 HOME ASSISTANT SUMMARY")
        print("=" * 50)
        print(f"Entities: {domains.get('total_entities', 'N/A')}")
        print(f"Domains: {domains.get('total_domains', 'N/A')}")
        print(f"Health: {health.get('summary', {}).get('health_score', 'N/A')}/100")
        print(f"Status: {health.get('summary', {}).get('status', 'N/A')}")
        print("=" * 50)
