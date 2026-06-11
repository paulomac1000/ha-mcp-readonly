"""
Tests for ha_graph/scanner.py — HomeAssistantGraphScanner and build_graph_index.

Creates a temporary config directory with mock YAML/JSON files and verifies
the graph index is correctly built.
"""

import json

import pytest
import yaml

from ha_graph.models import GraphIndex
from ha_graph.scanner import build_graph_index

# Sample automations.yaml — one sensor-triggered, one light-controlling
AUTOMATIONS_YAML = [
    {
        "id": "auto_001",
        "alias": "Motion Light Hallway",
        "mode": "restart",
        "trigger": [{"platform": "state", "entity_id": "binary_sensor.hallway_motion", "to": "on"}],
        "condition": [{"condition": "state", "entity_id": "light.hallway", "state": "off"}],
        "action": [
            {"service": "light.turn_on", "target": {"entity_id": "light.hallway"}},
            {"delay": {"seconds": 120}},
            {"service": "light.turn_off", "target": {"entity_id": "light.hallway"}},
        ],
    },
    {
        "id": "auto_002",
        "alias": "Temperature Alert",
        "mode": "single",
        "trigger": [
            {"platform": "numeric_state", "entity_id": "sensor.living_temp", "above": "30"}
        ],
        "condition": [],
        "action": [
            {"service": "notify.mobile", "data": {"message": "Temperature too high!"}},
        ],
    },
]

SCRIPTS_YAML = {
    "script_001": {
        "alias": "Party Mode",
        "sequence": [
            {
                "service": "light.turn_on",
                "target": {"entity_id": "light.living_room"},
                "data": {"brightness": 255},
            },
            {"service": "switch.turn_on", "target": {"entity_id": "switch.party_lights"}},
        ],
    }
}

SCENES_YAML = [
    {
        "id": "scene_evening",
        "name": "Evening Scene",
        "entities": {
            "light.living_room": {"state": "on", "brightness": 80},
            "light.kitchen": {"state": "off"},
        },
    }
]

# Minimal entity registry
ENTITY_REGISTRY_DATA = {
    "data": {
        "entities": [
            {
                "entity_id": "binary_sensor.hallway_motion",
                "platform": "mqtt",
                "device_id": None,
                "area_id": None,
                "config_entry_id": None,
                "original_name": "Hallway Motion",
            },
            {
                "entity_id": "light.hallway",
                "platform": "mqtt",
                "device_id": None,
                "area_id": None,
                "config_entry_id": None,
                "original_name": "Hallway Light",
            },
            {
                "entity_id": "sensor.living_temp",
                "platform": "mqtt",
                "device_id": None,
                "area_id": None,
                "config_entry_id": None,
                "original_name": "Living Temperature",
            },
            {
                "entity_id": "light.living_room",
                "platform": "mqtt",
                "device_id": None,
                "area_id": None,
                "config_entry_id": None,
                "original_name": "Living Room Light",
            },
            {
                "entity_id": "switch.party_lights",
                "platform": "mqtt",
                "device_id": None,
                "area_id": None,
                "config_entry_id": None,
                "original_name": "Party Lights",
            },
        ]
    }
}

DEVICE_REGISTRY_DATA = {
    "data": {
        "devices": [
            {
                "id": "device_hub_001",
                "name": "ZigBee Hub",
                "model": "ZigBee Hub Pro",
                "manufacturer": "Conbee",
                "area_id": "living_room",
            }
        ]
    }
}

AREA_REGISTRY_DATA = {
    "data": {
        "areas": [
            {"area_id": "living_room", "name": "Living Room"},
            {"area_id": "hallway", "name": "Hallway"},
        ]
    }
}

CONFIG_ENTRIES_DATA = {
    "data": {
        "entries": [
            {
                "entry_id": "mqtt_entry_001",
                "domain": "mqtt",
                "title": "MQTT Broker",
                "version": 1,
                "state": "loaded",
            }
        ]
    }
}

LOVELACE_DATA = {
    "data": {
        "config": {
            "views": [
                {
                    "title": "Home",
                    "cards": [
                        {"type": "tile", "entity": "light.living_room"},
                        {"type": "tile", "entity": "sensor.living_temp"},
                    ],
                }
            ]
        }
    }
}


@pytest.fixture
def config_dir(tmp_path):
    """Create a temporary HA config directory with YAML/JSON files."""
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)

    # automations.yaml
    with open(cfg / "automations.yaml", "w") as f:
        yaml.dump(AUTOMATIONS_YAML, f)

    # scripts.yaml
    with open(cfg / "scripts.yaml", "w") as f:
        yaml.dump(SCRIPTS_YAML, f)

    # scenes.yaml
    with open(cfg / "scenes.yaml", "w") as f:
        yaml.dump(SCENES_YAML, f)

    # .storage directory
    storage = cfg / ".storage"
    storage.mkdir(parents=True, exist_ok=True)

    (storage / "core.entity_registry").write_text(
        json.dumps(ENTITY_REGISTRY_DATA), encoding="utf-8"
    )
    (storage / "core.device_registry").write_text(
        json.dumps(DEVICE_REGISTRY_DATA), encoding="utf-8"
    )
    (storage / "core.area_registry").write_text(json.dumps(AREA_REGISTRY_DATA), encoding="utf-8")
    (storage / "core.config_entries").write_text(json.dumps(CONFIG_ENTRIES_DATA), encoding="utf-8")
    (storage / "lovelace").write_text(json.dumps(LOVELACE_DATA), encoding="utf-8")

    return str(cfg)


