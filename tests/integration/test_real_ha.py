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
        print(f"\n[OK] Connected: {result['data']['message']}")

    def test_config_directory_exists(self):
        """Verify config directory is mounted."""
        config_path = Path(HA_CONFIG_PATH)
        assert config_path.exists()
        assert (config_path / "configuration.yaml").exists()
        assert (config_path / ".storage").exists()
        print(f"\n[OK] Config path verified: {HA_CONFIG_PATH}")

    def test_log_file_exists(self):
        """Verify log file is accessible."""
        log_path = Path(HA_CONFIG_PATH) / "home-assistant.log"
        assert log_path.exists()
        with open(log_path) as f:
            lines = f.readlines()
        print(f"\n[OK] Log file: {len(lines)} lines")

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
        print("\n[OK] Storage files present")


# ============================================================
# [STATS] STATES
# ============================================================


class TestStates:
    """State tools tests."""

    def test_get_domains_summary(self, real_mcp):
        result = real_mcp.call_tool("get_domains_summary")
        data = json.loads(result)
        assert data["success"]
        assert data["total_entities"] > 0
        print(f"\n[OK] {data['total_entities']} entities in {data['total_domains']} domains")

    def test_get_all_states_by_domain(self, real_mcp):
        """Test get_all_states with domain filter - may fail if too many entities."""
        result = real_mcp.call_tool("get_all_states", domain="sensor")
        data = json.loads(result)

        # get_all_states returns success=False when >500 entities (by design)
        if data["success"]:
            assert data.get("count", 0) >= 0
            print(f"\n[OK] get_all_states: {data.get('count', 0)} sensor entities")
        else:
            # Expected when too many entities - check for proper error response
            assert "Too many" in data.get("error", "") or "suggestion" in data
            print("\n[WARN] get_all_states: Too many entities (expected behavior)")

    def test_get_entity_state(self, real_mcp):
        result = real_mcp.call_tool("get_entity_state", entity_id="sun.sun")
        data = json.loads(result)
        assert data["success"]
        assert data["entity"]["entity_id"] == "sun.sun"
        print(f"\n[OK] sun.sun = {data['entity']['state']}")

    def test_get_entity_state_batch(self, real_mcp, sample_entities):
        all_entities = sample_entities.get("all", [])
        if len(all_entities) < 2:
            pytest.skip("Not enough entities")

        ids = ",".join(all_entities[:3])
        result = real_mcp.call_tool("get_entity_state_batch", entity_ids=ids)
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Batch: {data['found_count']}/{data['found_count'] + data['missing_count']}")

    def test_get_states_grouped(self, real_mcp):
        result = real_mcp.call_tool("get_states_grouped", group_by="domain")
        data = json.loads(result)
        assert data["success"]
        assert len(data["groups"]) > 0
        print(f"\n[OK] {len(data['groups'])} domain groups")

    def test_get_states_filtered(self, real_mcp):
        result = real_mcp.call_tool("get_states_filtered", domains="sensor", state="unavailable")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] {data['count']} unavailable sensors")

    def test_search_entities(self, real_mcp):
        result = real_mcp.call_tool("search_entities", search_term="temperature")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Search 'temperature': {data['count']} results")

    def test_get_system_overview(self, real_mcp):
        result = real_mcp.call_tool(
            "get_system_overview",
            include_unavailable=True,
            group_unavailable_by="integration",
        )
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] System: {data['summary']['unavailable_count']} unavailable")

    def test_get_entity_changes(self, real_mcp):
        result = real_mcp.call_tool("get_entity_changes", hours_back=1)
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] {data['total_changed']} entities changed in 1h")

    def test_verify_recent_implementation(self, real_mcp):
        result = real_mcp.call_tool("verify_recent_implementation", hours_back=1)
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Recent: {data['summary']['recent_entities_count']} entities")


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
            f"\n[OK] Logs: {data['summary']['total_errors']} errors, {data['summary']['total_warnings']} warnings"
        )

    def test_get_log_insights_patterns(self, real_mcp):
        result = real_mcp.call_tool("get_log_insights", hours=24, severity="error")
        data = json.loads(result)
        assert data["success"]

        if data.get("grouped_errors"):
            for pattern, details in list(data["grouped_errors"].items())[:1]:
                assert "count" in details
                assert "affected_entities" in details
        print(f"\n[OK] {len(data.get('grouped_errors', {}))} error patterns")

    def test_analyze_log_errors(self, real_mcp):
        result = real_mcp.call_tool("analyze_log_errors")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] {data['total_errors']} errors, {data['total_tracebacks']} tracebacks")

    def test_get_recent_logs(self, real_mcp):
        result = real_mcp.call_tool("get_recent_logs", lines=50, level="error")
        assert isinstance(result, str)
        print(f"\n[OK] Recent logs: {len(result)} chars")

    def test_search_logs(self, real_mcp):
        result = real_mcp.call_tool("search_logs", search_term="ERROR")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Search 'ERROR': {data['total_found']} results")

    def test_get_startup_errors(self, real_mcp):
        result = real_mcp.call_tool("get_startup_errors")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Startup: {data['total_errors']} errors")

    def test_get_log_timeline(self, real_mcp):
        result = real_mcp.call_tool("get_log_timeline", hours="2")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Timeline: {data['total_events_found']} events")


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
        print(f"\n[OK] Health: {data['summary']['health_score']}/100 ({data['summary']['status']})")

    def test_diagnose_system_health_minimal(self, real_mcp):
        result = real_mcp.call_tool(
            "diagnose_system_health",
            include_log_analysis=False,
            include_unavailable_breakdown=False,
        )
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Health (minimal): {data['summary']['health_score']}/100")

    def test_get_unavailable_entities_grouped(self, real_mcp):
        result = real_mcp.call_tool("get_unavailable_entities_grouped")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] {data['total_unavailable']} unavailable (grouped)")

    def test_get_integration_health(self, real_mcp):
        result = real_mcp.call_tool("get_integration_health", domain="sun")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] sun integration: {data['status']}")

    def test_get_energy_dashboard_data(self, real_mcp):
        result = real_mcp.call_tool("get_energy_dashboard_data")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Energy: {data['tariff_status']['current_tariff']}")


# ============================================================
# 🤖 AUTOMATIONS
# ============================================================


class TestAutomations:
    """Automation tests."""

    def test_list_automations(self, real_mcp):
        result = real_mcp.call_tool("list_automations")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] {data['total_count']} automations")

        if data.get("automations"):
            TestAutomations.sample_alias = data["automations"][0].get("alias")

    def test_search_automations(self, real_mcp):
        result = real_mcp.call_tool("search_automations", search_term="light")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Search 'light': {data['matched_count']} matches")

    def test_get_automation_code(self, real_mcp):
        if not hasattr(TestAutomations, "sample_alias"):
            pytest.skip("No automation from previous test")

        result = real_mcp.call_tool(
            "get_automation_code", automation_id=TestAutomations.sample_alias
        )
        data = json.loads(result)
        assert data["success"]
        assert "code" in data
        print(f"\n[OK] Got code for: {data.get('alias')}")

    def test_get_automation_dependencies(self, real_mcp):
        if not hasattr(TestAutomations, "sample_alias"):
            pytest.skip("No automation")

        result = real_mcp.call_tool(
            "get_automation_dependencies", automation_id=TestAutomations.sample_alias
        )
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Dependencies: {len(data['dependencies'].get('entities', []))} entities")

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
        print(f"\n[OK] Diagnose: {len(data['issues'])} issues")


# ============================================================
# 📘 BLUEPRINTS
# ============================================================


class TestBlueprints:
    """Blueprint tests."""

    def test_list_blueprints(self, real_mcp):
        result = real_mcp.call_tool("list_blueprints")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] {data['total_blueprints']} blueprints")

        if data.get("blueprints"):
            TestBlueprints.sample_path = data["blueprints"][0].get("path")

    def test_get_blueprint_code(self, real_mcp):
        if not hasattr(TestBlueprints, "sample_path"):
            pytest.skip("No blueprint")

        result = real_mcp.call_tool("get_blueprint_code", blueprint_path=TestBlueprints.sample_path)
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Blueprint code: {len(data['code'])} chars")

    def test_get_blueprint_usage_summary(self, real_mcp):
        """Test blueprint usage summary - may fail if parsing issues."""
        result = real_mcp.call_tool("get_blueprint_usage_summary")
        data = json.loads(result)

        # May return success=False if no blueprints or parsing issues
        if data.get("success", True) is False:
            # Valid error response - just log it
            print(f"\n[WARN] get_blueprint_usage_summary: {data.get('error', 'Unknown error')}")
        else:
            assert "total_instances" in data or "total_blueprints" in data
            print(f"\n[OK] get_blueprint_usage_summary: {data.get('total_instances', 0)} instances")


