"""Analyzers for Home Assistant configuration data."""

import json
import os
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from typing import Any

from . import constants
from .utils import (
    extract_controlled_entities,
    extract_entities_from_data,
    extract_entities_from_template,
    extract_services,
    extract_trigger_info,
    get_best_name,
    get_cache_stats,
    load_registry,
    load_yaml_file,
    make_ha_request,
    resolve_area_id,
    slugify,
    validate_yaml_syntax,
)


class RegistryCollector:
    """Collects and integrates data from HA registries with caching."""

    def __init__(self):
        self.entities: list[dict] = []
        self.devices: list[dict] = []
        self.areas: list[dict] = []
        self.config_entries: list[dict] = []
        self.states: list[dict] = []

        # maps for quick access
        self.entities_map: dict[str, dict] = {}
        self.devices_map: dict[str, dict] = {}
        self.areas_map: dict[str, str] = {}  # id -> name
        self.states_map: dict[str, dict] = {}
        self.config_entries_map: dict[str, dict] = {}

        # Reverse lookups
        self.entity_to_platform: dict[str, str] = {}
        self.entity_to_device: dict[str, str] = {}
        self.entity_to_config_entry: dict[str, str] = {}
        self.device_to_config_entry: dict[str, list[str]] = defaultdict(list)

        # Config entry health
        self.config_entry_health: dict[str, dict] = {}

    def collect(self) -> bool:
        """Collects all data. Returns True if successful."""
        print("Collecting data from registries (with cache)...")

        # Registry files
        entity_reg = load_registry("core.entity_registry")
        self.entities = entity_reg.get("data", {}).get("entities", [])

        device_reg = load_registry("core.device_registry")
        self.devices = device_reg.get("data", {}).get("devices", [])

        area_reg = load_registry("core.area_registry")
        self.areas = area_reg.get("data", {}).get("areas", [])

        config_reg = load_registry("core.config_entries")
        self.config_entries = config_reg.get("data", {}).get("entries", [])

        # API States
        print("Fetching states from API...", end=" ", flush=True)
        states_result = make_ha_request("/api/states")
        if states_result["success"]:
            self.states = states_result["data"]
            print(f"   ({len(self.states)} entities)")
        else:
            print(f"   Error: {states_result.get('error')}")
            return False

        self._build_maps()
        self._compute_config_entry_health()
        self._compute_domain_summary()

        # Data quality tracking
        self.data_quality: dict[str, str] = {}
        self.data_quality["entity_registry"] = "complete" if self.entities else "failed"
        self.data_quality["device_registry"] = "complete" if self.devices else "failed"
        self.data_quality["states_api"] = "complete" if self.states else "failed"
        self.data_quality_overall: str = (
            "complete" if all(v == "complete" for v in self.data_quality.values()) else "partial"
        )

        print(
            f"   Registry: {len(self.entities)} entities, {len(self.devices)} devices, {len(self.areas)} areas"
        )
        print(f"   Config entries: {len(self.config_entries)}")
        return True

    def _build_maps(self):
        """Builds maps for quick access."""
        # Area map (id -> name)
        for a in self.areas:
            self.areas_map[a.get("id")] = a.get("name", a.get("id"))

        # Device map
        for d in self.devices:
            did = d.get("id")
            if did:
                self.devices_map[did] = d
                for ce_id in d.get("config_entries", []):
                    self.device_to_config_entry[did].append(ce_id)

        # Config entry map
        for e in self.config_entries:
            eid = e.get("entry_id")
            if eid:
                self.config_entries_map[eid] = e

        # Entity registry map
        for e in self.entities:
            entity_id = e.get("entity_id")
            if entity_id:
                self.entities_map[entity_id] = e
                self.entity_to_platform[entity_id] = e.get("platform", "unknown")
                self.entity_to_device[entity_id] = e.get("device_id")
                self.entity_to_config_entry[entity_id] = e.get("config_entry_id")

        # States map
        for s in self.states:
            entity_id = s.get("entity_id")
            if entity_id:
                self.states_map[entity_id] = s
                # Add entities from API that are not in registry
                if entity_id not in self.entities_map:
                    self.entity_to_platform[entity_id] = entity_id.split(".")[0]

    def _compute_config_entry_health(self):
        """
        Calculates health of each config entry.
        Based on test_config_entries.py diagnose_config_entry.
        """
        for entry in self.config_entries:
            entry_id = entry.get("entry_id")
            if not entry_id:
                continue

            # Find entities for this config entry
            entry_entities = [
                eid for eid, ce_id in self.entity_to_config_entry.items() if ce_id == entry_id
            ]

            # Count unavailable
            unavailable = sum(
                1
                for eid in entry_entities
                if self.states_map.get(eid, {}).get("state") == "unavailable"
            )

            total = len(entry_entities)
            health = 100
            if total > 0:
                health = int(((total - unavailable) / total) * 100)

            self.config_entry_health[entry_id] = {
                "total_entities": total,
                "unavailable": unavailable,
                "health_percent": health,
                "disabled_by": entry.get("disabled_by"),
                "state": entry.get("state", "loaded"),
            }

    def _compute_domain_summary(self):
        """Compute aggregated domain statistics."""
        self.domain_counts = Counter()
        self.state_distribution = Counter()

        for state in self.states:
            eid = state.get("entity_id", "")
            domain = eid.split(".", 1)[0] if "." in eid else "unknown"
            self.domain_counts[domain] += 1
            self.state_distribution[state.get("state", "unknown")] += 1

        self.updates_available = {"total": 0, "available": 0, "entities": []}
        for state in self.states:
            eid = state.get("entity_id", "")
            if eid.startswith("update."):
                self.updates_available["total"] += 1
                if state.get("state") == "on":
                    self.updates_available["available"] += 1
                    self.updates_available["entities"].append(eid)

    def get_entity_ids(self) -> list[str]:
        """
        Returns list of entity_id from registry.
        Handles lists of strings, dicts, and objects with entity_id attribute.
        Always returns list[str], without duplicates, sorted.
        """
        if not self.entities:
            return []

        entity_ids: list[str] = []

        for e in self.entities:
            # most common case: string
            if isinstance(e, str):
                entity_ids.append(e)
                continue

            # dict with entity_id
            if isinstance(e, dict):
                eid = e.get("entity_id")
                if isinstance(eid, str):
                    entity_ids.append(eid)
                continue

            # object with entity_id attribute
            eid = getattr(e, "entity_id", None)
            if isinstance(eid, str):
                entity_ids.append(eid)
                continue

        # remove duplicates + stability
        return sorted(set(entity_ids))

    def get_entity_info(self, entity_id: str) -> dict[str, Any]:
        """
        Fetches full info about entity (registry + state).
        Based on test_storage.py get_entity_context.
        """
        reg = self.entities_map.get(entity_id, {})
        state = self.states_map.get(entity_id, {})
        device_id = reg.get("device_id") or self.entity_to_device.get(entity_id)

        # FIXED resolve_area_id
        area_id = resolve_area_id(reg, self.devices_map)
        area_name = self.areas_map.get(area_id, "Unassigned") if area_id else "Unassigned"

        info = {
            "entity_id": entity_id,
            "state": state.get("state", "unknown"),
            "friendly_name": state.get("attributes", {}).get("friendly_name") or get_best_name(reg),
            "platform": self.entity_to_platform.get(entity_id, "unknown"),
            "area_id": area_id or "unassigned",
            "area_name": area_name,
            "device_id": device_id,
            "device_name": self._get_device_name(device_id),
            "disabled_by": reg.get("disabled_by"),
            "hidden_by": reg.get("hidden_by"),
        }

        # Add device_class and unit if present
        attrs = state.get("attributes", {})
        if attrs.get("device_class"):
            info["device_class"] = attrs["device_class"]
        if attrs.get("unit_of_measurement"):
            info["unit"] = attrs["unit_of_measurement"]

        return info

    def _get_device_name(self, device_id: str | None) -> str:
        """Fetches device name."""
        if not device_id:
            return "Virtual/Service"
        device = self.devices_map.get(device_id, {})
        return get_best_name(device, "device")

    def get_integration_domain(self, entity_id: str) -> str:
        """
        Fetches integration domain for entity.
        Based on test_config_entries.py.
        """
        # First from config_entry
        config_entry_id = self.entity_to_config_entry.get(entity_id)
        if config_entry_id and config_entry_id in self.config_entries_map:
            return self.config_entries_map[config_entry_id].get("domain", "unknown")

        # Fallback to platform
        return self.entity_to_platform.get(entity_id, entity_id.split(".")[0])

    def entity_exists(self, entity_id: str) -> bool:
        """Checks if entity exists in the system."""
        return entity_id in self.states_map or entity_id in self.entities_map

    def is_virtual_entity(self, entity_id: str) -> bool:
        """Checks if entity is virtual (may not have a state)."""
        domain = entity_id.split(".")[0]
        return domain in constants.VIRTUAL_ENTITY_DOMAINS