class TestBuildGraphIndex:
    """Test the module-level build_graph_index convenience function."""

    def test_build_returns_graph_index(self, config_dir):
        """build_graph_index returns a GraphIndex instance."""
        index = build_graph_index(config_dir)
        assert isinstance(index, GraphIndex)
        assert index.built_at is not None

    def test_nodes_include_entities(self, config_dir):
        """Entity nodes are present from registry and config references."""
        index = build_graph_index(config_dir)
        entity_ids = {nid for nid, n in index.nodes.items() if n.type == "entity"}
        assert "entity:binary_sensor.hallway_motion" in entity_ids
        assert "entity:light.hallway" in entity_ids
        assert "entity:sensor.living_temp" in entity_ids

    def test_nodes_include_automations(self, config_dir):
        """Automation nodes are present."""
        index = build_graph_index(config_dir)
        auto_ids = {nid for nid, n in index.nodes.items() if n.type == "automation"}
        assert "automation:auto_001" in auto_ids
        assert "automation:auto_002" in auto_ids

    def test_edges_include_triggers_on(self, config_dir):
        """Edges include triggers_on relations from automations."""
        index = build_graph_index(config_dir)
        trigger_edges = [(e.source, e.target) for e in index.edges if e.relation == "triggers_on"]
        assert any("auto_001" in s and "hallway_motion" in t for s, t in trigger_edges)

    def test_edges_include_controls(self, config_dir):
        """Edges include controls relations from automations/scripts."""
        index = build_graph_index(config_dir)
        control_edges = [(e.source, e.target) for e in index.edges if e.relation == "controls"]
        # automation auto_001 controls light.hallway
        assert any("auto_001" in s and "light.hallway" in t for s, t in control_edges)
        # script_001 controls light.living_room
        assert any("script_001" in s and "light.living_room" in t for s, t in control_edges)

    def test_edges_include_calls_service(self, config_dir):
        """Edges include calls_service relations."""
        index = build_graph_index(config_dir)
        service_edges = {(e.source, e.target) for e in index.edges if e.relation == "calls_service"}
        assert any("auto_001" in s for s, _ in service_edges)
        assert any("script_001" in s for s, _ in service_edges)

    def test_nodes_include_areas_and_devices(self, config_dir):
        """Area and device nodes are present from registries."""
        index = build_graph_index(config_dir)
        types = {n.type for n in index.nodes.values()}
        assert "area" in types
        assert "device" in types

    def test_nodes_include_integration(self, config_dir):
        """Integration node is present from config entries."""
        index = build_graph_index(config_dir)
        integ_ids = {nid for nid, n in index.nodes.items() if n.type == "integration"}
        assert "integration:mqtt" in integ_ids

    def test_dashboard_displays_entities(self, config_dir):
        """Dashboard node has displays edges to entities."""
        index = build_graph_index(config_dir)
        display_edges = [(e.source, e.target) for e in index.edges if e.relation == "displays"]
        assert any("light.living_room" in t for _, t in display_edges)
        assert any("sensor.living_temp" in t for _, t in display_edges)

    def test_script_nodes(self, config_dir):
        """Script nodes are present."""
        index = build_graph_index(config_dir)
        script_ids = {nid for nid, n in index.nodes.items() if n.type == "script"}
        assert "script:script_001" in script_ids

    def test_scene_nodes(self, config_dir):
        """Scene nodes are present."""
        index = build_graph_index(config_dir)
        scene_ids = {nid for nid, n in index.nodes.items() if n.type == "scene"}
        assert "scene:scene_evening" in scene_ids

    def test_stats_populated(self, config_dir):
        """Graph stats are populated after scan."""
        index = build_graph_index(config_dir)
        assert "node_types" in index.stats
        assert "edge_relations" in index.stats
        assert index.stats["node_types"].get("entity", 0) >= 5

    def test_built_at_set(self, config_dir):
        """built_at timestamp is set after scan."""
        index = build_graph_index(config_dir)
        assert index.built_at is not None
        assert isinstance(index.built_at, float)


class TestScannerEdgeCases:
    """Test edge cases for the scanner."""

    def test_missing_directory(self, tmp_path):
        """Scanner tolerates missing config directory (adds a file node for config)."""
        missing = str(tmp_path / "nonexistent")
        index = build_graph_index(missing)
        assert isinstance(index, GraphIndex)
        # A file:configuration.yaml node is always added
        assert len(index.nodes) >= 1
        assert len(index.edges) == 0

    def test_empty_yaml_files(self, tmp_path):
        """Empty YAML files produce no errors."""
        cfg = tmp_path / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "automations.yaml").write_text("", encoding="utf-8")
        (cfg / "scripts.yaml").write_text("", encoding="utf-8")
        (cfg / "scenes.yaml").write_text("", encoding="utf-8")
        index = build_graph_index(str(cfg))
        assert isinstance(index, GraphIndex)
        assert len(index.nodes) >= 0

    def test_corrupt_yaml_file(self, tmp_path):
        """Scanner tolerates corrupt YAML files without crashing."""
        cfg = tmp_path / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "automations.yaml").write_bytes(b"\x00\x00\x00\x00\x00\x00\x00\x00")
        (cfg / "scripts.yaml").write_text("[]", encoding="utf-8")
        (cfg / "scenes.yaml").write_text("[]", encoding="utf-8")
        # Should not raise
        index = build_graph_index(str(cfg))
        assert isinstance(index, GraphIndex)