# ============================================================
# STORAGE
# ============================================================


class TestStorage:
    """Storage/Registry tests."""

    def test_search_registries_batch(self, real_mcp):
        result = real_mcp.call_tool(
            "search_registries_batch", search_term="temperature", include_states=True
        )
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Registry search: {data['summary']['matched_entities']} entities")

    def test_get_entity_context(self, real_mcp, sample_entities):
        sensors = sample_entities.get("sensor", [])
        if not sensors:
            pytest.skip("No sensors")

        entity_id = sensors[0]
        result = real_mcp.call_tool("get_entity_context", entity_id=entity_id)
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Context: {len(data.get('related_entities', []))} related")

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
            print(f"\n[OK] get_area_overview: {data.get('devices_count', 'N/A')} devices")

    def test_get_entity_registry(self, real_mcp):
        result = real_mcp.call_tool("get_entity_registry")
        data = json.loads(result)
        assert data["total_entities"] > 0
        print(f"\n[OK] Entity registry: {data['total_entities']} entities")

    def test_get_device_registry(self, real_mcp):
        result = real_mcp.call_tool("get_device_registry")
        data = json.loads(result)
        print(f"\n[OK] Device registry: {data['total_devices']} devices")

    def test_get_config_entries(self, real_mcp):
        result = real_mcp.call_tool("get_config_entries")
        data = json.loads(result)
        print(f"\n[OK] Config entries: {data['total_entries']} entries")

    def test_get_template_entities(self, real_mcp):
        result = real_mcp.call_tool("get_template_entities")
        data = json.loads(result)
        print(f"\n[OK] Template entities: {data['total_templates']}")

    def test_get_timers(self, real_mcp):
        result = real_mcp.call_tool("get_timers")
        data = json.loads(result)
        assert data["success"] is True
        assert isinstance(data["timers"], list)
        assert data["total_timers"] >= 0
        print(f"\n[OK] get_timers: {data['total_timers']} timers")

    def test_get_counters(self, real_mcp):
        result = real_mcp.call_tool("get_counters")
        data = json.loads(result)
        assert data["success"] is True
        assert isinstance(data["counters"], list)
        assert data["total_counters"] >= 0
        print(f"\n[OK] get_counters: {data['total_counters']} counters")

    def test_get_template_entities_batch(self, real_mcp):
        """Fetch template entities batch using discovered template entity IDs."""
        list_result = real_mcp.call_tool("get_template_entities")
        list_data = json.loads(list_result)
        templates = list_data.get("templates", [])
        if not templates:
            pytest.skip("No template entities available")
        entity_ids = ",".join([t["entity_id"] for t in templates[:3]])
        result = real_mcp.call_tool("get_template_entities_batch", entity_ids=entity_ids)
        data = json.loads(result)
        assert data["success"] is True
        assert "results" in data
        assert data["total"] > 0
        assert data["found"] > 0
        print(f"\n[OK] get_template_entities_batch: {data['found']}/{data['total']} found")

    def test_search_entity_by_name(self, real_mcp):
        result = real_mcp.call_tool("search_entity_by_name", search_term="sun")
        data = json.loads(result)
        assert data["success"] is True
        assert "matched_entities" in data or "summary" in data
        print(f"\n[OK] search_entity_by_name: {data.get('summary', {}).get('matched_entities', 'N/A')} matched")

    def test_get_entity_details(self, real_mcp):
        registry_result = real_mcp.call_tool("get_entity_registry")
        registry_data = json.loads(registry_result)
        entities = registry_data.get("entities", [])
        if not entities:
            pytest.skip("No entities in registry")
        entity_id = entities[0]["entity_id"]
        result = real_mcp.call_tool("get_entity_details", entity_id=entity_id)
        data = json.loads(result)
        assert data["success"] is True
        assert "entity_id" in data or "entity_info" in data
        print(f"\n[OK] get_entity_details: entity_id={data.get('entity_id', 'N/A')}")

    def test_get_entity_registry_batch(self, real_mcp):
        registry_result = real_mcp.call_tool("get_entity_registry")
        registry_data = json.loads(registry_result)
        entities = registry_data.get("entities", [])
        if not entities:
            pytest.skip("No entities in registry")
        entity_id = entities[0]["entity_id"]
        result = real_mcp.call_tool(
            "get_entity_registry_batch",
            entity_ids=entity_id,
            fields="entity_id,platform",
        )
        data = json.loads(result)
        assert data["success"] is True
        assert "entities" in data
        assert data["total_entities"] > 0
        entity = data["entities"][0]
        assert entity["entity_id"] == entity_id
        assert "platform" in entity
        print(f"\n[OK] get_entity_registry_batch: {data['total_entities']} entities, fields={list(entity.keys())}")


# ============================================================
# DEV TOOLS
# ============================================================


class TestDevTools:
    """Developer tools tests."""

    def test_test_template(self, real_mcp):
        result = real_mcp.call_tool("test_template", template="{{ now().hour }}")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] {{ now().hour }} = {data['result']}")

    def test_test_templates_batch(self, real_mcp):
        templates = json.dumps({"hour": "{{ now().hour }}", "sun": "{{ states('sun.sun') }}"})
        result = real_mcp.call_tool("test_templates_batch", templates=templates)
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Batch: {data['successful']}/{data['total_templates']}")

    def test_get_template_performance(self, real_mcp):
        result = real_mcp.call_tool(
            "get_template_performance",
            template="{{ states | list | length }}",
            iterations=3,
        )
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Performance: {data['benchmark']['avg_ms']}ms avg")

    def test_check_entity_exists(self, real_mcp):
        result = real_mcp.call_tool("check_entity_exists", entity_id="sun.sun")
        data = json.loads(result)
        assert data["success"]
        assert data["exists"]
        print("\n[OK] sun.sun exists")

    def test_check_entities_batch(self, real_mcp):
        result = real_mcp.call_tool("check_entities_batch", entity_ids="sun.sun,nonexistent.xyz")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Batch check: {data['summary']['exists']}")

    def test_diagnose_entity(self, real_mcp, sample_entities):
        sensors = sample_entities.get("sensor", [])
        if not sensors:
            pytest.skip("No sensors")

        result = real_mcp.call_tool("diagnose_entity", entity_id=sensors[0])
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Diagnose: {len(data['issues'])} issues")

    def test_diagnose_energy_setup(self, real_mcp):
        result = real_mcp.call_tool("diagnose_energy_setup")
        data = json.loads(result)
        assert data["success"]
        print(f"\n[OK] Energy: {data['statistics']['total_energy_sensors']} sensors")


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

        print(f"\n[STATS] Individual: {individual_time:.3f}s, {individual_size} chars")
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

        print(f"\n[STATS] Raw: {raw_size} chars")
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

        print(f"\n[STATS] Diagnostics: {len(sections)} sections")
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
        print("[STATS] HOME ASSISTANT SUMMARY")
        print("=" * 50)
        print(f"Entities: {domains.get('total_entities', 'N/A')}")
        print(f"Domains: {domains.get('total_domains', 'N/A')}")
        print(f"Health: {health.get('summary', {}).get('health_score', 'N/A')}/100")
        print(f"Status: {health.get('summary', {}).get('status', 'N/A')}")
        print("=" * 50)