def _analyze_choose_branches(actions: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze choose blocks in automation actions.

    Extracts structured metadata from each choose branch: condition types,
    action count, and whether a default branch exists.

    Args:
        actions: List of action dicts from an automation.

    Returns:
        Dict with choose_count and branches list.
    """
    choose_count = 0
    branches: list[dict[str, Any]] = []
    has_default = False

    for action in actions:
        if not isinstance(action, dict) or "choose" not in action:
            continue

        act_default = action.get("default")
        choose_block = action["choose"]
        if act_default is not None:
            has_default = True

        if not isinstance(choose_block, list):
            continue

        for branch_idx, choice in enumerate(choose_block):
            if not isinstance(choice, dict):
                continue

            choose_count += 1

            raw_conditions = choice.get("conditions", [])
            if isinstance(raw_conditions, dict):
                raw_conditions = [raw_conditions]
            elif not isinstance(raw_conditions, list):
                raw_conditions = []

            condition_types: list[str] = []
            for cond in raw_conditions:
                if isinstance(cond, str):
                    condition_types.append(f"alias:{cond}")
                elif isinstance(cond, dict):
                    cond_type = cond.get("condition", "unknown")
                    if cond_type == "trigger":
                        tid = cond.get("id", "?")
                        if isinstance(tid, list):
                            tid = ",".join(tid)
                        condition_types.append(f"trigger:{tid}")
                    elif cond_type == "state":
                        condition_types.append(f"state:{cond.get('entity_id', '?')}")
                    elif cond_type == "numeric_state":
                        condition_types.append(f"numeric_state:{cond.get('entity_id', '?')}")
                    elif cond_type == "time":
                        condition_types.append(
                            f"time:{cond.get('after', cond.get('before', cond.get('weekday', '?')))}"
                        )
                    elif cond_type == "sun":
                        condition_types.append(f"sun:{cond.get('after', cond.get('before', '?'))}")
                    elif cond_type == "template":
                        vt = cond.get("value_template", "")
                        condition_types.append(f"template:{vt[:40]}" if vt else "template:?")
                    elif cond_type == "zone":
                        condition_types.append(f"zone:{cond.get('entity_id', '?')}")
                    else:
                        condition_types.append(cond_type)

            raw_sequence = choice.get("sequence", [])
            if not isinstance(raw_sequence, list):
                raw_sequence = [raw_sequence] if raw_sequence else []

            branches.append(
                {
                    "index": branch_idx,
                    "conditions": condition_types,
                    "actions_count": len(raw_sequence),
                    "has_default": False,
                }
            )

    return {
        "choose_count": choose_count,
        "has_default": has_default,
        "branches": branches,
    }


class AutomationAnalyzer:
    """Analyzes automations, scripts, and scenes with full conflict detection."""

    def __init__(self, registry: RegistryCollector):
        self.registry = registry
        self.automations: list[dict] = []
        self.scripts: dict = {}
        self.scenes: list[dict] = []
        self.blueprints: list[dict] = []

        # analysis results
        self.automation_analysis: list[dict] = []
        self.script_analysis: list[dict] = []
        self.scene_analysis: list[dict] = []
        self.blueprint_usage: dict[str, list[str]] = defaultdict(list)

        # Dependency tracking
        self.entity_triggered_by: dict[str, list[str]] = defaultdict(list)
        self.entity_used_in: dict[str, list[str]] = defaultdict(list)
        self.entity_controlled_by: dict[str, list[str]] = defaultdict(list)

        # Ghost entities - used but non-existent
        self.ghost_entities: dict[str, list[str]] = defaultdict(list)

        # Conflict detection - EXTENDED to scenes and scripts
        self.entity_conflicts: dict[str, dict] = defaultdict(
            lambda: {
                "controlling_automations": [],
                "controlling_scripts": [],
                "controlling_scenes": [],
                "triggered_by": [],
            }
        )

        # Conflicting entities
        self.conflicting_entities: dict[str, dict] = {}

    def collect(self):
        """Collects data about HA logic."""
        print("Loading logic (automations, scripts, scenes)...")

        # Automations
        automations = load_yaml_file("automations.yaml")
        if automations:
            if isinstance(automations, dict):
                self.automations = [automations]
            elif isinstance(automations, list):
                self.automations = [a for a in automations if isinstance(a, dict)]
        print(f"   Automations: {len(self.automations)}")

        # Scripts
        scripts = load_yaml_file("scripts.yaml")
        if scripts and isinstance(scripts, dict):
            self.scripts = scripts
        print(f"   Scripts: {len(self.scripts)}")

        # Scenes
        scenes = load_yaml_file("scenes.yaml")
        if scenes and isinstance(scenes, list):
            self.scenes = [s for s in scenes if isinstance(s, dict)]
        print(f"   Scenes: {len(self.scenes)}")

        # Blueprints
        self._collect_blueprints()

    def _collect_blueprints(self):
        """
        Collects blueprints.
        Based on test_blueprints.py list_blueprints.
        """
        blueprints_dir = Path(constants.HA_CONFIG_PATH) / "blueprints"
        if not blueprints_dir.exists():
            return

        for domain in ["automation", "script"]:
            domain_dir = blueprints_dir / domain
            if not domain_dir.exists():
                continue
            for root, _, files in os.walk(domain_dir):
                for file in files:
                    if file.endswith(".yaml"):
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, blueprints_dir)
                        bp_data = load_yaml_file(full_path)

                        bp_info = bp_data.get("blueprint", {}) if bp_data else {}

                        self.blueprints.append(
                            {
                                "path": rel_path,
                                "domain": domain,
                                "name": bp_info.get("name", file.replace(".yaml", "")),
                                "description": bp_info.get("description", ""),
                                "source_url": bp_info.get("source_url"),
                                "inputs": list(bp_info.get("input", {}).keys())
                                if bp_info.get("input")
                                else [],
                            }
                        )

        print(f"   Blueprints: {len(self.blueprints)}")

    def analyze(self):
        """Analyzes logic and builds dependency graphs."""
        print("Analyzing logic and dependencies...")

        self._analyze_automations()
        self._analyze_scripts()
        self._analyze_scenes()
        self._detect_conflicts()

        # Statistics
        entities_with_deps = len(
            set(self.entity_triggered_by.keys())
            | set(self.entity_used_in.keys())
            | set(self.entity_controlled_by.keys())
        )
        print(f"   Entities with dependencies: {entities_with_deps}")
        print(f"   Ghost entities: {len(self.ghost_entities)}")
        print(f"   Potential conflicts: {len(self.conflicting_entities)}")

        self._build_blueprint_summary()

    def _analyze_automations(self):
        """Analyzes automations."""
        for auto in self.automations:
            auto_id = auto.get("id", "unknown")
            alias = auto.get("alias", f"ID: {auto_id}")
            mode = auto.get("mode", "single")

            # Blueprint usage
            if "use_blueprint" in auto:
                bp_path = auto["use_blueprint"].get("path", "")
                self.blueprint_usage[bp_path].append(alias)

            # Trigger analysis - EXTENDED
            triggers = auto.get("trigger", [])
            trigger_entities, trigger_platforms = extract_trigger_info(triggers)

            # Condition + Action analysis
            conditions = auto.get("condition", [])
            actions = auto.get("action", [])

            condition_entities = extract_entities_from_data(conditions)
            action_entities = extract_entities_from_data(actions)

            # Controlled entities
            controlled = extract_controlled_entities(actions)

            # Services used
            services = extract_services(actions)

            # Register dependencies
            for eid in trigger_entities:
                self._register_dependency(eid, f"automation: {alias}", "triggered_by")

            for eid in condition_entities:
                self._register_dependency(eid, f"automation: {alias}", "used_in")

            for eid in action_entities - controlled:
                self._register_dependency(eid, f"automation: {alias}", "used_in")

            for eid in controlled:
                self._register_dependency(
                    eid,
                    f"automation: {alias}",
                    "controlled_by",
                    source_type="automation",
                )

            # Automation state
            auto_entity_id = f"automation.{slugify(alias)}"
            auto_state = self.registry.states_map.get(auto_entity_id, {})
            is_disabled = auto_state.get("state") == "off"
            last_triggered = auto_state.get("attributes", {}).get("last_triggered")

            self.automation_analysis.append(
                {
                    "alias": alias,
                    "id": auto_id,
                    "entity_id": auto_entity_id,
                    "mode": mode,
                    "trigger_platforms": list(set(trigger_platforms)),
                    "trigger_entities": sorted(trigger_entities),
                    "condition_entities": sorted(condition_entities),
                    "action_entities": sorted(action_entities - controlled),
                    "controlled_entities": sorted(controlled),
                    "services": sorted(services),
                    "is_disabled": is_disabled,
                    "last_triggered": last_triggered,
                    "uses_blueprint": "use_blueprint" in auto,
                    "choose_analysis": _analyze_choose_branches(actions),
                }
            )

    def _analyze_scripts(self):
        """Analyzes scripts with full conflict support."""
        for script_id, script_config in self.scripts.items():
            if not isinstance(script_config, dict):
                continue

            alias = script_config.get("alias", script_id)

            all_entities = extract_entities_from_data(script_config)
            services = extract_services(script_config)

            sequence = script_config.get("sequence", [])
            controlled = extract_controlled_entities(sequence)

            for eid in all_entities - controlled:
                self._register_dependency(eid, f"script.{script_id}", "used_in")

            # NEW: register controlled entities also for scripts
            for eid in controlled:
                self._register_dependency(
                    eid, f"script.{script_id}", "controlled_by", source_type="script"
                )

            # Script state
            script_entity_id = f"script.{script_id}"
            script_state = self.registry.states_map.get(script_entity_id, {})

            self.script_analysis.append(
                {
                    "script_id": script_id,
                    "entity_id": script_entity_id,
                    "alias": alias,
                    "mode": script_config.get("mode", "single"),
                    "entities": sorted(all_entities - controlled),
                    "controlled_entities": sorted(controlled),
                    "services": sorted(services),
                    "state": script_state.get("state", "unknown"),
                }
            )

    def _analyze_scenes(self):
        """Analyzes scenes with full conflict support."""
        for scene in self.scenes:
            name = scene.get("name", "Unnamed Scene")
            scene_id = scene.get("id", slugify(name))
            entities_config = scene.get("entities", {})

            controlled = list(entities_config.keys()) if isinstance(entities_config, dict) else []

            # NEW: register controlled entities for scenes
            for eid in controlled:
                self._register_dependency(
                    eid, f"scene.{name}", "controlled_by", source_type="scene"
                )

            self.scene_analysis.append(
                {
                    "name": name,
                    "id": scene_id,
                    "entity_id": f"scene.{slugify(name)}",
                    "controlled_entities": sorted(controlled),
                    "entity_count": len(controlled),
                }
            )

    def _register_dependency(
        self,
        entity_id: str,
        source: str,
        dep_type: str,
        source_type: str = "automation",
    ):
        """Registers entity dependency with correct ghost entity checking."""
        # Check if entity exists
        if not self.registry.entity_exists(entity_id):
            # FIXED: Don't mark as ghost if it's a virtual entity
            if not self.registry.is_virtual_entity(entity_id):
                self.ghost_entities[entity_id].append(source)

        if dep_type == "triggered_by":
            self.entity_triggered_by[entity_id].append(source)
            self.entity_conflicts[entity_id]["triggered_by"].append(source)
        elif dep_type == "used_in":
            self.entity_used_in[entity_id].append(source)
        elif dep_type == "controlled_by":
            self.entity_controlled_by[entity_id].append(source)
            # EXTENDED: Add to appropriate conflict category
            if source_type == "automation":
                self.entity_conflicts[entity_id]["controlling_automations"].append(source)
            elif source_type == "script":
                self.entity_conflicts[entity_id]["controlling_scripts"].append(source)
            elif source_type == "scene":
                self.entity_conflicts[entity_id]["controlling_scenes"].append(source)

    def _detect_conflicts(self):
        """
        Detects potential conflicts.
        EXTENDED version covering automations, scripts, and scenes.
        Based on test_automations.py get_automation_conflicts.
        """
        for entity_id, conflict_info in self.entity_conflicts.items():
            total_controllers = (
                len(conflict_info["controlling_automations"])
                + len(conflict_info["controlling_scripts"])
                + len(conflict_info["controlling_scenes"])
            )

            if total_controllers > 1:
                self.conflicting_entities[entity_id] = {
                    "controlling_automations": conflict_info["controlling_automations"],
                    "controlling_scripts": conflict_info["controlling_scripts"],
                    "controlling_scenes": conflict_info["controlling_scenes"],
                    "total_controllers": total_controllers,
                    "triggered_by": conflict_info["triggered_by"],
                    "race_condition_risk": len(conflict_info["controlling_automations"]) > 1,
                }

    def _build_blueprint_summary(self):
        """Build aggregated blueprint usage statistics."""
        self.blueprint_summary = []
        if hasattr(self, "blueprints") and self.blueprints:
            usage = self.blueprint_usage if hasattr(self, "blueprint_usage") else {}
            for bp in self.blueprints:
                path = bp.get("path", "unknown")
                self.blueprint_summary.append(
                    {
                        "name": bp.get("name", path),
                        "path": path,
                        "domain": bp.get("domain", "unknown"),
                        "author": bp.get("author", "unknown"),
                        "used_by": usage.get(path, []),
                        "instance_count": len(usage.get(path, [])),
                    }
                )
            self.blueprint_summary.sort(key=lambda x: x["instance_count"], reverse=True)


class DashboardAnalyzer:
    """Analyzes entity usage in dashboards with extended custom card support."""

    def __init__(self, registry: RegistryCollector):
        self.registry = registry
        self.entity_in_dashboards: dict[str, list[dict]] = defaultdict(list)
        self.dashboards_found: list[dict] = []  # CHANGE: now Dict with metadata
        self.missing_entities: dict[str, list[str]] = defaultdict(list)  # NEW

    def analyze(self):
        """Analyzes all dashboards."""
        print("Analyzing Lovelace dashboards...")

        storage_path = Path(constants.HA_CONFIG_PATH) / ".storage"
        if not storage_path.exists():
            print("   Warning: Missing .storage folder")
            return

        # Find all lovelace files
        lovelace_files = []
        for f in storage_path.iterdir():
            if f.name.startswith("lovelace"):
                lovelace_files.append(f.name)

        if not lovelace_files:
            print("   Warning: No lovelace files in .storage")
            return

        print(f"   Found files: {lovelace_files}")

        for lf in lovelace_files:
            self._analyze_dashboard(lf)

        # NEW: Check if entities from dashboards exist
        self._check_dashboard_entities()

        used_count = len(self.entity_in_dashboards)
        print(f"   Entities used in dashboards: {used_count}")
        print(f"   Non-existent entities in dashboards: {len(self.missing_entities)}")

        self._collect_lovelace_resources()

    def _analyze_dashboard(self, registry_name):
        """Analyzes single dashboard."""
        data = load_registry(registry_name)
        if not data:
            return

        # Dashboard metadata
        if registry_name == "lovelace":
            dashboard_name = "Main Dashboard"
            dashboard_url = "lovelace"
        else:
            dashboard_url = registry_name.replace("lovelace.", "")
            dashboard_name = dashboard_url.replace("_", " ").title()

        self.dashboards_found.append(
            {
                "name": dashboard_name,
                "file": registry_name,
                "url": f"/lovelace/{dashboard_url}"
                if dashboard_url != "lovelace"
                else "/lovelace/0",
            }
        )

        # Structure may vary
        config = data.get("data", {}).get("config", {})
        if not config:
            config = data.get("data", {})

        views = config.get("views", [])

        for view_idx, view in enumerate(views):
            view_title = view.get("title", f"View {view_idx}")

            # Parse all cards recursively
            self._parse_cards(view.get("cards", []), dashboard_name, view_title)

            # Badges
            for badge in view.get("badges", []):
                if isinstance(badge, str):
                    self.entity_in_dashboards[badge].append(
                        {
                            "dashboard": dashboard_name,
                            "view": view_title,
                            "type": "badge",
                        }
                    )
                elif isinstance(badge, dict) and "entity" in badge:
                    self.entity_in_dashboards[badge["entity"]].append(
                        {
                            "dashboard": dashboard_name,
                            "view": view_title,
                            "type": "badge",
                        }
                    )

    def _parse_cards(self, cards: list, dashboard: str, view: str, depth: int = 0):
        """Recursively parses cards with extended custom card support."""
        if depth > 15:
            return

        for card in cards:
            if not isinstance(card, dict):
                continue

            card_type = card.get("type", "unknown")

            # Main card entity
            if "entity" in card:
                self.entity_in_dashboards[card["entity"]].append(
                    {"dashboard": dashboard, "view": view, "card_type": card_type}
                )

            # entity list
            if "entities" in card:
                entities = card["entities"]
                if isinstance(entities, list):
                    for e in entities:
                        if isinstance(e, str):
                            self.entity_in_dashboards[e].append(
                                {
                                    "dashboard": dashboard,
                                    "view": view,
                                    "card_type": card_type,
                                }
                            )
                        elif isinstance(e, dict) and "entity" in e:
                            self.entity_in_dashboards[e["entity"]].append(
                                {
                                    "dashboard": dashboard,
                                    "view": view,
                                    "card_type": card_type,
                                }
                            )

            # Nested cards (stack, grid, etc.)
            for nested_key in ["cards", "elements", "sections", "rows"]:
                if nested_key in card:
                    self._parse_cards(card[nested_key], dashboard, view, depth + 1)

            # Custom cards - EXTENDED
            # button-card
            if card_type == "custom:button-card":
                if "entity" in card:
                    self.entity_in_dashboards[card["entity"]].append(
                        {"dashboard": dashboard, "view": view, "card_type": card_type}
                    )
                # tap_action, hold_action, etc.
                for action_key in ["tap_action", "hold_action", "double_tap_action"]:
                    action = card.get(action_key, {})
                    if action.get("service"):
                        found = extract_entities_from_data(action)
                        for eid in found:
                            self.entity_in_dashboards[eid].append(
                                {
                                    "dashboard": dashboard,
                                    "view": view,
                                    "card_type": card_type,
                                }
                            )

            # mod-card (card-mod wrapper)
            if card_type == "custom:mod-card" and "card" in card:
                self._parse_cards([card["card"]], dashboard, view, depth + 1)

            # state-switch
            if card_type == "custom:state-switch":
                if "entity" in card:
                    self.entity_in_dashboards[card["entity"]].append(
                        {"dashboard": dashboard, "view": view, "card_type": card_type}
                    )
                for state_key, state_card in card.get("states", {}).items():
                    if isinstance(state_card, dict):
                        self._parse_cards([state_card], dashboard, view, depth + 1)

            # auto-entities
            if card_type == "custom:auto-entities":
                if "card" in card:
                    self._parse_cards([card["card"]], dashboard, view, depth + 1)

            # Fallback: search for entities in entire card structure
            card_str = json.dumps(card)
            found_entities = set(constants.ENTITY_PATTERN.findall(card_str))

            for eid in found_entities:
                # Add only if not yet added for this card
                existing = self.entity_in_dashboards.get(eid, [])
                if not any(
                    d["dashboard"] == dashboard
                    and d["view"] == view
                    and d["card_type"] == card_type
                    for d in existing
                ):
                    self.entity_in_dashboards[eid].append(
                        {"dashboard": dashboard, "view": view, "card_type": card_type}
                    )

    def _check_dashboard_entities(self):
        """
        Checks if entities used in dashboards exist.
        NEW: Cross-referencing ghost entities with dashboards.
        """
        for entity_id, usages in self.entity_in_dashboards.items():
            if not self.registry.entity_exists(entity_id):
                # Not a virtual entity
                if not self.registry.is_virtual_entity(entity_id):
                    self.missing_entities[entity_id] = [
                        f"{u['dashboard']}/{u['view']}" for u in usages[:3]
                    ]

    def _collect_lovelace_resources(self):
        """Collect Lovelace dashboard resources (custom cards, modules)."""
        self.lovelace_resources = []
        resources_data = load_registry("lovelace.resources")
        if resources_data:
            items = resources_data.get("data", {}).get("items", [])
            if not items:
                items = resources_data.get("data", {}).get("resources", [])
            for item in items:
                if isinstance(item, dict):
                    url = item.get("url", item.get("path", ""))
                    self.lovelace_resources.append(
                        {
                            "type": item.get("type", "module"),
                            "url": url,
                            "source": "hacs"
                            if "/hacsfiles/" in url
                            else "local"
                            if "/local/" in url
                            else "external",
                        }
                    )
        print(f"   Lovelace resources: {len(self.lovelace_resources)}")


class TemplateEntityCollector:
    """Collects template entities with YAML validation."""

    def __init__(self, registry: RegistryCollector):
        self.registry = registry
        self.template_entities: list[dict] = []
        self.validation_errors: list[dict] = []

    def collect(self):
        """Collects template entities."""
        print("Collecting template entities...")

        # 1. From config_entries (UI-defined)
        self._collect_from_config_entries()

        # 2. From configuration.yaml
        self._collect_from_configuration()

        print(f"   Template entities: {len(self.template_entities)}")
        if self.validation_errors:
            print(f"   Validation errors: {len(self.validation_errors)}")

        self._collect_exposed_entities()

    def _collect_from_config_entries(self):
        """Collects template entities from config entries (UI)."""
        for entry in self.registry.config_entries:
            if entry.get("domain") != "template":
                continue

            options = entry.get("options", {})
            data = entry.get("data", {})

            name = options.get("name") or data.get("name", entry.get("title", "Unknown"))
            ttype = options.get("template_type") or data.get("template_type", "sensor")

            state_template = options.get("state") or data.get("state", "")

            # Extract referenced entities from template
            referenced = list(extract_entities_from_template(str(state_template)))

            # NEW: Get attributes from template
            attributes = {}
            attrs_config = options.get("attributes") or data.get("attributes", {})
            if isinstance(attrs_config, dict):
                for attr_name, attr_template in attrs_config.items():
                    attributes[attr_name] = str(attr_template)[:100]
                    referenced.extend(extract_entities_from_template(str(attr_template)))

            self.template_entities.append(
                {
                    "name": name,
                    "type": ttype,
                    "source": "UI (config_entry)",
                    "entry_id": entry.get("entry_id"),
                    "state_template": str(state_template)[:150] if state_template else "",
                    "device_class": options.get("device_class") or data.get("device_class"),
                    "unit": options.get("unit_of_measurement") or data.get("unit_of_measurement"),
                    "attributes": attributes,
                    "referenced_entities": sorted(set(referenced)),
                }
            )

    def _collect_from_configuration(self):
        """Collects template entities from configuration.yaml with validation."""
        config = load_yaml_file("configuration.yaml")
        if not config:
            return

        # Template integration (new format)
        if "template" in config:
            template_config = config["template"]
            if isinstance(template_config, list):
                for item in template_config:
                    if isinstance(item, dict):
                        self._parse_template_section(item)

        # Legacy sensor.template
        if "sensor" in config:
            sensors = config["sensor"]
            if isinstance(sensors, list):
                for sensor_config in sensors:
                    if (
                        isinstance(sensor_config, dict)
                        and sensor_config.get("platform") == "template"
                    ):
                        self._parse_legacy_template(sensor_config, "sensor")

        # Legacy binary_sensor.template
        if "binary_sensor" in config:
            bs = config["binary_sensor"]
            if isinstance(bs, list):
                for sensor_config in bs:
                    if (
                        isinstance(sensor_config, dict)
                        and sensor_config.get("platform") == "template"
                    ):
                        self._parse_legacy_template(sensor_config, "binary_sensor")

    def _parse_template_section(self, item: dict):
        """Parses template section from configuration.yaml."""
        for platform in ["sensor", "binary_sensor", "number", "select", "button"]:
            if platform not in item:
                continue

            entities = item[platform]
            if not isinstance(entities, list):
                entities = [entities]

            for entity_config in entities:
                if not isinstance(entity_config, dict):
                    continue

                name = entity_config.get("name", "Unknown")
                state_template = entity_config.get("state", "")

                # NEW: Validate template
                if state_template and isinstance(state_template, str):
                    validation = validate_yaml_syntax(f"test: '{state_template}'")
                    if not validation.get("syntax_valid", True):
                        self.validation_errors.append(
                            {
                                "name": name,
                                "error": validation.get("error", "Unknown error"),
                            }
                        )

                referenced = list(extract_entities_from_data(entity_config))

                # NEW: attributes
                attributes = {}
                for attr_name, attr_value in entity_config.get("attributes", {}).items():
                    if isinstance(attr_value, str):
                        attributes[attr_name] = attr_value[:100]

                self.template_entities.append(
                    {
                        "name": name,
                        "type": platform,
                        "source": "configuration.yaml",
                        "state_template": str(state_template)[:150] if state_template else "",
                        "device_class": entity_config.get("device_class"),
                        "unit": entity_config.get("unit_of_measurement"),
                        "attributes": attributes,
                        "referenced_entities": sorted(set(referenced)),
                    }
                )

    def _parse_legacy_template(self, config: dict, platform: str):
        """Parses legacy template format."""
        sensors = config.get("sensors", {})
        if not isinstance(sensors, dict):
            return

        for sensor_id, sensor_config in sensors.items():
            if not isinstance(sensor_config, dict):
                continue

            name = sensor_config.get("friendly_name", sensor_id)
            state_template = sensor_config.get("value_template", "")

            referenced = list(extract_entities_from_data(sensor_config))

            # attributes
            attributes = {}
            for attr_name, attr_template in sensor_config.get("attribute_templates", {}).items():
                if isinstance(attr_template, str):
                    attributes[attr_name] = attr_template[:100]

            self.template_entities.append(
                {
                    "name": name,
                    "type": platform,
                    "source": "configuration.yaml (legacy)",
                    "entity_id": f"{platform}.{sensor_id}",
                    "state_template": str(state_template)[:150] if state_template else "",
                    "device_class": sensor_config.get("device_class"),
                    "unit": sensor_config.get("unit_of_measurement"),
                    "attributes": attributes,
                    "referenced_entities": sorted(set(referenced)),
                }
            )

    def _collect_exposed_entities(self):
        """Collect entities exposed to voice assistants."""
        self.exposed_entities = {}
        # Check cloud configuration for exposed entities
        cloud_data = load_registry("cloud")
        if cloud_data:
            alexa = cloud_data.get("data", {}).get("alexa", {})
            google = cloud_data.get("data", {}).get("google_actions", {})
            if alexa:
                self.exposed_entities["alexa"] = {
                    "entities": alexa.get("entities", {}),
                    "filter": alexa.get("filter", {}),
                }
            if google:
                self.exposed_entities["google"] = google
        print(f"   Exposed entities: {len(self.exposed_entities)} assistants configured")


class LogAnalyzer:
    """Analyzes HA logs with categorization per component."""

    def __init__(self):
        self.errors: list[dict] = []
        self.warnings: list[dict] = []
        self.error_patterns: dict[str, dict] = defaultdict(
            lambda: {
                "count": 0,
                "category": "",
                "components": Counter(),
                "integrations": Counter(),
                "first_seen": None,
                "last_seen": None,
                "sample_message": "",
                "affected_entities": set(),
            }
        )
        self.component_errors: Counter = Counter()
        self.integration_errors: Counter = Counter()  # NOWE
        self.affected_entities: set[str] = set()
        self.api_errors: list[dict] = []
        self.startup_errors: list[dict] = []  # NOWE

    def analyze(self, hours: int = constants.LOG_HOURS_BACK):
        """
        Analyzes logs from the last X hours.
        Based on test_real_ha.py get_log_insights.
        """
        print(f"Analyzing logs (last {hours}h)...")

        log_path = Path(constants.HA_CONFIG_PATH) / "home-assistant.log"
        if not log_path.exists():
            print("   Warning: Missing home-assistant.log file")
            return

        cutoff = datetime.now() - timedelta(hours=hours)

        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-15000:]  # More lines
        except Exception as e:
            print(f"   Error reading log: {e}")
            return

        # Detect startup
        startup_cutoff = None

        for line in lines:
            # Detect startup marker
            if "Starting Home Assistant" in line or "homeassistant.bootstrap" in line:
                ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if ts_match:
                    try:
                        startup_cutoff = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass

            self._parse_line(line, cutoff, startup_cutoff)

        print(f"   Errors: {len(self.errors)}, Warnings: {len(self.warnings)}")
        print(f"   Unique patterns: {len(self.error_patterns)}")
        print(f"   Affected entities: {len(self.affected_entities)}")
        print(f"   Startup errors: {len(self.startup_errors)}")

        self._collect_notifications()

    def _parse_line(self, line, cutoff, startup_cutoff):
        """Parses single log line with extended categorization."""
        # Timestamp
        ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        timestamp = None
        if ts_match:
            try:
                timestamp = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
                if timestamp < cutoff:
                    return
            except Exception:
                pass

        # Level
        is_error = " ERROR " in line or "ERROR:" in line
        is_warning = " WARNING " in line or "WARNING:" in line

        if not is_error and not is_warning:
            return

        # Component - search in square brackets
        comp_match = re.search(r"\[([^\]]+)\]", line)
        component = comp_match.group(1) if comp_match else "unknown"

        # Extract integration domain from component
        integration = self._extract_integration(component)

        # Extract entities
        entities = set(constants.ENTITY_PATTERN.findall(line))
        self.affected_entities.update(entities)

        # Message
        message = line.split("]")[-1].strip() if "]" in line else line

        # API errors
        if re.search(r"(429|500|502|503|504|timeout|rate.?limit)", line, re.IGNORECASE):
            self.api_errors.append(
                {
                    "component": component,
                    "integration": integration,
                    "message": message[:150],
                    "timestamp": ts_match.group(1) if ts_match else None,
                }
            )

        log_entry = {
            "component": component,
            "integration": integration,
            "message": message[:200],
            "entities": list(entities),
            "timestamp": ts_match.group(1) if ts_match else None,
        }

        if is_error:
            self.errors.append(log_entry)
            self.component_errors[component] += 1
            self.integration_errors[integration] += 1

            # Startup error?
            if startup_cutoff and timestamp and timestamp <= startup_cutoff + timedelta(minutes=5):
                self.startup_errors.append(log_entry)

            # Pattern grouping
            pattern = self._normalize_pattern(message)
            pdata = self.error_patterns[pattern]
            pdata["count"] += 1
            pdata["category"] = self._categorize_error(component, message)
            pdata["components"][component] += 1
            pdata["integrations"][integration] += 1
            pdata["affected_entities"].update(entities)

            if not pdata["first_seen"]:
                pdata["first_seen"] = ts_match.group(1) if ts_match else "unknown"
            pdata["last_seen"] = ts_match.group(1) if ts_match else "unknown"

            if not pdata["sample_message"]:
                pdata["sample_message"] = message[:200]

        elif is_warning:
            self.warnings.append(log_entry)

    def _extract_integration(self, component: str) -> str:
        """Extracts integration name from component."""
        if "components." in component:
            parts = component.split("components.")
            if len(parts) > 1:
                return parts[1].split(".")[0]
        if "custom_components." in component:
            parts = component.split("custom_components.")
            if len(parts) > 1:
                return f"custom:{parts[1].split('.')[0]}"
        if "." in component:
            return component.split(".")[-1]
        return component

    def _normalize_pattern(self, msg: str) -> str:
        """Normalizes message for grouping."""
        normalized = msg
        normalized = re.sub(r"\d+\.\d+\.\d+\.\d+", "<IP>", normalized)
        normalized = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", "<TS>", normalized)
        normalized = re.sub(
            r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}",
            "<UUID>",
            normalized,
        )
        normalized = re.sub(r"\d+", "<N>", normalized)
        normalized = re.sub(r"'[^']{20,}'", "'<LONG_STR>'", normalized)
        return normalized[:150].strip()

    def _categorize_error(self, component: str, message: str) -> str:
        """Categorizes error type."""
        msg_lower = message.lower()

        if "timeout" in msg_lower or "timed out" in msg_lower:
            return "timeout"
        elif "connection" in msg_lower or "connect" in msg_lower or "refused" in msg_lower:
            return "connection"
        elif "unavailable" in msg_lower or "not available" in msg_lower:
            return "unavailable"
        elif (
            "permission" in msg_lower or "access denied" in msg_lower or "unauthorized" in msg_lower
        ):
            return "permission"
        elif "api" in msg_lower or "http" in msg_lower or "429" in msg_lower or "500" in msg_lower:
            return "api"
        elif "template" in msg_lower or "jinja" in msg_lower:
            return "template"
        elif "config" in msg_lower or "configuration" in msg_lower or "yaml" in msg_lower:
            return "configuration"
        elif "not found" in msg_lower or "does not exist" in msg_lower:
            return "not_found"
        elif "authentication" in msg_lower or "auth" in msg_lower:
            return "authentication"
        elif "setup" in msg_lower or "initialize" in msg_lower:
            return "setup"
        return "other"

    def get_recommendations(self) -> list[dict]:
        """
        Generates recommendations per component/integration.
        Based on test_real_ha.py analyze_log_errors.
        """
        recommendations = []

        # Error categorization
        categories = Counter(p["category"] for p in self.error_patterns.values())

        category_recommendations = {
            "timeout": (
                "high",
                "[TIMING] Many timeouts - check network connections and system load",
            ),
            "connection": (
                "high",
                " Connection issues - check network and device availability",
            ),
            "unavailable": (
                "high",
                " Many unavailable entities - check devices and integrations",
            ),
            "template": ("medium", "[LOGS] Template errors - check Jinja2 syntax"),
            "api": (
                "medium",
                "[NETWORK] API issues - check rate limits and service availability",
            ),
            "configuration": ("medium", " Configuration errors - check YAML files"),
            "authentication": (
                "high",
                "[SECURITY] Authentication issues - check tokens and passwords",
            ),
            "setup": (
                "high",
                " Initialization errors - check integration configuration",
            ),
        }

        for category, count in categories.items():
            if count >= 3 and category in category_recommendations:
                priority, msg = category_recommendations[category]
                recommendations.append(
                    {
                        "priority": priority,
                        "issue": f"category_{category}",
                        "message": f"{msg} ({count} occurrences)",
                        "count": count,
                        "category": category,
                    }
                )

        # NEW: Recommendations per integration
        for integration, count in self.integration_errors.most_common(5):
            if count >= 5:
                recommendations.append(
                    {
                        "priority": "high" if count >= 20 else "medium",
                        "issue": f"integration_{integration}",
                        "message": f"[MAINTENANCE] Integration '{integration}' generates {count} errors - requires attention",
                        "count": count,
                        "integration": integration,
                    }
                )

        # Startup errors
        if len(self.startup_errors) > 5:
            recommendations.append(
                {
                    "priority": "high",
                    "issue": "startup_errors",
                    "message": f" {len(self.startup_errors)} errors during startup - may cause problems",
                    "count": len(self.startup_errors),
                }
            )

        return sorted(recommendations, key=lambda x: 0 if x["priority"] == "high" else 1)

    def _collect_notifications(self):
        """Collect persistent notification data."""
        self.notifications = []
        result = make_ha_request("/api/states")
        if result.get("success"):
            for state in result.get("data", []):
                if state["entity_id"].startswith("persistent_notification."):
                    attrs = state.get("attributes", {})
                    self.notifications.append(
                        {
                            "title": attrs.get("title", state["entity_id"]),
                            "message": attrs.get("message", ""),
                            "created_at": attrs.get("created_at"),
                            "notification_id": attrs.get("notification_id"),
                        }
                    )
        print(f"   Active notifications: {len(self.notifications)}")


class HistoryAnalyzer:
    """Analyzes entity change history."""

    def __init__(self, registry: RegistryCollector):
        self.registry = registry
        self.recent_changes: list[dict] = []
        self.change_frequency: dict[str, int] = Counter()

    def analyze(self, hours: int = 1, batch_size: int = 25):
        """
        Analyzes recent entity changes.
        Fetches history in batches of batch_size entities to avoid URL limits.
        """
        print(f"Analyzing change history (last {hours}h)...")

        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(hours=hours)
        start_time_str = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        entity_ids = self.registry.get_entity_ids()

        if not entity_ids:
            print("   No entities to analyze")
            return

        # Helper function for batching
        def batch_iterable(iterable, size):
            it = iter(iterable)
            while True:
                batch = list(islice(it, size))
                if not batch:
                    break
                yield batch

        # Store all results
        all_history = []

        for batch in batch_iterable(entity_ids, batch_size):
            filter_param = ",".join(batch)
            endpoint = f"/api/history/period/{start_time_str}?filter_entity_id={filter_param}"

            result = make_ha_request(endpoint, timeout=30)
            if not result["success"]:
                print(f"   Failed to fetch history for batch: {filter_param}")
                print(f"       {result.get('error')}")
                continue

            all_history.extend(result["data"])

        if not all_history:
            print("   No history fetched")
            return

        # Parsing history (your existing logic)
        self.recent_changes = []
        self.change_frequency = Counter()
        self.entity_history: dict[str, list[dict]] = defaultdict(list)

        for entity_history in all_history:
            if not entity_history:
                continue

            for i, state in enumerate(entity_history):
                entity_id = state.get("entity_id")
                if not entity_id:
                    continue

                # Skip first entry (initial state)
                if i == 0:
                    continue

                self.change_frequency[entity_id] += 1
                self.entity_history[entity_id].append(state)

                if len(self.recent_changes) < 100:
                    self.recent_changes.append(
                        {
                            "entity_id": entity_id,
                            "state": state.get("state"),
                            "last_changed": state.get("last_changed"),
                            "previous_state": entity_history[i - 1].get("state") if i > 0 else None,
                        }
                    )

        # Sort by time descending
        self.recent_changes.sort(key=lambda x: x.get("last_changed", ""), reverse=True)

        print(f"   Recent changes: {len(self.recent_changes)}")
        print(f"   Entities with changes: {len(self.change_frequency)}")

        # Per-entity statistics
        self.entity_stats: dict[str, dict] = {}
        for entity_id, entries in self.entity_history.items():
            if not entries:
                continue
            states = [
                e.get("state") for e in entries if e.get("state") not in ("unknown", "unavailable")
            ]
            numeric_values = []
            for s in states:
                try:
                    numeric_values.append(float(s))
                except (ValueError, TypeError):
                    pass

            entity_info = (
                self.registry.get_entity_info(entity_id)
                if hasattr(self, "registry") and self.registry
                else {}
            )
            self.entity_stats[entity_id] = {
                "name": entity_info.get("friendly_name", entity_id) if entity_info else entity_id,
                "state": entity_info.get("state", "unknown") if entity_info else "unknown",
                "platform": entity_info.get("platform", "unknown") if entity_info else "unknown",
                "area": entity_info.get("area_name", "Unassigned") if entity_info else "Unassigned",
                "change_count": self.change_frequency.get(entity_id, 0),
                "min": min(numeric_values) if numeric_values else None,
                "max": max(numeric_values) if numeric_values else None,
                "avg": round(sum(numeric_values) / len(numeric_values), 2)
                if numeric_values
                else None,
            }

        print(f"   Recent changes: {len(self.recent_changes)}")
        print(f"   Entities with changes: {len(self.change_frequency)}")


class PersonAnalyzer:
    """Collects person entities, linked trackers, and location data."""

    def __init__(self, registry: RegistryCollector):
        self.registry = registry
        self.persons: list[dict] = []
        self.person_states: dict[str, dict] = {}
        self.trackers: dict[str, list[dict]] = defaultdict(list)
        self.tracker_states: dict[str, dict] = {}

    def collect(self):
        """Collect person and tracker data from registry and API states."""
        print("Collecting person / tracking data...")

        # Find person entities from states
        for state in self.registry.states:
            eid = state.get("entity_id", "")
            if eid.startswith("person."):
                self.person_states[eid] = state
                attrs = state.get("attributes", {})
                self.persons.append(
                    {
                        "entity_id": eid,
                        "name": attrs.get("friendly_name", eid),
                        "state": state.get("state"),
                        "user_id": attrs.get("user_id"),
                        "latitude": attrs.get("latitude"),
                        "longitude": attrs.get("longitude"),
                        "source": attrs.get("source"),
                    }
                )
                # Get linked trackers
                trackers = attrs.get("device_trackers", [])
                for tid in trackers:
                    self.trackers[eid].append({"entity_id": tid})

        # Get tracker states
        for state in self.registry.states:
            eid = state.get("entity_id", "")
            if eid.startswith("device_tracker."):
                self.tracker_states[eid] = {
                    "state": state.get("state"),
                    "last_updated": state.get("last_updated"),
                    "battery": state.get("attributes", {}).get("battery"),
                    "source_type": state.get("attributes", {}).get("source_type", "unknown"),
                }
                # Link to person
                for person_entities in self.trackers.values():
                    for tracker in person_entities:
                        if tracker["entity_id"] == eid:
                            tracker.update(self.tracker_states[eid])
                            break

        print(f"   Persons: {len(self.persons)}, Trackers: {len(self.tracker_states)}")


class ZoneAnalyzer:
    """Collects zone definitions and presence mapping."""

    def __init__(self, registry: RegistryCollector):
        self.registry = registry
        self.zones: list[dict] = []
        self.zone_states: dict[str, dict] = {}
        self.persons_in_zones: dict[str, list[str]] = defaultdict(list)

    def collect(self):
        """Collect zone data from registry and API states."""
        print("Collecting zone / geofence data...")

        # Load zone registry from config entries
        zone_reg = load_registry("core.config_entries")
        entries = zone_reg.get("data", {}).get("entries", [])

        for entry in entries:
            if entry.get("domain") == "zone":
                zone_id = f"zone.{entry.get('title', '').lower().replace(' ', '_')}"
                data = entry.get("data", {})
                self.zones.append(
                    {
                        "entity_id": zone_id,
                        "name": entry.get("title", "Unknown Zone"),
                        "latitude": data.get("latitude"),
                        "longitude": data.get("longitude"),
                        "radius": data.get("radius", 100),
                        "passive": data.get("passive", False),
                    }
                )

        # Also get zone states from API
        for state in self.registry.states:
            eid = state.get("entity_id", "")
            if eid.startswith("zone."):
                attrs = state.get("attributes", {})
                self.zone_states[eid] = {
                    "entity_id": eid,
                    "name": attrs.get("friendly_name", eid),
                    "state": state.get("state"),
                    "latitude": attrs.get("latitude"),
                    "longitude": attrs.get("longitude"),
                    "radius": attrs.get("radius"),
                }
                # Count persons in this zone
                for person_state in self.registry.states:
                    if (
                        person_state["entity_id"].startswith("person.")
                        and person_state["state"] == eid
                    ):
                        self.persons_in_zones[eid].append(person_state["entity_id"])

        # Merge registry zones with state zones
        seen = {z["entity_id"] for z in self.zones}
        for eid, zs in self.zone_states.items():
            if eid not in seen:
                self.zones.append(zs)

        print(f"   Zones: {len(self.zones)}")
        for z in self.zones:
            persons_count = len(self.persons_in_zones.get(z["entity_id"], []))
            if persons_count > 0:
                print(f"   {z.get('name', z.get('entity_id'))}: {persons_count} persons")


class EnergyAnalyzer:
    """Collects energy dashboard data and consumption analysis."""

    def __init__(self, registry: RegistryCollector):
        self.registry = registry
        self.energy_data: dict[str, Any] = {}
        self.consumption_by_device: dict[str, dict] = {}
        self.energy_sensors: list[dict] = []

    def collect(self):
        """Collect energy data from API."""
        print("Collecting energy dashboard data...")

        # Fetch energy data from API
        energy_result = make_ha_request("/api/energy/dashboard")
        if energy_result.get("success"):
            self.energy_data = energy_result.get("data", {})
            print(f"   Energy data: {len(self.energy_data)} sections")
        else:
            print(f"   Energy API unavailable: {energy_result.get('error', 'unknown')}")
            self.energy_data = {"unavailable": True}

        # Collect energy-related sensors from state
        for state in self.registry.states:
            eid = state.get("entity_id", "")
            attrs = state.get("attributes", {})
            device_class = attrs.get("device_class", "")

            if (
                device_class in ("energy", "power", "gas", "water")
                or "energy" in eid.lower()
                or "power" in eid.lower()
            ):
                unit = attrs.get("unit_of_measurement", "")
                if device_class == "energy" or "kwh" in unit.lower() or "wh" in unit.lower():
                    try:
                        val = float(state.get("state", 0))
                    except (ValueError, TypeError):
                        val = 0

                    self.energy_sensors.append(
                        {
                            "entity_id": eid,
                            "name": attrs.get("friendly_name", eid),
                            "state": state.get("state"),
                            "value_numeric": val,
                            "unit": unit,
                            "device_class": device_class,
                        }
                    )

                    # Group by device
                    device_name = attrs.get("friendly_name", eid).split(" ")[0]
                    if device_name not in self.consumption_by_device:
                        self.consumption_by_device[device_name] = {"total": 0, "sensors": []}
                    self.consumption_by_device[device_name]["total"] += val
                    self.consumption_by_device[device_name]["sensors"].append(eid)

        self.energy_sensors.sort(key=lambda x: x.get("value_numeric", 0), reverse=True)
        print(
            f"   Energy sensors: {len(self.energy_sensors)} ({len(self.consumption_by_device)} devices)"
        )


class HelperAnalyzer:
    """Collects timers, counters, and input helpers."""

    def __init__(self, registry: RegistryCollector):
        self.registry = registry
        self.timers: list[dict] = []
        self.counters: list[dict] = []
        self.input_booleans: list[dict] = []
        self.input_numbers: list[dict] = []
        self.input_texts: list[dict] = []
        self.input_selects: list[dict] = []
        self.input_datetimes: list[dict] = []
        self.input_buttons: list[dict] = []
        self.nfc_tags: list[dict] = []

    def collect(self):
        """Collect helper entity data from states and registry."""
        print("Collecting helper entities...")

        for state in self.registry.states:
            eid = state.get("entity_id", "")
            attrs = state.get("attributes", {})
            base = {
                "entity_id": eid,
                "name": attrs.get("friendly_name", eid),
                "state": state.get("state"),
            }

            if eid.startswith("timer."):
                base["duration"] = attrs.get("duration")
                base["remaining"] = attrs.get("remaining")
                base["finishes_at"] = attrs.get("finishes_at")
                self.timers.append(base)
            elif eid.startswith("counter."):
                base["min"] = attrs.get("min")
                base["max"] = attrs.get("max")
                base["step"] = attrs.get("step")
                self.counters.append(base)
            elif eid.startswith("input_boolean."):
                self.input_booleans.append(base)
            elif eid.startswith("input_number."):
                base["min"] = attrs.get("min")
                base["max"] = attrs.get("max")
                base["step"] = attrs.get("step")
                base["unit"] = attrs.get("unit_of_measurement")
                self.input_numbers.append(base)
            elif eid.startswith("input_text."):
                self.input_texts.append(base)
            elif eid.startswith("input_select."):
                base["options"] = attrs.get("options", [])
                self.input_selects.append(base)
            elif eid.startswith("input_datetime."):
                base["has_date"] = attrs.get("has_date")
                base["has_time"] = attrs.get("has_time")
                self.input_datetimes.append(base)
            elif eid.startswith("input_button."):
                self.input_buttons.append(base)

        # NFC tags from core.tag registry
        tag_reg = load_registry("core.tag")
        tags_data = tag_reg.get("data", {}).get("tags", [])
        if isinstance(tags_data, list):
            for tag in tags_data:
                if isinstance(tag, dict):
                    self.nfc_tags.append(
                        {
                            "id": tag.get("id", "unknown"),
                            "name": tag.get("name", tag.get("id", "unknown")),
                        }
                    )

        print(f"   Timers: {len(self.timers)}, Counters: {len(self.counters)}")
        print(f"   Input booleans: {len(self.input_booleans)}, numbers: {len(self.input_numbers)}")
        print(f"   Input texts: {len(self.input_texts)}, selects: {len(self.input_selects)}")
        print(
            f"   Input datetimes: {len(self.input_datetimes)}, buttons: {len(self.input_buttons)}"
        )
        print(f"   NFC tags: {len(self.nfc_tags)}")


class ServiceCatalogAnalyzer:
    """Collects available Home Assistant services."""

    def __init__(self, registry: RegistryCollector):
        self.registry = registry
        self.services: dict[str, list[dict]] = {}
        self.total_services = 0

    def collect(self):
        """Collect available services from API."""
        print("Collecting service catalog...")

        result = make_ha_request("/api/services")
        if result.get("success"):
            data = result.get("data", [])
            for domain_info in data:
                domain = domain_info.get("domain", "unknown")
                services = domain_info.get("services", {})
                domain_services = []
                for svc_name, svc_info in services.items():
                    domain_services.append(
                        {
                            "name": svc_name,
                            "description": svc_info.get("description", ""),
                            "fields": svc_info.get("fields", {}),
                        }
                    )
                    self.total_services += 1
                self.services[domain] = domain_services
            print(f"   Services: {self.total_services} across {len(self.services)} domains")
        else:
            print(f"   Service API unavailable: {result.get('error', 'unknown')}")


class HacsAnalyzer:
    """Collects HACS data and custom components."""

    def __init__(self, registry: RegistryCollector):
        self.registry = registry
        self.hacs_repos: list[dict] = []
        self.custom_components: list[dict] = []
        self.custom_components_dir = os.path.join(constants.HA_CONFIG_PATH, "custom_components")

    def collect(self):
        """Collect HACS and custom component data."""
        print("Collecting HACS / custom components...")

        # Load HACS data from storage
        hacs_data = load_registry("hacs.repositories")
        repos = hacs_data.get("data", {}).get("repositories", [])
        if isinstance(repos, list):
            for repo in repos:
                if isinstance(repo, dict):
                    self.hacs_repos.append(
                        {
                            "name": repo.get("name", "Unknown"),
                            "category": repo.get("category", "unknown"),
                            "installed_version": repo.get("installed_version"),
                            "available_version": repo.get("available_version"),
                            "status": repo.get("status"),
                        }
                    )
        elif isinstance(repos, dict):
            for rid, repo in repos.items():
                if isinstance(repo, dict):
                    self.hacs_repos.append(
                        {
                            "name": repo.get("name", rid),
                            "category": repo.get("category", "unknown"),
                            "installed_version": repo.get("installed_version"),
                            "available_version": repo.get("available_version"),
                            "status": repo.get("status"),
                        }
                    )

        # Collect custom components
        if os.path.isdir(self.custom_components_dir):
            for item in sorted(os.listdir(self.custom_components_dir)):
                item_path = os.path.join(self.custom_components_dir, item)
                if os.path.isdir(item_path):
                    manifest_path = os.path.join(item_path, "manifest.json")
                    manifest = {}
                    if os.path.isfile(manifest_path):
                        try:
                            with open(manifest_path) as mf:
                                manifest = json.load(mf)
                        except (OSError, json.JSONDecodeError):
                            pass

                    self.custom_components.append(
                        {
                            "name": item,
                            "version": manifest.get("version", "unknown"),
                            "domain": manifest.get("domain", item),
                            "requirements": manifest.get("requirements", []),
                            "dependencies": manifest.get("dependencies", []),
                        }
                    )

        print(
            f"   HACS repos: {len(self.hacs_repos)}, Custom components: {len(self.custom_components)}"
        )


class CacheAnalyzer:
    """Analyzes registry cache performance."""

    def __init__(self, registry: RegistryCollector):
        self.registry = registry
        self.cache_stats: dict[str, Any] = {}

    def collect(self):
        """Collects cache statistics."""
        self.cache_stats = get_cache_stats()
        hit_rate = self.cache_stats.get("hit_rate_percent", 0)
        print(f"Collecting cache statistics... Hit rate: {hit_rate}%")


# --- REPORT GENERATOR ---