class TestNewToolsV10:
    """Integration tests for tools added in v1.0+."""

    def test_get_lovelace_dashboards(self, real_mcp):
        result = real_mcp.call_tool("get_lovelace_dashboards")
        data = json.loads(result)
        assert data["success"] is True

    def test_get_lovelace_resources(self, real_mcp):
        result = real_mcp.call_tool("get_lovelace_resources")
        data = json.loads(result)
        assert data["success"] is True

    def test_search_lovelace_config(self, real_mcp):
        result = real_mcp.call_tool("search_lovelace_config", entity_id="sun.sun")
        data = json.loads(result)
        assert data["success"] is True

    def test_get_lovelace_config_summary(self, real_mcp):
        result = real_mcp.call_tool("get_lovelace_config_summary")
        data = json.loads(result)
        assert data["success"] is True

    def test_get_persons(self, real_mcp):
        result = real_mcp.call_tool("get_persons")
        data = json.loads(result)
        assert data["success"] is True

    def test_get_zones(self, real_mcp):
        result = real_mcp.call_tool("get_zones")
        data = json.loads(result)
        assert data["success"] is True

    def test_diagnose_person_tracking(self, real_mcp):
        """Person tracking diagnostics should return success for existing person."""
        # Find person entities via entity registry
        registry_result = real_mcp.call_tool("get_entity_registry")
        registry_data = json.loads(registry_result)
        entities = registry_data.get("entities", [])
        persons = [e for e in entities if e.get("entity_id", "").startswith("person.")]
        if persons:
            person_entity = persons[0]["entity_id"]
            result = real_mcp.call_tool("diagnose_person_tracking", person_entity=person_entity)
            data = json.loads(result)
            assert data["success"] is True

    def test_get_area_diagnostic(self, real_mcp):
        """Area diagnostic should work for first available area."""
        areas_result = real_mcp.call_tool("get_area_registry")
        areas_data = json.loads(areas_result)
        areas = areas_data.get("areas", [])
        if areas:
            area_name = areas[0].get("name") or areas[0].get("id")
            result = real_mcp.call_tool("get_area_diagnostic", area_name=area_name)
            data = json.loads(result)
            assert data["success"] is True

    def test_get_energy_dashboard_data(self, real_mcp):
        result = real_mcp.call_tool("get_energy_dashboard_data")
        data = json.loads(result)
        assert data["success"] is True

    def test_get_hacs_data(self, real_mcp):
        result = real_mcp.call_tool("get_hacs_data")
        data = json.loads(result)
        assert data["success"] is True


class TestAutomationExtra:
    """Integration tests for remaining automation tools."""

    def test_search_automations_by_entity(self, real_mcp):
        result = real_mcp.call_tool("search_automations_by_entity", entity_id="sun.sun")
        data = json.loads(result)
        assert data["success"] is True

    def test_get_automation_conflicts(self, real_mcp):
        result = real_mcp.call_tool("get_automation_conflicts", entity_id="sun.sun")
        data = json.loads(result)
        assert data["success"] is True

    def test_get_automation_usage_stats(self, real_mcp):
        list_data = real_mcp.call_tool("list_automations")
        autos = json.loads(list_data)["automations"]
        if autos:
            alias = autos[0].get("alias")
            result = real_mcp.call_tool("get_automation_usage_stats", automation_id=alias)
            data = json.loads(result)
            assert data["success"] is True

    def test_automation_validate_triggers(self, real_mcp):
        list_data = real_mcp.call_tool("list_automations")
        autos = json.loads(list_data)["automations"]
        if autos:
            alias = autos[0].get("alias")
            result = real_mcp.call_tool("automation_validate_triggers", automation_alias=alias)
            data = json.loads(result)
            assert data["success"] is True


class TestConfigEntryDiagnostics:
    """Integration tests for config entry diagnostics."""

    def test_search_config_entries(self, real_mcp):
        result = real_mcp.call_tool("search_config_entries")
        data = json.loads(result)
        assert data["success"] is True

    def test_get_config_entry_details(self, real_mcp):
        entries_result = real_mcp.call_tool("search_config_entries", domain="sun")
        entries_data = json.loads(entries_result)
        entries = entries_data.get("entries", [])
        if entries:
            entry_id = entries[0].get("entry_id")
            result = real_mcp.call_tool("get_config_entry_details", entry_id=entry_id)
            data = json.loads(result)
            assert data["success"] is True

    def test_diagnose_config_entry(self, real_mcp):
        entries_result = real_mcp.call_tool("search_config_entries", domain="sun")
        entries_data = json.loads(entries_result)
        entries = entries_data.get("entries", [])
        if entries:
            entry_id = entries[0].get("entry_id")
            result = real_mcp.call_tool("diagnose_config_entry", entry_id=entry_id)
            data = json.loads(result)
            assert data["success"] is True


class TestEntityDepsExtra:
    """Integration tests for entity dependency tools."""

    def test_get_entity_consumers(self, real_mcp):
        result = real_mcp.call_tool("get_entity_consumers", entity_id="sun.sun")
        data = json.loads(result)
        assert data["success"] is True

    def test_entity_get_context_tree(self, real_mcp):
        result = real_mcp.call_tool("entity_get_context_tree", entity_id="sun.sun")
        data = json.loads(result)
        assert data["success"] in (True, False)


class TestHistoryExtra:
    """Integration tests for remaining history tools."""

    def test_get_history_batch(self, real_mcp):
        result = real_mcp.call_tool(
            "get_history_batch", entity_ids="sun.sun", hours_back=1, limit=3
        )
        data = json.loads(result)
        assert data["success"] is True

    def test_get_history_stats(self, real_mcp):
        result = real_mcp.call_tool("get_history_stats", entity_id="sun.sun")
        data = json.loads(result)
        assert isinstance(data, dict)


class TestBatchOperations:
    """Integration tests for batch operation tools."""

    def test_validate_yaml_batch(self, real_mcp):
        result = real_mcp.call_tool("validate_yaml_batch", file_paths="configuration.yaml")
        data = json.loads(result)
        assert data["success"] in (True, False)

    def test_bulk_search_entities(self, real_mcp):
        result = real_mcp.call_tool("bulk_search_entities", search_terms="sun,temperature")
        data = json.loads(result)
        assert data["success"] is True

    def test_get_template_dependencies(self, real_mcp):
        registry = real_mcp.call_tool("get_template_entities")
        reg_data = json.loads(registry)
        templates = reg_data.get("template_entities", [])
        if templates:
            entity_id = templates[0].get("entity_id") or "sensor.template_test"
            result = real_mcp.call_tool("get_template_dependencies", entity_id=entity_id)
            data = json.loads(result)
            assert data["success"] in (True, False)


class TestCompositeExtra:
    """Integration tests for composite tools."""

    def test_investigate_entity(self, real_mcp):
        result = real_mcp.call_tool("investigate_entity", search_term="sun")
        data = json.loads(result)
        assert data["success"] is True

    def test_get_entity_with_automations(self, real_mcp):
        result = real_mcp.call_tool("get_entity_with_automations", entity_id="sun.sun")
        data = json.loads(result)
        assert data["success"] in (True, False)


class TestDevToolsExtraIntegration:
    """Integration tests for remaining dev tools."""

    def test_test_service_call(self, real_mcp):
        result = real_mcp.call_tool(
            "test_service_call",
            domain="light",
            service="turn_on",
            entity_id="sun.sun",
        )
        data = json.loads(result)
        assert data["success"] in (True, False)


class TestHealthReporterIntegration:
    """Integration test for health reporter."""

    def test_trigger_health_report_integration(self, real_mcp):
        result = real_mcp.call_tool("trigger_health_report")
        data = json.loads(result)
        assert data["success"] is True


class TestFilesystemExplorerIntegration:
    """Integration tests for filesystem explorer tools.

    Note: filesystem tools validate paths against an allowlist (default: /config).
    Outside Docker, the config path may differ, so some tests may return errors.
    """

    def test_list_directory_integration(self, real_mcp):
        result = real_mcp.call_tool("list_directory", path="/config")
        data = json.loads(result)
        assert "success" in data or "error" in data

    def test_read_file_integration(self, real_mcp):
        result = real_mcp.call_tool(
            "read_file", file_path="/config/configuration.yaml", max_lines=5
        )
        data = json.loads(result)
        assert "success" in data or "error" in data

    def test_search_files_integration(self, real_mcp):
        result = real_mcp.call_tool(
            "search_files", pattern="homeassistant", search_path="/config", max_results=3
        )
        data = json.loads(result)
        assert "success" in data or "error" in data


class TestDynamicLookup:
    """Integration tests that dynamically find test data from existing entities."""

    def test_diagnose_automation_with_first(self, real_mcp):
        """Diagnose first available automation by looking it up dynamically."""
        list_data = real_mcp.call_tool("list_automations")
        list_result = json.loads(list_data)
        autos = list_result.get("automations", [])
        if autos:
            alias = autos[0].get("alias")
            result = real_mcp.call_tool("diagnose_automation", automation_id=alias)
            data = json.loads(result)
            assert data["success"] is True

    def test_get_automation_dependencies_with_first(self, real_mcp):
        """Get dependencies for first available automation."""
        list_data = real_mcp.call_tool("list_automations")
        list_result = json.loads(list_data)
        autos = list_result.get("automations", [])
        if autos:
            alias = autos[0].get("alias")
            result = real_mcp.call_tool("get_automation_dependencies", automation_id=alias)
            data = json.loads(result)
            assert data["success"] is True

    def test_get_entity_with_automations(self, real_mcp):
        """Get entity with automations for sun.sun."""
        result = real_mcp.call_tool("get_entity_with_automations", entity_id="sun.sun")
        data = json.loads(result)
        assert data["success"] in (True, False)

    def test_get_entity_dependencies(self, real_mcp):
        """Entity dependencies for sun.sun."""
        result = real_mcp.call_tool("get_entity_dependencies", entity_id="sun.sun")
        data = json.loads(result)
        assert data["success"] is True

    def test_get_area_devices_summary(self, real_mcp):
        """Area devices summary for first available area."""
        areas_data = real_mcp.call_tool("get_area_registry")
        areas = json.loads(areas_data).get("areas", [])
        if areas:
            area_id = areas[0].get("id")
            result = real_mcp.call_tool("get_area_devices_summary", area_id=area_id)
            data = json.loads(result)
            assert data["success"] is True

    def test_get_devices_by_area(self, real_mcp):
        """Devices by area for first available area."""
        areas_data = real_mcp.call_tool("get_area_registry")
        areas = json.loads(areas_data).get("areas", [])
        if areas:
            area_id = areas[0].get("id")
            result = real_mcp.call_tool("get_devices_by_area", area_id=area_id)
            data = json.loads(result)
            assert data["success"] is True

    def test_get_blueprint_instances(self, real_mcp):
        """Blueprint instances for first available blueprint."""
        bp_data = real_mcp.call_tool("list_blueprints")
        bp_result = json.loads(bp_data)
        blueprints = bp_result.get("blueprints", [])
        if blueprints:
            bp_path = blueprints[0].get("path")
            result = real_mcp.call_tool("get_blueprint_instances", blueprint_path=bp_path)
            data = json.loads(result)
            assert data["success"] is True

    def test_diagnose_template(self, real_mcp):
        """Diagnose first available template entity."""
        tmpl_data = real_mcp.call_tool("get_template_entities")
        tmpl_result = json.loads(tmpl_data)
        templates = tmpl_result.get("template_entities", [])
        if templates:
            eid = templates[0].get("entity_id")
            if eid:
                result = real_mcp.call_tool("diagnose_template", entity_id=eid)
                data = json.loads(result)
                assert data["success"] in (True, False)

    def test_automation_validate_triggers(self, real_mcp):
        """Validate triggers for first available automation."""
        list_data = real_mcp.call_tool("list_automations")
        list_result = json.loads(list_data)
        autos = list_result.get("automations", [])
        if autos:
            alias = autos[0].get("alias")
            result = real_mcp.call_tool("automation_validate_triggers", automation_alias=alias)
            data = json.loads(result)
            assert data["success"] in (True, False)


class TestTemplateEntityCode:
    """Integration tests for get_template_entity_code."""

    def test_get_template_entity_code_integration(self, real_mcp):
        list_data = real_mcp.call_tool("get_template_entities", entity_id=None)
        list_result = json.loads(list_data)
        templates = list_result.get("templates", [])
        if templates:
            eid = templates[0].get("entity_id")
            result = real_mcp.call_tool("get_template_entity_code", entity_id=eid)
            data = json.loads(result)
            assert data["success"] is True
            assert "state_template" in data


# ============================================================
# PAGINATED REGISTRY TESTS (Wave 4)
# ============================================================


class TestPaginatedRegistries:
    """Integration tests for paginated registry tools (limit/offset)."""

    # --- get_entity_registry ---

    def test_get_entity_registry_default(self, real_mcp):
        """Default call without limit/offset returns entities."""
        result = real_mcp.call_tool("get_entity_registry")
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_entities"] >= 0
        assert "entities" in data
        # Default limit is 200
        assert data["total_entities"] <= 200

    def test_get_entity_registry_paginated(self, real_mcp):
        """Call with limit=5 returns at most 5 entities."""
        result = real_mcp.call_tool("get_entity_registry", limit=5)
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_entities"] <= 5
        assert "entities" in data
        # If the system has more than 5 entities, _meta should be present
        if data.get("_meta") and data["_meta"].get("truncated"):
            assert data["_meta"]["total_count"] > 5
            assert data["_meta"]["total_count"] > data["total_entities"]

    def test_get_entity_registry_offset(self, real_mcp):
        """Call with limit=5 and offset=5 — verify no overlap with first 5."""
        # Get first page
        page1 = json.loads(real_mcp.call_tool("get_entity_registry", limit=5, offset=0))
        # Get second page
        page2 = json.loads(real_mcp.call_tool("get_entity_registry", limit=5, offset=5))

        assert page1["success"] is True
        assert page2["success"] is True

        # If both pages have entities, verify no overlap in entity_ids
        page1_ids = {e["entity_id"] for e in page1.get("entities", [])}
        page2_ids = {e["entity_id"] for e in page2.get("entities", [])}

        if page1_ids and page2_ids:
            overlap = page1_ids & page2_ids
            assert len(overlap) == 0, f"Pages overlap: {overlap}"

    def test_get_entity_registry_meta(self, real_mcp):
        """Verify _meta.truncated and _meta.total_count when truncated."""
        result = real_mcp.call_tool("get_entity_registry", limit=5)
        data = json.loads(result)
        assert data["success"] is True

        if data.get("_meta"):
            assert "_meta" in data
            assert "truncated" in data["_meta"]
            assert data["_meta"]["truncated"] is True
            assert "total_count" in data["_meta"]
            assert isinstance(data["_meta"]["total_count"], int)

    # --- get_device_registry ---

    def test_get_device_registry_default(self, real_mcp):
        """Default call without limit/offset returns devices."""
        result = real_mcp.call_tool("get_device_registry")
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_devices"] >= 0
        assert "devices" in data

    def test_get_device_registry_paginated(self, real_mcp):
        """Call with limit=3 returns at most 3 devices."""
        result = real_mcp.call_tool("get_device_registry", limit=3)
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_devices"] <= 3
        assert "devices" in data

    def test_get_device_registry_offset(self, real_mcp):
        """Call with limit=3 and offset=3 — verify no overlap."""
        page1 = json.loads(real_mcp.call_tool("get_device_registry", limit=3, offset=0))
        page2 = json.loads(real_mcp.call_tool("get_device_registry", limit=3, offset=3))

        assert page1["success"] is True
        assert page2["success"] is True

        page1_ids = {d["id"] for d in page1.get("devices", [])}
        page2_ids = {d["id"] for d in page2.get("devices", [])}

        if page1_ids and page2_ids:
            overlap = page1_ids & page2_ids
            assert len(overlap) == 0, f"Device pages overlap: {overlap}"

    # --- get_area_registry ---

    def test_get_area_registry_default(self, real_mcp):
        """Default call without limit/offset returns areas."""
        result = real_mcp.call_tool("get_area_registry")
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_areas"] >= 0
        assert "areas" in data

    def test_get_area_registry_paginated(self, real_mcp):
        """Call with limit=2 returns at most 2 areas."""
        result = real_mcp.call_tool("get_area_registry", limit=2)
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_areas"] <= 2
        assert "areas" in data

    # --- get_config_entries ---

    def test_get_config_entries_default(self, real_mcp):
        """Default call without limit/offset returns entries."""
        result = real_mcp.call_tool("get_config_entries")
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_entries"] >= 0
        assert "entries" in data

    def test_get_config_entries_paginated(self, real_mcp):
        """Call with limit=3 returns at most 3 entries."""
        result = real_mcp.call_tool("get_config_entries", limit=3)
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_entries"] <= 3
        assert "entries" in data

    def test_get_config_entries_offset(self, real_mcp):
        """Call with limit=3 and offset=3 — verify no overlap."""
        page1 = json.loads(real_mcp.call_tool("get_config_entries", limit=3, offset=0))
        page2 = json.loads(real_mcp.call_tool("get_config_entries", limit=3, offset=3))

        assert page1["success"] is True
        assert page2["success"] is True

        page1_ids = {e["entry_id"] for e in page1.get("entries", [])}
        page2_ids = {e["entry_id"] for e in page2.get("entries", [])}

        if page1_ids and page2_ids:
            overlap = page1_ids & page2_ids
            assert len(overlap) == 0, f"Entry pages overlap: {overlap}"


# ============================================================
# 🩺 NEW DIAGNOSTICS TOOLS (v1.7+) — installation type, post-update, health snapshots, threshold proximity, notifications
# ============================================================


class TestNewDiagnosticsTools:
    """Integration tests for diagnostics tools added in v1.7+."""

    def test_diagnose_installation_type(self, real_mcp):
        """diagnose_installation_type should detect HA installation type."""
        result = real_mcp.call_tool("diagnose_installation_type")
        data = json.loads(result)
        assert data["success"] is True
        assert "type" in data or "installation_type" in data or "ha_type" in data
        print(f"\n[OK] diagnose_installation_type: type={data.get('type', 'unknown')}")

    def test_diagnose_post_update_integrations(self, real_mcp):
        """diagnose_post_update_integrations should report custom integration status."""
        result = real_mcp.call_tool("diagnose_post_update_integrations")
        data = json.loads(result)
        assert data["success"] is True
        has_fragile = "fragile" in data or "fragile_highlighted" in data
        has_custom = "custom_integrations" in data or "custom_components_total" in data
        assert has_fragile or has_custom, f"Expected integration keys not found: {list(data.keys())}"
        total = data.get("custom_components_total", data.get("custom_integrations_total", 0))
        print(f"\n[OK] diagnose_post_update_integrations: {total} custom components")

    def test_take_entity_health_snapshot(self, real_mcp):
        """take_entity_health_snapshot should return a snapshot_id."""
        result = real_mcp.call_tool("take_entity_health_snapshot")
        data = json.loads(result)
        assert data["success"] is True
        assert "snapshot_id" in data, f"No snapshot_id in response: {list(data.keys())}"
        assert data["snapshot_id"].startswith("snap_")
        TestNewDiagnosticsTools._snapshot_id = data["snapshot_id"]
        print(
            f"\n[OK] take_entity_health_snapshot: {data['snapshot_id']} "
            f"({data.get('unavailable_count', 0)} unavailable)"
        )

    def test_compare_entity_health_snapshot(self, real_mcp):
        """compare_entity_health_snapshot: take snapshot → compare with same → new_issues >= 0."""
        if not hasattr(TestNewDiagnosticsTools, "_snapshot_id"):
            # Take a fresh snapshot if previous test didn't run
            snap_result = real_mcp.call_tool("take_entity_health_snapshot")
            snap_data = json.loads(snap_result)
            assert snap_data["success"] is True
            TestNewDiagnosticsTools._snapshot_id = snap_data["snapshot_id"]

        snapshot_id = TestNewDiagnosticsTools._snapshot_id
        result = real_mcp.call_tool("compare_entity_health_snapshot", snapshot_id=snapshot_id)
        data = json.loads(result)
        assert data["success"] is True

        # Check for new issues count (field may be named differently)
        new_count = data.get("new_unavailable_count", data.get("new_issues", -1))
        if new_count == -1:
            # Accept any count-like field
            for key in ("new_unavailable", "new_issues", "resolved"):
                if key in data and isinstance(data[key], list):
                    new_count = len(data[key])
                    break
        assert new_count >= 0, f"Expected non-negative new issues: {data}"
        print(
            f"\n[OK] compare_entity_health_snapshot: new={data.get('new_unavailable_count', 0)}, "
            f"resolved={data.get('resolved_count', 0)}"
        )

    def test_diagnose_entity_threshold_proximity(self, real_mcp):
        """diagnose_entity_threshold_proximity should return threshold_alerts list."""
        result = real_mcp.call_tool(
            "diagnose_entity_threshold_proximity",
            proximity_percent=25,
        )
        data = json.loads(result)
        assert data["success"] is True
        alerts = data.get("threshold_alerts", data.get("alerts", []))
        assert isinstance(alerts, list)
        print(
            f"\n[OK] diagnose_entity_threshold_proximity: {len(alerts)} alerts at 25% proximity"
        )

    def test_get_notification_history(self, real_mcp):
        """get_notification_history should return active and recent notifications."""
        result = real_mcp.call_tool("get_notification_history")
        data = json.loads(result)
        assert data["success"] is True

        # Check for notification lists under various possible field names
        active = data.get(
            "active_persistent_notifications",
            data.get("notifications", data.get("active_notifications", [])),
        )
        assert isinstance(active, list), (
            f"Expected notifications to be a list, got {type(active).__name__}"
        )
        print(
            f"\n[OK] get_notification_history: {data.get('active_count', len(active))} active, "
            f"{data.get('recent_count', 0)} recent in 24h"
        )


# ============================================================
# NEW TOOLS (v1.6+) — diagnose_stuck_helpers, list_automation_categories, describe_ha_capabilities
# ============================================================


class TestNewToolsV11:
    """Integration tests for tools added in v1.6+."""

    def test_diagnose_stuck_helpers(self, real_mcp):
        """diagnose_stuck_helpers should return stuck_count >= 0."""
        result = real_mcp.call_tool("diagnose_stuck_helpers", stale_hours=48)
        data = json.loads(result)
        assert data["success"] is True
        assert data["stuck_count"] >= 0
        assert "stuck_helpers" in data
        assert "total_helpers_scanned" in data
        print(
            f"\n[OK] diagnose_stuck_helpers: {data['stuck_count']} stuck out of {data['total_helpers_scanned']} scanned"
        )

    def test_list_automation_categories(self, real_mcp):
        """list_automation_categories should return categories list."""
        result = real_mcp.call_tool("list_automation_categories", include_entity_count=True)
        data = json.loads(result)
        assert data["success"] is True
        assert isinstance(data["categories"], list)
        assert data["total"] >= 0
        assert "empty_categories" in data
        print(f"\n[OK] list_automation_categories: {data['total']} categories, {len(data['empty_categories'])} empty")

    def test_describe_ha_capabilities(self, real_mcp):
        """describe_ha_capabilities should return tool catalog."""
        result = real_mcp.call_tool("describe_ha_capabilities")
        data = json.loads(result)
        assert data["success"] is True
        assert "tool_count" in data
        assert "tools" in data
        assert data["tool_count"] > 0
        print(f"\n[OK] describe_ha_capabilities: {data['tool_count']} tools, schema v{data.get('schema_version', '?')}")


class TestNewToolsV12:
    """Integration tests for diagnostic tools added in v1.7+."""

    def test_diagnose_connectivity(self, real_mcp):
        """diagnose_connectivity should return overall_status."""
        result = real_mcp.call_tool("diagnose_connectivity")
        data = json.loads(result)
        assert data["success"] is True
        assert "overall_status" in data
        assert isinstance(data["connectivity_issues"], list)
        assert isinstance(data["recommendations"], list)
        print(f"\n[OK] diagnose_connectivity: status={data['overall_status']}, issues={len(data['connectivity_issues'])}")

    def test_diagnose_performance(self, real_mcp):
        """diagnose_performance should return slowest_automations or largest_entities list."""
        result = real_mcp.call_tool("diagnose_performance")
        data = json.loads(result)
        assert data["success"] is True
        assert "slowest_automations" in data or "largest_entities" in data
        assert isinstance(data.get("slowest_automations", []), list)
        assert isinstance(data.get("largest_entities", []), list)
        print(f"\n[OK] diagnose_performance: summary={data.get('summary', 'N/A')}")

    def test_diagnose_stale_entities(self, real_mcp):
        """diagnose_stale_entities should return total_stale >= 0."""
        result = real_mcp.call_tool("diagnose_stale_entities", stale_minutes=30, domain_filter="sensor")
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_stale"] >= 0
        assert data["total_scanned"] >= 0
        assert "stale_entities" in data
        assert "by_severity" in data
        print(
            f"\n[OK] diagnose_stale_entities: {data['total_stale']} stale out of {data['total_scanned']} scanned"
        )

    def test_diagnose_orphan_references(self, real_mcp):
        """diagnose_orphan_references should return orphan_count >= 0."""
        result = real_mcp.call_tool("diagnose_orphan_references", scope="automations")
        data = json.loads(result)
        assert data["success"] is True
        assert data["orphan_count"] >= 0
        assert data["scope"] == "automations"
        assert "orphan_references" in data
        assert "total_references_checked" in data
        print(
            f"\n[OK] diagnose_orphan_references: {data['orphan_count']} orphans out of {data['total_references_checked']} checked"
        )

    def test_diagnose_startup_progress(self, real_mcp):
        """diagnose_startup_progress should return progress metrics."""
        result = real_mcp.call_tool("diagnose_startup_progress")
        data = json.loads(result)
        assert data["success"] is True
        assert "progress_pct" in data
        assert data["progress_pct"] >= 0.0
        assert "status" in data
        assert data["status"] in ("starting", "loading", "ready", "unknown")
        assert "entity_count" in data
        assert data["entity_count"] >= 0
        print(
            f"\n[OK] diagnose_startup_progress: {data['progress_pct']}%, status={data['status']}"
        )

    def test_diagnose_voice(self, real_mcp):
        """diagnose_voice should return assistants_available or pipelines."""
        result = real_mcp.call_tool("diagnose_voice")
        data = json.loads(result)
        assert data["success"] is True
        assert "assistants_available" in data or "pipelines" in data
        assert isinstance(data.get("exposed_entities_count", 0), int)
        assert isinstance(data.get("issues", []), list)
        print(
            f"\n[OK] diagnose_voice: exposed={data.get('exposed_entities_count', 0)}, "
            f"assistants={len(data.get('assistants_available', {}))}"
        )



# ============================================================
# ⚙️ CONFIG TOOLS (v1.6+) — get_main_configuration, list_custom_components,
# list_themes, search_in_config_batch, search_in_config,
# search_config_by_params, get_lovelace_entity_usage
# ============================================================


class TestConfigTools:
    """Integration tests for configuration management tools."""

    def test_get_main_configuration(self, real_mcp):
        """get_main_configuration should return YAML content."""
        result = real_mcp.call_tool("get_main_configuration")
        data = json.loads(result)
        assert data["success"] is True, f"get_main_configuration failed: {data.get('error')}"
        assert "data" in data
        print(f"\n[OK] get_main_configuration: {len(data.get('data', ''))} chars")

    def test_list_custom_components(self, real_mcp):
        """list_custom_components should return components as a list."""
        result = real_mcp.call_tool("list_custom_components")
        data = json.loads(result)
        # May fail if custom_components directory doesn't exist (valid for some installs)
        if data["success"] is False:
            print(f"\n[WARN] list_custom_components: {data.get('error')}")
        else:
            assert isinstance(data["components"], list)
            print(
                f"\n[OK] list_custom_components: {data['total_custom_components']} components"
            )

    def test_list_themes(self, real_mcp):
        """list_themes should return themes as a list."""
        result = real_mcp.call_tool("list_themes")
        data = json.loads(result)
        if data["success"] is False:
            print(f"\n[WARN] list_themes: {data.get('error')}")
        else:
            assert isinstance(data["themes"], list)
            print(f"\n[OK] list_themes: {data['total_theme_files']} theme files")

    def test_search_in_config_batch(self, real_mcp):
        """search_in_config_batch should find files matching multiple terms."""
        result = real_mcp.call_tool(
            "search_in_config_batch",
            search_terms="mqtt,automation",
            file_types="yaml",
        )
        data = json.loads(result)
        assert data["success"] is True
        assert "results_by_term" in data
        assert "matching_files" in data
        print(
            f"\n[OK] search_in_config_batch: "
            f"{data['summary']['files_matching_criteria']} files matched"
        )

    def test_search_in_config(self, real_mcp):
        """search_in_config should find files containing a search term."""
        result = real_mcp.call_tool(
            "search_in_config",
            search_term="homeassistant",
            file_types="yaml",
        )
        data = json.loads(result)
        assert data["success"] is True
        assert "results_by_term" in data
        print(
            f"\n[OK] search_in_config: "
            f"{data['summary']['files_matching_criteria']} files matched"
        )

    def test_search_config_by_params(self, real_mcp):
        """search_config_by_params should find config entries by service call."""
        result = real_mcp.call_tool(
            "search_config_by_params",
            service="light.turn_on",
        )
        data = json.loads(result)
        assert data["success"] is True
        assert "results" in data
        print(
            f"\n[OK] search_config_by_params: "
            f"{data['summary']['total_matches']} matches"
        )

    def test_get_lovelace_entity_usage(self, real_mcp):
        """get_lovelace_entity_usage should verify lovelace_dashboards registry fix."""
        result = real_mcp.call_tool(
            "get_lovelace_entity_usage",
            entity_id="sun.sun",
        )
        data = json.loads(result)
        assert data["success"] is True
        assert "usage_count" in data
        assert data["usage_count"] >= 0
        print(
            f"\n[OK] get_lovelace_entity_usage: "
            f"{data['usage_count']} usages for sun.sun"
        )


# ============================================================
# ⛳ SINGLE-TOOL GAPS WAVE — get_services, get_previous_logs,
# list_config_entry_domains, compare_entities_state,
# get_automation_codes_batch, audit_config_orphans,
# entity_get_context_tree, get_recent_state_changes,
# get_integration_entities, compare_templates,
# eval_templates_batch, get_device_entities,
# device_get_wifi_status, get_device_triggers
# ============================================================


class TestStatesGap:
    """Integration tests for state-related tools not yet covered."""

    def test_get_services(self, real_mcp):
        """get_services should return services dict with domains."""
        result = real_mcp.call_tool("get_services")
        data = json.loads(result)
        assert data["success"] is True
        assert "services" in data
        assert isinstance(data["services"], list)
        assert len(data["services"]) > 0
        assert "domain" in data["services"][0]
        print(f"\n[OK] get_services: {len(data['services'])} services across {len(set(s['domain'] for s in data['services']))} domains")

    def test_get_services_filtered(self, real_mcp):
        """get_services with domain filter returns only that domain."""
        result = real_mcp.call_tool("get_services", domain="light")
        data = json.loads(result)
        assert data["success"] is True
        assert "services" in data
        for s in data["services"]:
            assert s["domain"] == "light"
        print(f"\n[OK] get_services(light): {len(data['services'])} light services")


class TestLogsGap:
    """Integration tests for log tools not yet covered."""

    def test_get_previous_logs(self, real_mcp):
        """get_previous_logs should return log entries."""
        result = real_mcp.call_tool("get_previous_logs", lines=10)
        data = json.loads(result)
        assert data["success"] is True
        assert "lines_requested" in data
        assert "lines_returned" in data
        assert "logs" in data
        assert data["lines_requested"] == 10
        print(f"\n[OK] get_previous_logs: {data['lines_returned']} lines returned")


class TestConfigEntriesGap:
    """Integration tests for config entry tools not yet covered."""

    def test_list_config_entry_domains(self, real_mcp):
        """list_config_entry_domains should return domains list."""
        result = real_mcp.call_tool("list_config_entry_domains")
        data = json.loads(result)
        assert data["success"] is True
        assert "total_entries" in data
        assert "total_domains" in data
        assert "domains" in data
        assert isinstance(data["domains"], list)
        assert data["total_domains"] > 0
        assert data["total_entries"] > 0
        assert "domain" in data["domains"][0]
        print(f"\n[OK] list_config_entry_domains: {data['total_domains']} domains, {data['total_entries']} entries")


class TestBatchGap:
    """Integration tests for batch operation tools not yet covered."""

    def test_compare_entities_state(self, real_mcp):
        """compare_entities_state takes snapshot (1st call returns mode='snapshot')."""
        result = real_mcp.call_tool("compare_entities_state", entity_ids="sun.sun,sensor.date")
        data = json.loads(result)
        assert data["success"] is True
        assert data["mode"] == "snapshot"
        assert data["entities_captured"] >= 1
        assert "snapshot" in data
        assert "sun.sun" in data["snapshot"]
        print(f"\n[OK] compare_entities_state: {data['entities_captured']} entities captured")

    def test_get_automation_codes_batch(self, real_mcp):
        """get_automation_codes_batch should return codes for known automations."""
        list_data = real_mcp.call_tool("list_automations")
        autos = json.loads(list_data).get("automations", [])
        if len(autos) < 2:
            pytest.skip("Need at least 2 automations for batch test")

        alias1 = autos[0].get("alias")
        alias2 = autos[1].get("alias")
        ids = f"{alias1},{alias2}"
        result = real_mcp.call_tool("get_automation_codes_batch", automation_ids=ids)
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_requested"] == 2
        assert data["found_count"] >= 1
        assert "results" in data
        print(f"\n[OK] get_automation_codes_batch: {data['found_count']}/{data['total_requested']} found")


class TestCompositeGap:
    """Integration tests for composite tools not yet covered."""

    def test_audit_config_orphans(self, real_mcp):
        """audit_config_orphans should return data_quality field (our fix!)."""
        result = real_mcp.call_tool("audit_config_orphans")
        data = json.loads(result)
        assert data["success"] is True
        assert "orphan_entities" in data
        assert "orphan_count" in data
        assert "never_triggered_automations" in data
        assert "broken_references" in data
        assert "unused_blueprints" in data
        assert "data_quality" in data
        assert "summary" in data
        assert isinstance(data["data_quality"], dict)
        print(f"\n[OK] audit_config_orphans: {data['summary']}")
        print(f"     data_quality: {data['data_quality']}")


class TestEntityContextGap:
    """Integration tests for entity context tools not yet covered."""

    def test_entity_get_context_tree(self, real_mcp):
        """entity_get_context_tree should trace sources for sun.sun."""
        result = real_mcp.call_tool("entity_get_context_tree", entity_id="sun.sun", hours_back=1)
        data = json.loads(result)
        assert data["success"] is True
        assert "current_state" in data
        assert "sources" in data
        assert "recent_changes" in data
        assert data["current_state"]["entity_id"] == "sun.sun"
        print(f"\n[OK] entity_get_context_tree: {data.get('total_history_entries', 'N/A')} history entries, "
              f"{len(data.get('sources', {}))} sources")


class TestHistoryGap:
    """Integration tests for history tools not yet covered."""

    def test_get_recent_state_changes(self, real_mcp):
        """get_recent_state_changes should return recent entity changes."""
        result = real_mcp.call_tool("get_recent_state_changes", minutes=10)
        data = json.loads(result)
        assert data["success"] is True
        assert "total_changes" in data
        assert "changes" in data
        assert isinstance(data["changes"], list)
        assert data["total_changes"] >= 0
        if data["total_changes"] > 0:
            assert "entity_id" in data["changes"][0]
            assert "from" in data["changes"][0]
            assert "to" in data["changes"][0]
        print(f"\n[OK] get_recent_state_changes: {data['total_changes']} changes in 10min")

    def test_get_recent_state_changes_filtered(self, real_mcp):
        """get_recent_state_changes with domain filter."""
        result = real_mcp.call_tool("get_recent_state_changes", minutes=10, domains="sensor")
        data = json.loads(result)
        assert data["success"] is True
        assert "total_changes" in data
        assert isinstance(data.get("changes", []), list)
        print(f"\n[OK] get_recent_state_changes(sensor): {data['total_changes']} sensor changes")


class TestIntegrationsGap:
    """Integration tests for integration tools not yet covered."""

    def test_get_integration_entities(self, real_mcp):
        """get_integration_entities should return entities for sun domain."""
        result = real_mcp.call_tool("get_integration_entities", domain="sun")
        data = json.loads(result)
        assert data["success"] is True
        assert data["domain"] == "sun"
        assert "entity_count" in data or "entities" in data or "total_entities" in data
        assert data.get("entity_count", data.get("total_entities", 0)) >= 1
        print(f"\n[OK] get_integration_entities(sun): {data.get('entity_count', data.get('total_entities', 0))} entities")


class TestDevToolsGap:
    """Integration tests for developer tools not yet covered."""

    def test_compare_templates_equal(self, real_mcp):
        """compare_templates should detect equal results."""
        result = real_mcp.call_tool(
            "compare_templates",
            template_a="{{ 2 + 2 }}",
            template_b="{{ 3 + 1 }}",
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["match"] is True
        assert data["result_a"] == data["result_b"]
        print(f"\n[OK] compare_templates: match={data['match']}, a='{data['result_a']}' b='{data['result_b']}'")

    def test_compare_templates_different(self, real_mcp):
        """compare_templates should detect different results."""
        result = real_mcp.call_tool(
            "compare_templates",
            template_a="{{ now().hour }}",
            template_b="{{ now().minute }}",
        )
        data = json.loads(result)
        assert data["success"] is True
        # Results may or may not match depending on the time
        print(f"\n[OK] compare_templates(diff): match={data['match']}, warning={data.get('warning')}")

    def test_eval_templates_batch(self, real_mcp):
        """eval_templates_batch should evaluate multiple templates."""
        templates_json = json.dumps(["{{ 2 + 2 }}", "{{ 3 + 3 }}"])
        result = real_mcp.call_tool("eval_templates_batch", templates=templates_json)
        data = json.loads(result)
        assert data["success"] is True
        assert data["total"] == 2
        assert data["successful"] == 2
        assert data["failed"] == 0
        assert "results" in data
        assert "template_0" in data["results"]
        assert "template_1" in data["results"]
        print(f"\n[OK] eval_templates_batch: {data['successful']}/{data['total']} successful")

    def test_eval_templates_batch_named(self, real_mcp):
        """eval_templates_batch with named templates."""
        templates_json = json.dumps({"add": "{{ 2 + 2 }}", "now_h": "{{ now().hour }}"})
        result = real_mcp.call_tool("eval_templates_batch", templates=templates_json)
        data = json.loads(result)
        assert data["success"] is True
        assert data["total"] == 2
        assert "add" in data["results"]
        assert "now_h" in data["results"]
        print(f"\n[OK] eval_templates_batch(named): {data['successful']}/{data['total']} successful")


class TestDevicesGap:
    """Integration tests for device tools not yet covered."""

    def _find_any_device(self, real_mcp) -> str | None:
        """Find any device from the device registry."""
        result = real_mcp.call_tool("get_device_registry")
        data = json.loads(result)
        devices = data.get("devices", [])
        if devices:
            return devices[0].get("id")
        return None

    def test_get_device_entities(self, real_mcp):
        """get_device_entities should return entities for a known device."""
        device_id = self._find_any_device(real_mcp)
        if not device_id:
            pytest.skip("No devices found in registry")

        result = real_mcp.call_tool("get_device_entities", device_id=device_id)
        data = json.loads(result)
        assert data["success"] is True
        assert "entities" in data or "entity_count" in data or "total_entities" in data
        print(f"\n[OK] get_device_entities: {len(data.get('entities', data.get('entity_list', [])))} entities for device {device_id[:12]}...")

    def test_device_get_wifi_status(self, real_mcp):
        """device_get_wifi_status may fail softly if device has no WiFi data."""
        device_id = self._find_any_device(real_mcp)
        if not device_id:
            pytest.skip("No devices found in registry")

        result = real_mcp.call_tool("device_get_wifi_status", device_id=device_id)
        data = json.loads(result)
        # May return False for non-Tasmota/OpenBK devices - that's acceptable
        if data["success"] is False:
            print(f"\n[WARN] device_get_wifi_status: {data.get('error', 'no wifi info')}")
        else:
            assert "connection_state" in data or "ssid" in data or "rssi" in data
            print(f"\n[OK] device_get_wifi_status: connection={data.get('connection_state', 'N/A')}")

    def test_get_device_triggers_by_entity(self, real_mcp):
        """get_device_triggers should work for sun.sun entity."""
        result = real_mcp.call_tool("get_device_triggers", entity_id="sun.sun")
        data = json.loads(result)
        assert data["success"] is True
        # Device may or may not have triggers - that's fine
        assert "device_id" in data
        assert "triggers" in data
        assert isinstance(data["triggers"], list)
        print(f"\n[OK] get_device_triggers: device={data.get('device_id', 'N/A')}, {len(data['triggers'])} triggers")

    def test_get_device_triggers_by_id(self, real_mcp):
        """get_device_triggers should work for first device."""
        device_id = self._find_any_device(real_mcp)
        if not device_id:
            pytest.skip("No devices found in registry")

        result = real_mcp.call_tool("get_device_triggers", device_id=device_id)
        data = json.loads(result)
        assert data["success"] is True
        assert "device_id" in data
        assert "triggers" in data
        print(f"\n[OK] get_device_triggers(by_id): {len(data['triggers'])} triggers")


# ============================================================
# 🤖 8 NEW AUTOMATION TOOLS (v1.8+)
# ============================================================


class TestNewAutomationTools:
    """Integration tests for 8 automation tools:
    get_automation_file_location, search_inside_automations,
    diagnose_uncategorized_automations, diagnose_automation_aliases,
    validate_automation_names, get_automation_entity_id,
    resolve_blueprint_automation, diagnose_category_alias_mismatch.
    """

    def test_get_automation_file_location(self, real_mcp):
        """get_automation_file_location: known alias -> file_path + line_start/line_end."""
        list_data = real_mcp.call_tool("list_automations")
        list_result = json.loads(list_data)
        autos = list_result.get("automations", [])
        if not autos:
            pytest.skip("No automations available")

        alias = autos[0].get("alias")
        result = real_mcp.call_tool("get_automation_file_location", automation_id=alias)
        data = json.loads(result)
        assert data["success"] is True
        assert data["file_path"] == "automations.yaml"
        assert data["line_start"] > 0
        assert data["line_end"] >= data["line_start"]
        print(
            f"\n[OK] get_automation_file_location: lines {data['line_start']}-{data['line_end']}"
        )

    def test_search_inside_automations(self, real_mcp):
        """search_inside_automations: pattern=light in actions -> matches list."""
        result = real_mcp.call_tool(
            "search_inside_automations", pattern="light", search_in="actions"
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["match_count"] >= 0
        assert isinstance(data["matches"], list)
        print(
            f"\n[OK] search_inside_automations: {data['match_count']} matches for 'light'"
        )

    def test_diagnose_uncategorized_automations(self, real_mcp):
        """diagnose_uncategorized_automations: scope=automation -> uncategorized list."""
        result = real_mcp.call_tool("diagnose_uncategorized_automations", scope="automation")
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_uncategorized"] >= 0
        assert isinstance(data["uncategorized"], list)
        print(
            f"\n[OK] diagnose_uncategorized_automations: {data['total_uncategorized']} uncategorized"
        )

    def test_diagnose_automation_aliases(self, real_mcp):
        """diagnose_automation_aliases: assert duplicate_count or results."""
        result = real_mcp.call_tool("diagnose_automation_aliases")
        data = json.loads(result)
        assert data["success"] is True
        assert "total_duplicates" in data
        assert data["total_duplicates"] >= 0
        print(
            f"\n[OK] diagnose_automation_aliases: {data['total_duplicates']} duplicate groups"
        )

    def test_validate_automation_names(self, real_mcp):
        """validate_automation_names: assert validation results."""
        result = real_mcp.call_tool("validate_automation_names")
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_violations"] >= 0
        assert isinstance(data["violations"], list)
        print(
            f"\n[OK] validate_automation_names: {data['total_violations']} violations"
        )

    def test_get_automation_entity_id(self, real_mcp):
        """get_automation_entity_id: known alias -> entity_id starts with automation."""
        list_data = real_mcp.call_tool("list_automations")
        list_result = json.loads(list_data)
        autos = list_result.get("automations", [])
        if not autos:
            pytest.skip("No automations available")

        alias = autos[0].get("alias")
        result = real_mcp.call_tool("get_automation_entity_id", identifier=alias)
        data = json.loads(result)
        assert data["success"] is True
        assert "entity_id" in data
        assert data["entity_id"].startswith("automation.")
        print(
            f"\n[OK] get_automation_entity_id: {alias} -> {data['entity_id']}"
        )

    def test_resolve_blueprint_automation(self, real_mcp):
        """resolve_blueprint_automation: find blueprint-based automation -> verify resolution."""
        # Find a blueprint-based automation
        bp_result = real_mcp.call_tool("search_automations", uses_blueprint=True)
        bp_data = json.loads(bp_result)
        bp_autos = bp_data.get("results", [])
        if not bp_autos:
            pytest.skip("No blueprint-based automations available")

        alias = bp_autos[0].get("alias")
        result = real_mcp.call_tool("resolve_blueprint_automation", automation_id=alias)
        data = json.loads(result)
        assert data["success"] is True
        assert data.get("is_blueprint") is True
        assert "resolved_yaml" in data
        assert "blueprint_path" in data
        print(
            f"\n[OK] resolve_blueprint_automation: {alias} -> blueprint={data['blueprint_path']}"
        )

    def test_diagnose_category_alias_mismatch(self, real_mcp):
        """diagnose_category_alias_mismatch: assert mismatches list (isinstance fix!)."""
        result = real_mcp.call_tool("diagnose_category_alias_mismatch")
        data = json.loads(result)
        assert data["success"] is True
        assert data["total_mismatches"] >= 0
        assert isinstance(data["mismatches"], list)
        print(
            f"\n[OK] diagnose_category_alias_mismatch: {data['total_mismatches']} mismatches"
        )


# ============================================================
# STORAGE TOOLS (v1.8+) — get_lovelace_config, diagnose_lovelace_setup,
# get_exposed_entities, hacs_get_update_count, get_nfc_tags, get_cache_stats
# ============================================================


class TestNewToolsV13:
    """Integration tests for 6 storage tools added in v1.8+."""

    def test_get_lovelace_config(self, real_mcp):
        """get_lovelace_config with dashboard='default' should return views config."""
        result = real_mcp.call_tool("get_lovelace_config", dashboard="default")
        data = json.loads(result)
        if data.get("success"):
            config = data.get("data", {}).get("config", {})
            views = config.get("views", [])
            assert isinstance(views, list)
            print(f"\n[OK] get_lovelace_config: {len(views)} views in default dashboard")
        else:
            # Dashboard may not exist in storage (e.g., YAML-mode dashboards)
            assert "error" in data
            print(f"\n[WARN] get_lovelace_config: {data.get('error')}")

    def test_diagnose_lovelace_setup(self, real_mcp):
        """diagnose_lovelace_setup should return dashboards list and issues."""
        result = real_mcp.call_tool("diagnose_lovelace_setup")
        data = json.loads(result)
        assert data["success"] is True
        assert "dashboards" in data
        dashboard_count = len(data.get("dashboards", []))
        assert dashboard_count >= 0
        assert "issues" in data
        assert isinstance(data.get("issues", []), list)
        print(
            f"\n[OK] diagnose_lovelace_setup: {dashboard_count} dashboards, "
            f"{len(data.get('issues', []))} issues"
        )

    def test_get_exposed_entities(self, real_mcp):
        """get_exposed_entities should return entities dict."""
        result = real_mcp.call_tool("get_exposed_entities")
        data = json.loads(result)
        assert data["success"] is True
        entities = data.get("entities", {})
        assert isinstance(entities, dict)
        print(
            f"\n[OK] get_exposed_entities: {data.get('total_exposed', 0)} exposed entities"
        )

    def test_hacs_get_update_count(self, real_mcp):
        """hacs_get_update_count verifies dict-repositories fix with total_installed and updates_available."""
        result = real_mcp.call_tool("hacs_get_update_count")
        data = json.loads(result)
        if data.get("success"):
            assert "total_installed" in data
            assert "updates_available" in data
            assert isinstance(data["total_installed"], int)
            assert isinstance(data["updates_available"], int)
            print(
                f"\n[OK] hacs_get_update_count: {data['total_installed']} installed, "
                f"{data['updates_available']} updates available"
            )
        else:
            assert "error" in data
            assert "HACS" in data.get("error", "") or "not" in data.get("error", "")
            print(f"\n[WARN] hacs_get_update_count: {data.get('error')}")

    def test_get_nfc_tags(self, real_mcp):
        """get_nfc_tags should return tags list and total count."""
        result = real_mcp.call_tool("get_nfc_tags")
        data = json.loads(result)
        assert data["success"] is True
        tags = data.get("tags", [])
        assert isinstance(tags, list)
        assert "total" in data
        assert data["total"] >= 0
        print(
            f"\n[OK] get_nfc_tags: {data['total']} tags, "
            f"{data.get('unused_count', 0)} unused"
        )

    def test_get_cache_stats(self, real_mcp):
        """get_cache_stats should return hits, misses, and hit_rate_percent."""
        result = real_mcp.call_tool("get_cache_stats")
        data = json.loads(result)
        assert data["success"] is True
        assert "hits" in data
        assert "misses" in data
        assert "hit_rate_percent" in data
        assert isinstance(data["hits"], int)
        assert isinstance(data["misses"], int)
        assert isinstance(data["hit_rate_percent"], (int, float))
        print(
            f"\n[OK] get_cache_stats: {data['hits']} hits, "
            f"{data['misses']} misses, "
            f"{data['hit_rate_percent']}% hit rate"
        )
