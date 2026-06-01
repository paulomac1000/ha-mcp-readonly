"""Markdown report formatter for context generation."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime

# Type-only imports to avoid circular dependency issues at runtime
from typing import TYPE_CHECKING

from . import constants
from .utils import is_ignorable_entity

if TYPE_CHECKING:
    pass


class ReportGenerator:
    """Generates final MD report."""

    def __init__(
        self,
        registry,
        automation,
        dashboard,
        logs,
        templates,
        history,
        persons=None,
        zones=None,
        energy=None,
        helpers=None,
        services=None,
        hacs=None,
    ):
        self.registry = registry
        self.automation = automation
        self.dashboard = dashboard
        self.logs = logs
        self.templates = templates
        self.history = history
        self.persons = persons
        self.zones = zones
        self.energy = energy
        self.helpers = helpers
        self.services = services
        self.hacs = hacs

    def generate(self, output_file: str):
        """Generates MD file."""
        print(f"\nGenerating {output_file}...")

        with open(output_file, "w", encoding="utf-8") as f:
            self._write_header(f)
            self._write_executive_summary(f)
            self._write_system_health(f)
            self._write_integration_status(f)
            self._write_topology(f)
            self._write_automation_logic(f)
            self._write_entity_dependency_graph(f)
            self._write_conflict_analysis(f)
            self._write_template_entities(f)
            self._write_persons_and_tracking(f)
            self._write_zones_and_geofencing(f)
            self._write_energy_dashboard(f)
            self._write_helper_inventory(f)
            self._write_services_catalog(f)
            self._write_hacs_and_components(f)
            self._write_dashboard_usage(f)
            self._write_log_analysis(f)
            self._write_recent_changes(f)
            self._write_quick_reference(f)

        print(f"Success. File {output_file} ready.")

    def _write_header(self, f):
        """Document header."""
        f.write("# Home Assistant Context for AI (v1.0)\n\n")
        f.write(f"> **Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"> **HA Instance:** {constants.HA_URL}\n")
        f.write(f"> **Config Path:** {constants.HA_CONFIG_PATH}\n")
        f.write("> **Generator Version:** 1.0\n\n")
        f.write("---\n\n")

    def _write_executive_summary(self, f):
        """Executive summary."""
        f.write("## 📋 Executive Summary\n\n")

        total_entities = len(self.registry.states)
        unavailable = sum(
            1
            for s in self.registry.states
            if s.get("state") == "unavailable" and not is_ignorable_entity(s.get("entity_id", ""))
        )

        f.write("### System Stats\n")
        f.write("| Metric | Value |\n")
        f.write("|--------|-------|\n")
        f.write(f"| Total Entities | {total_entities} |\n")
        f.write(f"| Devices | {len(self.registry.devices)} |\n")
        f.write(f"| Areas | {len(self.registry.areas)} |\n")
        f.write(f"| Automations | {len(self.automation.automation_analysis)} |\n")
        f.write(f"| Scripts | {len(self.automation.script_analysis)} |\n")
        f.write(f"| Scenes | {len(self.automation.scene_analysis)} |\n")
        f.write(f"| Template Entities | {len(self.templates.template_entities)} |\n")
        f.write(f"| Integrations | {len(self.registry.config_entries)} |\n")
        f.write(f"| Blueprints | {len(self.automation.blueprints)} |\n")
        f.write(f"| Dashboards | {len(self.dashboard.dashboards_found)} |\n\n")

        # Health score
        health_score = 100
        deductions = []

        if unavailable > 0:
            deduction = min(30, unavailable * 2)
            health_score -= deduction
            deductions.append(f"-{deduction} (unavailable)")

        if len(self.automation.ghost_entities) > 0:
            deduction = min(20, len(self.automation.ghost_entities) * 2)
            health_score -= deduction
            deductions.append(f"-{deduction} (ghost entities)")

        if len(self.logs.errors) > 10:
            deduction = min(20, len(self.logs.errors) // 2)
            health_score -= deduction
            deductions.append(f"-{deduction} (log errors)")

        if len(self.automation.conflicting_entities) > 0:
            deduction = min(10, len(self.automation.conflicting_entities))
            health_score -= deduction
            deductions.append(f"-{deduction} (conflicts)")

        health_score = max(0, health_score)
        health_emoji = "" if health_score >= 80 else "" if health_score >= 60 else ""

        f.write(f"### Health Score: {health_emoji} {health_score}%\n")
        if deductions:
            f.write(f"*Deductions: {', '.join(deductions)}*\n")
        f.write("\n")

        # Critical issues
        critical = []
        if unavailable > 10:
            critical.append(f" **{unavailable} unavailable entities**")
        if len(self.automation.ghost_entities) > 5:
            critical.append(
                f"👻 **{len(self.automation.ghost_entities)} ghost entities** in automations"
            )
        if len(self.automation.conflicting_entities) > 0:
            critical.append(
                f"⚡ **{len(self.automation.conflicting_entities)} potential conflicts** between automations/scripts/scenes"
            )
        if len(self.dashboard.missing_entities) > 0:
            critical.append(
                f" **{len(self.dashboard.missing_entities)} non-existent entities** in dashboards"
            )

        disabled_autos = sum(1 for a in self.automation.automation_analysis if a.get("is_disabled"))
        if disabled_autos > 5:
            critical.append(f" **{disabled_autos} disabled automations**")

        if len(self.logs.startup_errors) > 5:
            critical.append(f" **{len(self.logs.startup_errors)} errors during startup**")

        if critical:
            f.write("### Critical Issues\n")
            for issue in critical:
                f.write(f"- {issue}\n")
            f.write("\n")
        else:
            f.write("### No Critical Issues\n\n")

        f.write("---\n\n")

    def _write_system_health(self, f):
        """System health section."""
        f.write("## 🚨 1. System Health & Issues\n\n")

        # Ghost entities - rozszerzone o dashboardy
        f.write("### 👻 Ghost Entities\n")
        f.write(
            "*Entities used in automations/scripts/dashboards, but non-existent in the system.*\n\n"
        )

        # Merge ghost z automation i dashboard
        all_ghosts = dict(self.automation.ghost_entities)
        for eid, sources in self.dashboard.missing_entities.items():
            if eid in all_ghosts:
                all_ghosts[eid].extend([f"dashboard: {s}" for s in sources])
            else:
                all_ghosts[eid] = [f"dashboard: {s}" for s in sources]

        if all_ghosts:
            f.write("| Entity ID | Used In | Source Type |\n")
            f.write("|-----------|---------|-------------|\n")
            for eid, sources in sorted(all_ghosts.items()):
                sources_str = ", ".join(sources[:3])
                if len(sources) > 3:
                    sources_str += f" +{len(sources) - 3} more"

                source_types = set()
                for s in sources:
                    if s.startswith("automation:"):
                        source_types.add("automation")
                    elif s.startswith("script."):
                        source_types.add("script")
                    elif s.startswith("scene."):
                        source_types.add("scene")
                    elif s.startswith("dashboard:"):
                        source_types.add("dashboard")

                f.write(f"| `{eid}` | {sources_str} | {', '.join(source_types)} |\n")
            f.write("\n")
        else:
            f.write(" *No ghost entities.*\n\n")

        # Unavailable by integration
        f.write("### Unavailable Entities by Integration\n\n")

        unavailable_by_integration = defaultdict(
            lambda: {"count": 0, "entities": [], "devices": set()}
        )

        for state in self.registry.states:
            if state.get("state") != "unavailable":
                continue
            eid = state.get("entity_id", "")
            if is_ignorable_entity(eid):
                continue

            integration = self.registry.get_integration_domain(eid)
            unavailable_by_integration[integration]["count"] += 1

            if len(unavailable_by_integration[integration]["entities"]) < 5:
                unavailable_by_integration[integration]["entities"].append(eid)

            device_id = self.registry.entity_to_device.get(eid)
            if device_id:
                device_name = self.registry._get_device_name(device_id)
                unavailable_by_integration[integration]["devices"].add(device_name)

        if unavailable_by_integration:
            f.write("| Integration | Count | Sample Entities | Affected Devices |\n")
            f.write("|-------------|-------|-----------------|------------------|\n")

            for integration, data in sorted(
                unavailable_by_integration.items(),
                key=lambda x: x[1]["count"],
                reverse=True,
            )[:15]:
                entities_str = ", ".join(f"`{e}`" for e in data["entities"][:2])
                if data["count"] > 2:
                    entities_str += f" +{data['count'] - 2}"
                devices_str = ", ".join(list(data["devices"])[:2])
                if len(data["devices"]) > 2:
                    devices_str += f" +{len(data['devices']) - 2}"
                f.write(f"| {integration} | {data['count']} | {entities_str} | {devices_str} |\n")
            f.write("\n")
        else:
            f.write(" *All entities available.*\n\n")

        # Disabled automations
        disabled = [a for a in self.automation.automation_analysis if a.get("is_disabled")]
        if disabled:
            f.write("### Disabled Automations\n\n")
            f.write("| Alias | ID | Trigger Platforms | Last Triggered |\n")
            f.write("|-------|-----|------------------|----------------|\n")
            for auto in disabled[:20]:
                triggers = ", ".join(auto.get("trigger_platforms", [])[:3])
                last = auto.get("last_triggered", "Never")
                if last and last != "Never":
                    last = last[:19]  # Trim timestamp
                f.write(f"| {auto['alias'][:40]} | {auto['id']} | {triggers} | {last} |\n")
            if len(disabled) > 20:
                f.write(f"\n*... and {len(disabled) - 20} more*\n")
            f.write("\n")

    def _write_integration_status(self, f):
        """Integration status with config entry health."""
        f.write("## 2. Integration Status\n\n")

        # Config entry health
        f.write("### Config Entry Health\n\n")

        entries_by_domain = defaultdict(list)
        for entry in self.registry.config_entries:
            domain = entry.get("domain", "unknown")
            entry_id = entry.get("entry_id")
            health = self.registry.config_entry_health.get(entry_id, {})

            entries_by_domain[domain].append(
                {
                    "entry_id": entry_id,
                    "title": entry.get("title", ""),
                    "disabled_by": entry.get("disabled_by"),
                    "state": entry.get("state", "loaded"),
                    "health": health,
                }
            )

        f.write("| Domain | Entries | Total Entities | Unavailable | Health | Status |\n")
        f.write("|--------|---------|----------------|-------------|--------|--------|\n")

        for domain in sorted(entries_by_domain.keys()):
            entries = entries_by_domain[domain]
            total_entities = sum(e["health"].get("total_entities", 0) for e in entries)
            unavailable = sum(e["health"].get("unavailable", 0) for e in entries)

            if total_entities > 0:
                health_pct = int(((total_entities - unavailable) / total_entities) * 100)
            else:
                health_pct = 100

            health_emoji = "" if health_pct >= 95 else "" if health_pct >= 80 else ""

            disabled_count = sum(1 for e in entries if e["disabled_by"])
            status = "OK" if disabled_count == 0 else f"{disabled_count} disabled"

            f.write(
                f"| {domain} | {len(entries)} | {total_entities} | {unavailable} | {health_emoji} {health_pct}% | {status} |\n"
            )

        f.write("\n")

        # Entity health by domain
        f.write("### Entity Health by Domain\n\n")

        domain_stats = defaultdict(lambda: {"total": 0, "unavailable": 0, "unknown": 0, "ok": 0})

        for state in self.registry.states:
            eid = state.get("entity_id", "")
            if is_ignorable_entity(eid):
                continue

            domain = eid.split(".")[0]
            state_val = state.get("state", "")

            domain_stats[domain]["total"] += 1

            if state_val == "unavailable":
                domain_stats[domain]["unavailable"] += 1
            elif state_val == "unknown":
                domain_stats[domain]["unknown"] += 1
            else:
                domain_stats[domain]["ok"] += 1

        f.write("| Domain | Total | OK | Unavailable | Unknown | Health |\n")
        f.write("|--------|-------|-----|-------------|---------|--------|\n")

        for domain, stats in sorted(
            domain_stats.items(), key=lambda x: x[1]["total"], reverse=True
        )[:25]:
            total = stats["total"]
            ok = stats["ok"]
            unavail = stats["unavailable"]
            unknown = stats["unknown"]

            health_pct = (ok / total) * 100 if total > 0 else 100
            health = "" if health_pct >= 95 else "" if health_pct >= 80 else ""

            f.write(
                f"| {domain} | {total} | {ok} | {unavail} | {unknown} | {health} {health_pct:.0f}% |\n"
            )

        f.write("\n")

    def _write_topology(self, f):
        """Area and device topology section."""
        f.write("## 🏠 3. Area & Device Topology\n\n")

        # Building topology
        topology = defaultdict(
            lambda: {
                "devices": defaultdict(list),
                "orphan_entities": [],
                "stats": {"total": 0, "unavailable": 0},
            }
        )

        for state in self.registry.states:
            eid = state.get("entity_id", "")

            if eid.startswith(("update.", "persistent_notification.", "conversation.")):
                continue

            entity_info = self.registry.get_entity_info(eid)
            area_name = entity_info["area_name"]
            device_name = entity_info["device_name"]

            topology[area_name]["stats"]["total"] += 1
            if entity_info["state"] == "unavailable":
                topology[area_name]["stats"]["unavailable"] += 1

            simple_entity = {
                "id": eid,
                "state": entity_info["state"],
                "name": entity_info["friendly_name"],
            }
            if entity_info.get("device_class"):
                simple_entity["class"] = entity_info["device_class"]

            if device_name == "Virtual/Service":
                topology[area_name]["orphan_entities"].append(simple_entity)
            else:
                topology[area_name]["devices"][device_name].append(simple_entity)

        # Summary table
        f.write("### Areas Summary\n\n")
        f.write("| Area | Devices | Entities | Unavailable | Health |\n")
        f.write("|------|---------|----------|-------------|--------|\n")

        for area_name in sorted(topology.keys()):
            data = topology[area_name]
            devices = len(data["devices"])
            total = data["stats"]["total"]
            unavail = data["stats"]["unavailable"]
            health_pct = ((total - unavail) / total * 100) if total > 0 else 100
            health = "" if health_pct >= 95 else "" if health_pct >= 80 else ""

            f.write(
                f"| {area_name} | {devices} | {total} | {unavail} | {health} {health_pct:.0f}% |\n"
            )

        f.write("\n")

        # Detailed per area
        for area_name in sorted(topology.keys()):
            area_data = topology[area_name]
            devices = area_data["devices"]
            orphans = area_data["orphan_entities"]

            total_entities = sum(len(ents) for ents in devices.values()) + len(orphans)

            f.write(f"### 📍 {area_name}\n")
            f.write(f"*{len(devices)} devices, {total_entities} entities*\n\n")

            if devices:
                f.write("<details>\n<summary>Devices & Entities</summary>\n\n")

                for device_name, entities in sorted(devices.items()):
                    unavail_count = sum(1 for e in entities if e["state"] == "unavailable")
                    status = f" {unavail_count} unavailable" if unavail_count > 0 else ""

                    f.write(f"**{device_name}** ({len(entities)} entities){status}\n")

                    for e in entities[:5]:
                        state_emoji = (
                            ""
                            if e["state"] == "unavailable"
                            else ""
                            if e["state"] == "unknown"
                            else ""
                        )
                        f.write(f"- `{e['id']}`: {e['state']} {state_emoji}\n")

                    if len(entities) > 5:
                        f.write(f"- *... +{len(entities) - 5} more*\n")
                    f.write("\n")

                f.write("</details>\n\n")

            if orphans:
                f.write(
                    f"<details>\n<summary>Virtual/Service Entities ({len(orphans)})</summary>\n\n"
                )
                for e in orphans[:10]:
                    f.write(f"- `{e['id']}`: {e['state']}\n")
                if len(orphans) > 10:
                    f.write(f"- *... +{len(orphans) - 10} more*\n")
                f.write("\n</details>\n\n")

    def _write_automation_logic(self, f):
        """Automation logic section."""
        f.write("## 🧠 4. Automation Logic\n\n")

        # Summary
        trigger_counts = Counter()
        for auto in self.automation.automation_analysis:
            for platform in auto.get("trigger_platforms", ["unknown"]):
                trigger_counts[platform] += 1

        f.write("### Trigger Platforms Distribution\n\n")
        f.write("| Platform | Count | % |\n")
        f.write("|----------|-------|---|\n")
        total_triggers = sum(trigger_counts.values())
        for platform, count in trigger_counts.most_common(15):
            pct = (count / total_triggers * 100) if total_triggers > 0 else 0
            f.write(f"| {platform} | {count} | {pct:.1f}% |\n")
        f.write("\n")

        # Automations
        f.write(f"### 🤖 Automations ({len(self.automation.automation_analysis)} total)\n\n")

        f.write("<details>\n<summary>Automation List</summary>\n\n")
        f.write("| Alias | Mode | Triggers | Controls | Status | Last Triggered |\n")
        f.write("|-------|------|----------|----------|--------|----------------|\n")

        for auto in self.automation.automation_analysis[:50]:
            triggers = ", ".join(auto.get("trigger_platforms", [])[:2])
            controls = len(auto.get("controlled_entities", []))
            status = " OFF" if auto.get("is_disabled") else " ON"
            alias = auto["alias"][:35] + "..." if len(auto["alias"]) > 35 else auto["alias"]
            last = auto.get("last_triggered", "")
            if last:
                last = last[:16]
            f.write(f"| {alias} | {auto['mode']} | {triggers} | {controls} | {status} | {last} |\n")

        if len(self.automation.automation_analysis) > 50:
            f.write(f"\n*... and {len(self.automation.automation_analysis) - 50} more*\n")

        f.write("\n</details>\n\n")

        # Full JSON
        f.write("<details>\n<summary>Full Automation Data (JSON)</summary>\n\n")
        f.write("```json\n")
        f.write(
            json.dumps(
                self.automation.automation_analysis,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        f.write("\n```\n\n")
        f.write("</details>\n\n")

        # Scripts
        if self.automation.script_analysis:
            f.write(f"### 📜 Scripts ({len(self.automation.script_analysis)} total)\n\n")
            f.write("<details>\n<summary>Script List</summary>\n\n")
            f.write("```json\n")
            f.write(json.dumps(self.automation.script_analysis, indent=2, ensure_ascii=False))
            f.write("\n```\n\n")
            f.write("</details>\n\n")

        # Scenes
        if self.automation.scene_analysis:
            f.write(f"### 🎬 Scenes ({len(self.automation.scene_analysis)} total)\n\n")
            f.write("<details>\n<summary>Scene List</summary>\n\n")
            f.write("```json\n")
            f.write(json.dumps(self.automation.scene_analysis[:30], indent=2, ensure_ascii=False))
            f.write("\n```\n\n")
            f.write("</details>\n\n")

        # Blueprints
        if self.automation.blueprints:
            f.write(f"### 📘 Blueprints ({len(self.automation.blueprints)} total)\n\n")

            f.write("| Name | Domain | Used By | Inputs |\n")
            f.write("|------|--------|---------|--------|\n")

            for bp in self.automation.blueprints:
                path = bp["path"]
                usage = self.automation.blueprint_usage.get(path, [])
                usage_str = ", ".join(usage[:2])
                if len(usage) > 2:
                    usage_str += f" +{len(usage) - 2}"
                inputs = ", ".join(bp.get("inputs", [])[:3])
                if len(bp.get("inputs", [])) > 3:
                    inputs += "..."

                f.write(
                    f"| {bp['name']} | {bp['domain']} | {usage_str or 'Not used'} | {inputs} |\n"
                )

            f.write("\n")

    def _write_entity_dependency_graph(self, f):
        """Entity dependency graph."""
        f.write("## 🕸 5. Entity Dependency Graph\n\n")
        f.write("*Check this section BEFORE modifying an entity, to see what you might break.*\n\n")

        # Merge dependencies
        all_dependencies = {}

        all_entity_ids = (
            set(self.automation.entity_triggered_by.keys())
            | set(self.automation.entity_used_in.keys())
            | set(self.automation.entity_controlled_by.keys())
        )

        for eid in sorted(all_entity_ids):
            deps = {}

            triggered_by = self.automation.entity_triggered_by.get(eid, [])
            if triggered_by:
                deps["triggers_automations"] = triggered_by

            used_in = self.automation.entity_used_in.get(eid, [])
            if used_in:
                deps["used_in"] = used_in

            controlled_by = self.automation.entity_controlled_by.get(eid, [])
            if controlled_by:
                deps["controlled_by"] = controlled_by

            dashboard_usage = self.dashboard.entity_in_dashboards.get(eid, [])
            if dashboard_usage:
                deps["dashboards"] = list(set(d["dashboard"] for d in dashboard_usage))

            if deps:
                all_dependencies[eid] = deps

        # Critical entities
        critical = {}
        for eid, deps in all_dependencies.items():
            total_usage = (
                len(deps.get("triggers_automations", []))
                + len(deps.get("used_in", []))
                + len(deps.get("controlled_by", []))
                + len(deps.get("dashboards", []))
            )
            if total_usage >= 3:
                critical[eid] = deps

        f.write(f"### Critical Entities ({len(critical)} entities used in 3+ places)\n\n")

        if critical:
            f.write("| Entity | Triggers | Used In | Controlled By | Dashboards | Total |\n")
            f.write("|--------|----------|---------|---------------|------------|-------|\n")

            sorted_critical = sorted(
                critical.items(),
                key=lambda x: sum(len(v) for v in x[1].values() if isinstance(v, list)),
                reverse=True,
            )

            for eid, deps in sorted_critical[:25]:
                triggers = len(deps.get("triggers_automations", []))
                used_in = len(deps.get("used_in", []))
                controlled = len(deps.get("controlled_by", []))
                dashboards = len(deps.get("dashboards", []))
                total = triggers + used_in + controlled + dashboards
                f.write(
                    f"| `{eid}` | {triggers} | {used_in} | {controlled} | {dashboards} | {total} |\n"
                )

            f.write("\n")
        else:
            f.write("*No entities used in multiple places.*\n\n")

        # Full map
        f.write(f"### Full Dependency Map ({len(all_dependencies)} entities)\n\n")
        f.write("<details>\n<summary>Click to expand</summary>\n\n")
        f.write("```json\n")
        f.write(json.dumps(all_dependencies, indent=2, ensure_ascii=False))
        f.write("\n```\n\n")
        f.write("</details>\n\n")

    def _write_conflict_analysis(self, f):
        """Conflict analysis - extended to scripts and scenes."""
        f.write("## ⚡ 6. Automation Conflict Analysis\n\n")

        if not self.automation.conflicting_entities:
            f.write(" *No automation conflicts detected.*\n\n")
            return

        f.write(
            "*Entities controlled by more than one automation/script/scene - potential race conditions.*\n\n"
        )

        f.write("| Entity | Automations | Scripts | Scenes | Total | Risk |\n")
        f.write("|--------|-------------|---------|--------|-------|------|\n")

        sorted_conflicts = sorted(
            self.automation.conflicting_entities.items(),
            key=lambda x: x[1]["total_controllers"],
            reverse=True,
        )

        for eid, conflict in sorted_conflicts[:30]:
            autos = len(conflict["controlling_automations"])
            scripts = len(conflict["controlling_scripts"])
            scenes = len(conflict["controlling_scenes"])
            total = conflict["total_controllers"]

            risk = " HIGH" if conflict["race_condition_risk"] else " MEDIUM"

            f.write(f"| `{eid}` | {autos} | {scripts} | {scenes} | {total} | {risk} |\n")

        f.write("\n")

        # Detailed conflicts
        f.write("### Detailed Conflict Information\n\n")
        f.write("<details>\n<summary>Click to expand</summary>\n\n")

        for eid, conflict in sorted_conflicts[:15]:
            f.write(f"**`{eid}`**\n")

            if conflict["controlling_automations"]:
                f.write(f"- Automations: {', '.join(conflict['controlling_automations'])}\n")
            if conflict["controlling_scripts"]:
                f.write(f"- Scripts: {', '.join(conflict['controlling_scripts'])}\n")
            if conflict["controlling_scenes"]:
                f.write(f"- Scenes: {', '.join(conflict['controlling_scenes'])}\n")

            f.write("\n")

        f.write("</details>\n\n")

    def _write_template_entities(self, f):
        """Template entities with attributes."""
        f.write("## 📐 7. Template Entities\n\n")

        if not self.templates.template_entities:
            f.write("*No template entities.*\n\n")
            return

        f.write(f"*{len(self.templates.template_entities)} template entities*\n\n")

        # Validation errors
        if self.templates.validation_errors:
            f.write("### Template Validation Errors\n\n")
            for err in self.templates.validation_errors:
                f.write(f"- **{err['name']}**: {err['error']}\n")
            f.write("\n")

        # Group by source
        by_source = defaultdict(list)
        for te in self.templates.template_entities:
            by_source[te.get("source", "unknown")].append(te)

        for source, entities in by_source.items():
            f.write(f"### {source} ({len(entities)} entities)\n\n")

            f.write("| Name | Type | Device Class | Unit | Referenced Entities | Attributes |\n")
            f.write("|------|------|--------------|------|---------------------|------------|\n")

            for te in entities[:20]:
                name = te.get("name", "Unknown")[:30]
                ttype = te.get("type", "sensor")
                device_class = te.get("device_class") or "-"
                unit = te.get("unit") or "-"
                refs = ", ".join(te.get("referenced_entities", [])[:3])
                if len(te.get("referenced_entities", [])) > 3:
                    refs += f" +{len(te['referenced_entities']) - 3}"
                attrs = ", ".join(te.get("attributes", {}).keys())[:30]

                f.write(
                    f"| {name} | {ttype} | {device_class} | {unit} | {refs or '-'} | {attrs or '-'} |\n"
                )

            if len(entities) > 20:
                f.write(f"\n*... and {len(entities) - 20} more*\n")

            f.write("\n")

        # Full JSON
        f.write("<details>\n<summary>Full Template Entity Data (JSON)</summary>\n\n")
        f.write("```json\n")
        f.write(json.dumps(self.templates.template_entities, indent=2, ensure_ascii=False))
        f.write("\n```\n\n")
        f.write("</details>\n\n")

    def _write_persons_and_tracking(self, f):
        """Persons and presence tracking section."""
        if not self.persons or not self.persons.persons:
            return
        f.write("## Persons & Presence Tracking\n\n")

        for person in self.persons.persons:
            state_icon = ""
            if person["state"] == "home":
                state_icon = "(home)"
            elif person["state"] == "not_home":
                state_icon = "(away)"
            else:
                state_icon = f"({person['state']})"

            f.write(f"### {person['name']} {state_icon}\n\n")
            f.write(f"- **Entity:** `{person['entity_id']}`\n")
            if person.get("latitude") and person.get("longitude"):
                f.write(f"- **Location:** {person['latitude']}, {person['longitude']}\n")
            if person.get("source"):
                f.write(f"- **Source:** {person['source']}\n")

            trackers = self.persons.trackers.get(person["entity_id"], [])
            if trackers:
                f.write(f"- **Trackers ({len(trackers)}):**\n")
                for tracker in trackers:
                    state = tracker.get("state", "unknown")
                    battery = tracker.get("battery")
                    source = tracker.get("source_type", "unknown")
                    extras = []
                    if battery is not None:
                        extras.append(f"battery: {battery}%")
                    if source != "unknown":
                        extras.append(f"source: {source}")
                    extra_str = f" ({', '.join(extras)})" if extras else ""
                    f.write(f" - `{tracker['entity_id']}`: {state}{extra_str}\n")
            f.write("\n")

    def _write_zones_and_geofencing(self, f):
        """Zones and geofencing section."""
        if not self.zones or not self.zones.zones:
            return
        f.write("## Zones & Geofencing\n\n")

        for zone in self.zones.zones:
            f.write(f"### {zone.get('name', zone.get('entity_id', 'Unknown'))}\n\n")
            f.write(f"- **Entity:** `{zone.get('entity_id', '')}`\n")
            if zone.get("latitude") and zone.get("longitude"):
                f.write(f"- **Center:** {zone['latitude']}, {zone['longitude']}\n")
            f.write(f"- **Radius:** {zone.get('radius', 100)}m\n")
            if zone.get("passive"):
                f.write("- **Passive mode:** yes (does not trigger automations)\n")

            persons_here = self.zones.persons_in_zones.get(zone.get("entity_id", ""), [])
            if persons_here:
                f.write(
                    f"- **Persons currently here:** {', '.join(f'`{p}`' for p in persons_here)}\n"
                )
            f.write("\n")

    def _write_energy_dashboard(self, f):
        """Energy dashboard section."""
        if not self.energy:
            return
        f.write("## Energy & Consumption\n\n")

        if self.energy.energy_data.get("unavailable"):
            f.write("*Energy data not available (API endpoint unreachable).*\n\n")
            return

        # Top consumers
        if self.energy.consumption_by_device:
            sorted_devices = sorted(
                self.energy.consumption_by_device.items(), key=lambda x: x[1]["total"], reverse=True
            )
            f.write("### Top Energy Consumers\n\n")
            f.write("| Device | Total (kWh) | Sensors |\n")
            f.write("|--------|-------------|--------|\n")
            for device_name, data in sorted_devices[:15]:
                if data["total"] > 0:
                    f.write(f"| {device_name} | {data['total']:.2f} | {len(data['sensors'])} |\n")
            f.write("\n")

        # Energy sensors
        if self.energy.energy_sensors:
            f.write("### Energy Sensors\n\n")
            f.write("| Entity | Value | Unit |\n")
            f.write("|--------|-------|------|\n")
            for sensor in self.energy.energy_sensors[:20]:
                f.write(
                    f"| `{sensor['entity_id']}` | {sensor['state']} | {sensor.get('unit', '')} |\n"
                )
            if len(self.energy.energy_sensors) > 20:
                f.write(f"| ... | *{len(self.energy.energy_sensors) - 20} more sensors* | |\n")
            f.write("\n")

    def _write_helper_inventory(self, f):
        """Helper entity inventory section."""
        if not self.helpers:
            return
        f.write("## Helper Inventory\n\n")

        # Timers
        if self.helpers.timers:
            f.write("### Timers\n\n")
            f.write("| Entity | State | Duration | Remaining |\n")
            f.write("|--------|-------|----------|----------|\n")
            for item in self.helpers.timers:
                f.write(
                    f"| `{item['entity_id']}` | {item['state']} | {item.get('duration', '-')} | {item.get('remaining', '-')} |\n"
                )
            f.write("\n")

        # Counters
        if self.helpers.counters:
            f.write("### Counters\n\n")
            f.write("| Entity | Value | Range | Step |\n")
            f.write("|--------|-------|-------|------|\n")
            for item in self.helpers.counters:
                range_str = f"{item.get('min', 0)}-{item.get('max', 100)}"
                f.write(
                    f"| `{item['entity_id']}` | {item['state']} | {range_str} | {item.get('step', 1)} |\n"
                )
            f.write("\n")

        # Input booleans
        if self.helpers.input_booleans:
            f.write(f"### Input Booleans ({len(self.helpers.input_booleans)})\n\n")
            f.write("| Entity | State |\n")
            f.write("|--------|-------|\n")
            for item in self.helpers.input_booleans:
                f.write(f"| `{item['entity_id']}` | {item['state']} |\n")
            f.write("\n")

        # Input numbers
        if self.helpers.input_numbers:
            f.write(f"### Input Numbers ({len(self.helpers.input_numbers)})\n\n")
            f.write("| Entity | Value | Range | Unit |\n")
            f.write("|--------|-------|-------|------|\n")
            for item in self.helpers.input_numbers:
                range_str = f"{item.get('min', '-')}-{item.get('max', '-')}"
                f.write(
                    f"| `{item['entity_id']}` | {item['state']} | {range_str} | {item.get('unit', '-')} |\n"
                )
            f.write("\n")

        # Input selects
        if self.helpers.input_selects:
            f.write(f"### Input Selects ({len(self.helpers.input_selects)})\n\n")
            f.write("| Entity | State | Options |\n")
            f.write("|--------|-------|--------|\n")
            for item in self.helpers.input_selects:
                options_str = ", ".join(item.get("options", [])[:5])
                if len(item.get("options", [])) > 5:
                    options_str += "..."
                f.write(f"| `{item['entity_id']}` | {item['state']} | {options_str} |\n")
            f.write("\n")

        # Input texts
        if self.helpers.input_texts:
            f.write(f"### Input Texts ({len(self.helpers.input_texts)})\n\n")
            f.write("| Entity | State |\n")
            f.write("|--------|-------|\n")
            for item in self.helpers.input_texts:
                f.write(f"| `{item['entity_id']}` | {item.get('state', '-')[:50]} |\n")
            f.write("\n")

    def _write_services_catalog(self, f):
        """Services catalog section."""
        if not self.services or not self.services.services:
            return
        f.write("## Services Catalog\n\n")
        f.write(
            f"**{self.services.total_services} services across {len(self.services.services)} domains**\n\n"
        )

        # Show most important domains first
        important = [
            "light",
            "switch",
            "climate",
            "cover",
            "media_player",
            "automation",
            "script",
            "notify",
            "homeassistant",
            "input_boolean",
            "timer",
            "scene",
            "group",
        ]
        shown = set()

        for domain in important:
            if domain in self.services.services:
                shown.add(domain)
                services = self.services.services[domain]
                f.write(f"### `{domain}` ({len(services)} services)\n\n")
                for svc in services[:10]:
                    desc = svc.get("description", "")[:80]
                    f.write(f"- **`{domain}.{svc['name']}`**")
                    if desc:
                        f.write(f" — {desc}")
                    f.write("\n")
                if len(services) > 10:
                    f.write(f"- ... *{len(services) - 10} more services*\n")
                f.write("\n")

        # Remaining domains
        for domain, services in sorted(self.services.services.items()):
            if domain in shown:
                continue
            f.write(f"- **`{domain}`** ({len(services)} services)\n")

        f.write("\n")

    def _write_hacs_and_components(self, f):
        """HACS and custom components section."""
        if not self.hacs:
            return
        has_data = self.hacs.hacs_repos or self.hacs.custom_components
        if not has_data:
            return
        f.write("## HACS & Custom Components\n\n")

        if self.hacs.hacs_repos:
            f.write("### HACS Repositories\n\n")
            f.write("| Name | Category | Installed | Available | Status |\n")
            f.write("|------|----------|-----------|-----------|--------|\n")
            for repo in self.hacs.hacs_repos[:20]:
                f.write(
                    f"| {repo['name']} | {repo['category']} | {repo.get('installed_version', '-')} | {repo.get('available_version', '-')} | {repo.get('status', '-')} |\n"
                )
            if len(self.hacs.hacs_repos) > 20:
                f.write(f"| ... | *{len(self.hacs.hacs_repos) - 20} more repos* | | | |\n")
            f.write("\n")

        if self.hacs.custom_components:
            f.write("### Custom Components\n\n")
            f.write("| Component | Version | Dependencies |\n")
            f.write("|-----------|---------|-------------|\n")
            for comp in self.hacs.custom_components:
                deps = ", ".join(comp.get("dependencies", [])[:3]) or "-"
                f.write(f"| `{comp['domain']}` | {comp.get('version', '-')} | {deps} |\n")
            f.write("\n")

    def _write_dashboard_usage(self, f):
        """Entity usage in dashboards with source file list."""
        f.write("## 8. Dashboard Entity Usage\n\n")

        if not self.dashboard.dashboards_found:
            f.write("*No dashboards or failed to parse them.*\n\n")
            return

        # Dashboard list with source files
        f.write("### Dashboard Sources\n\n")
        f.write("| Dashboard | Source File | URL |\n")
        f.write("|-----------|-------------|-----|\n")

        for db in self.dashboard.dashboards_found:
            f.write(f"| {db['name']} | `.storage/{db['file']}` | `{db['url']}` |\n")

        f.write("\n")

        # Missing entities in dashboards
        if self.dashboard.missing_entities:
            f.write("### Missing Entities in Dashboards\n\n")
            f.write("*These entities are used in dashboards, but do not exist in the system.*\n\n")

            f.write("| Entity ID | Used In |\n")
            f.write("|-----------|--------|\n")

            for eid, usages in sorted(self.dashboard.missing_entities.items()):
                usage_str = ", ".join(usages[:3])
                if len(usages) > 3:
                    usage_str += f" +{len(usages) - 3}"
                f.write(f"| `{eid}` | {usage_str} |\n")

            f.write("\n")

        # Top used entities
        usage_count = {eid: len(locs) for eid, locs in self.dashboard.entity_in_dashboards.items()}

        if not usage_count:
            f.write("*No entities in dashboards.*\n\n")
            return

        top_used = sorted(usage_count.items(), key=lambda x: x[1], reverse=True)[:30]

        f.write("### Top Used Entities in Dashboards\n\n")
        f.write("| Entity | Usage Count | Dashboards | Card Types |\n")
        f.write("|--------|-------------|------------|------------|\n")

        for eid, count in top_used:
            usages = self.dashboard.entity_in_dashboards[eid]
            dashboards = list(set(d["dashboard"] for d in usages))[:2]
            dashboards_str = ", ".join(dashboards)
            if len(set(d["dashboard"] for d in usages)) > 2:
                dashboards_str += "..."

            card_types = list(set(d.get("card_type", "unknown") for d in usages))[:3]
            cards_str = ", ".join(card_types)

            f.write(f"| `{eid}` | {count} | {dashboards_str} | {cards_str} |\n")

        f.write("\n")

        # Card type distribution
        card_types = Counter()
        for usages in self.dashboard.entity_in_dashboards.values():
            for u in usages:
                card_types[u.get("card_type", "unknown")] += 1

        if card_types:
            f.write("### Card typeee Distribution\n\n")
            f.write("| Card Type | Entity References |\n")
            f.write("|-----------|------------------|\n")

            for card_type, count in card_types.most_common(15):
                f.write(f"| {card_type} | {count} |\n")

            f.write("\n")

    def _write_log_analysis(self, f):
        """Log analysis with recommendations per component."""
        f.write("## 📋 9. Log Analysis\n\n")

        f.write(f"*Analysis from the last {constants.LOG_HOURS_BACK} hours*\n\n")

        # Summary
        f.write("### Summary\n\n")
        f.write("| Metric | Value |\n")
        f.write("|--------|-------|\n")
        f.write(f"| Errors | {len(self.logs.errors)} |\n")
        f.write(f"| Warnings | {len(self.logs.warnings)} |\n")
        f.write(f"| Unique Error Patterns | {len(self.logs.error_patterns)} |\n")
        f.write(f"| Affected Entities | {len(self.logs.affected_entities)} |\n")
        f.write(f"| API Errors | {len(self.logs.api_errors)} |\n")
        f.write(f"| Startup Errors | {len(self.logs.startup_errors)} |\n\n")

        # Top error patterns
        if self.logs.error_patterns:
            f.write("### Top Error Patterns\n\n")

            sorted_patterns = sorted(
                self.logs.error_patterns.items(),
                key=lambda x: x[1]["count"],
                reverse=True,
            )[:15]

            f.write(
                "| Category | Count | Integration | Sample Message | First Seen | Last Seen |\n"
            )
            f.write("|----------|-------|-------------|----------------|------------|----------|\n")

            for pattern, data in sorted_patterns:
                category = data["category"]
                count = data["count"]

                # Top integration for this pattern
                top_integration = data["integrations"].most_common(1)
                integration = top_integration[0][0] if top_integration else "unknown"

                sample = data["sample_message"][:50].replace("|", "\\|")
                if len(data["sample_message"]) > 50:
                    sample += "..."

                first = data.get("first_seen", "")[:16] if data.get("first_seen") else "-"
                last = data.get("last_seen", "")[:16] if data.get("last_seen") else "-"

                f.write(f"| {category} | {count} | {integration} | {sample} | {first} | {last} |\n")

            f.write("\n")

        # Errors by integration
        if self.logs.integration_errors:
            f.write("### 🔧 Errors by Integration\n\n")
            f.write("| Integration | Error Count | % of Total |\n")
            f.write("|-------------|-------------|------------|\n")

            total_errors = sum(self.logs.integration_errors.values())
            for integration, count in self.logs.integration_errors.most_common(15):
                pct = (count / total_errors * 100) if total_errors > 0 else 0
                f.write(f"| {integration} | {count} | {pct:.1f}% |\n")

            f.write("\n")

        # Startup errors
        if self.logs.startup_errors:
            f.write("### Startup Errors\n\n")
            f.write("*Errors occurring during Home Assistant startup.*\n\n")

            f.write("<details>\n<summary>Show startup errors</summary>\n\n")

            for err in self.logs.startup_errors[:20]:
                f.write(f"- **{err['integration']}**: {err['message'][:100]}\n")

            if len(self.logs.startup_errors) > 20:
                f.write(f"\n*... and {len(self.logs.startup_errors) - 20} more*\n")

            f.write("\n</details>\n\n")

        # API errors
        if self.logs.api_errors:
            f.write("### 🌐 API/Network Errors\n\n")

            f.write("<details>\n<summary>Show API errors</summary>\n\n")

            for err in self.logs.api_errors[:15]:
                f.write(
                    f"- **{err['integration']}** ({err.get('timestamp', 'unknown')}): {err['message'][:80]}\n"
                )

            f.write("\n</details>\n\n")

        # Recommendations
        recommendations = self.logs.get_recommendations()
        if recommendations:
            f.write("### 💡 Recommendations\n\n")

            for rec in recommendations:
                priority_emoji = "" if rec["priority"] == "high" else ""
                f.write(f"- {priority_emoji} **{rec['issue']}**: {rec['message']}\n")

                # Add specific fix suggestions based on category
                if rec.get("category") == "timeout":
                    f.write(" - *Check: CPU load, network connections, integration timeouts*\n")
                elif rec.get("category") == "connection":
                    f.write(" - *Check: device availability, network configuration, firewall*\n")
                elif rec.get("category") == "template":
                    f.write(" - *Check: Jinja2 syntax, entity availability in templates*\n")
                elif rec.get("integration"):
                    f.write(
                        f" - *Consider: restart integration, check logs for `{rec['integration']}`*\n"
                    )

            f.write("\n")

        # Affected entities
        if self.logs.affected_entities:
            f.write("### 📋 Entities Mentioned in Errors\n\n")
            f.write("<details>\n<summary>Show affected entities</summary>\n\n")

            for eid in sorted(self.logs.affected_entities)[:50]:
                f.write(f"- `{eid}`\n")

            if len(self.logs.affected_entities) > 50:
                f.write(f"\n*... and {len(self.logs.affected_entities) - 50} more*\n")

            f.write("\n</details>\n\n")

    def _write_recent_changes(self, f):
        """Recent entity changes history."""
        f.write("## 📈 10. Recent Entity Changes\n\n")

        if not self.history.recent_changes:
            f.write("*No data on recent changes (history API unavailable or empty).*\n\n")
            return

        f.write(f"*Last {len(self.history.recent_changes)} changes*\n\n")

        # Most active entities
        if self.history.change_frequency:
            f.write("### Most Active Entities\n\n")
            f.write("| Entity | Changes | Domain |\n")
            f.write("|--------|---------|--------|\n")

            for eid, count in self.history.change_frequency.most_common(15):
                domain = eid.split(".")[0]
                f.write(f"| `{eid}` | {count} | {domain} |\n")

            f.write("\n")

        # Recent changes
        f.write("### Recent State Changes\n\n")
        f.write("<details>\n<summary>Show recent changes</summary>\n\n")

        f.write("| Entity | Previous | Current | Changed At |\n")
        f.write("|--------|----------|---------|------------|\n")

        for change in self.history.recent_changes[:30]:
            eid = change["entity_id"]
            prev = change.get("previous_state", "?")[:15]
            curr = change.get("state", "?")[:15]
            changed = change.get("last_changed", "")[:19]

            f.write(f"| `{eid}` | {prev} | {curr} | {changed} |\n")

        f.write("\n</details>\n\n")

    def _write_quick_reference(self, f):
        """Quick reference for AI."""
        f.write("## 11. Quick Reference for AI\n\n")

        f.write("### How to Use This Context\n\n")
        f.write("```\n")
        f.write("1. HEALTH CHECK: Start with Executive Summary (Section 0) for overall status\n")
        f.write(
            "2. DEBUGGING: Check System Health (1) → Log Analysis (9) → Integration Status (2)\n"
        )
        f.write("3. BEFORE EDITING: Check Entity Dependencies (5) + Conflicts (6)\n")
        f.write("4. NEW AUTOMATION: Review Automation Logic (4) + Topology (3)\n")
        f.write("5. DASHBOARD ISSUES: Check Dashboard Usage (8) for missing entities\n")
        f.write("```\n\n")

        f.write("### Quick Troubleshooting Guide\n\n")
        f.write("| Problem | Check Section | Key Info |\n")
        f.write("|---------|---------------|----------|\n")
        f.write("| Entity unavailable | Integration Status (2) | Config entry health |\n")
        f.write("| Automation not working | Automation Logic (4) | is_disabled, last_triggered |\n")
        f.write("| Ghost entity errors | System Health (1) | Ghost entities list |\n")
        f.write("| Race conditions | Conflict Analysis (6) | Controlling automations |\n")
        f.write("| Template errors | Template Entities (7) | Validation errors |\n")
        f.write("| Dashboard broken | Dashboard Usage (8) | Missing entities |\n")
        f.write("| Startup issues | Log Analysis (9) | Startup errors |\n\n")

        # Common entities
        f.write("### Most Used Entities in Automations\n\n")

        all_used = Counter()
        for auto in self.automation.automation_analysis:
            all_used.update(auto.get("trigger_entities", []))
            all_used.update(auto.get("controlled_entities", []))

        f.write("| Entity | Usage Count | Primary Role |\n")
        f.write("|--------|-------------|-------------|\n")

        for eid, count in all_used.most_common(15):
            # Determine role
            triggered_count = sum(
                1
                for a in self.automation.automation_analysis
                if eid in a.get("trigger_entities", [])
            )
            controlled_count = sum(
                1
                for a in self.automation.automation_analysis
                if eid in a.get("controlled_entities", [])
            )

            if triggered_count > controlled_count:
                role = "Trigger"
            elif controlled_count > triggered_count:
                role = "Target"
            else:
                role = "Both"

            f.write(f"| `{eid}` | {count} | {role} |\n")

        f.write("\n")

        # Persons
        if self.persons:
            home_count = len([p for p in self.persons.persons if p["state"] == "home"])
            away_count = len([p for p in self.persons.persons if p["state"] == "not_home"])
            f.write("**Persons:** ")
            f.write(f"{len(self.persons.persons)} total ")
            f.write(f"({home_count} home, {away_count} away)")
            f.write(" \n")

        # Zones
        if self.zones:
            f.write(f"**Zones:** {len(self.zones.zones)} defined \n")

        # Services
        if self.services:
            f.write(
                f"**Services:** {self.services.total_services} across {len(self.services.services)} domains \n"
            )

        # HACS
        if self.hacs:
            f.write(f"**HACS Repos:** {len(self.hacs.hacs_repos)} installed \n")
            f.write(f"**Custom Components:** {len(self.hacs.custom_components)} \n")

        # Areas summary
        f.write("### Available Areas\n\n")

        area_entity_count = defaultdict(int)
        for state in self.registry.states:
            eid = state.get("entity_id", "")
            info = self.registry.get_entity_info(eid)
            area_entity_count[info["area_name"]] += 1

        f.write("| Area | Entity Count | Area ID |\n")
        f.write("|------|--------------|--------|\n")

        for area_id, area_name in sorted(self.registry.areas_map.items()):
            count = area_entity_count.get(area_name, 0)
            f.write(f"| {area_name} | {count} | `{area_id}` |\n")

        # Add unassigned
        unassigned_count = area_entity_count.get("Unassigned", 0)
        if unassigned_count > 0:
            f.write(f"| Unassigned | {unassigned_count} | - |\n")

        f.write("\n")

        # Integration domains
        f.write("### Active Integration Domains\n\n")

        domains = Counter(e.get("domain") for e in self.registry.config_entries)

        f.write("| Domain | Config Entries |\n")
        f.write("|--------|---------------|\n")

        for domain, count in domains.most_common(20):
            f.write(f"| {domain} | {count} |\n")

        f.write("\n")

        # Services commonly used
        all_services = Counter()
        for auto in self.automation.automation_analysis:
            all_services.update(auto.get("services", []))
        for script in self.automation.script_analysis:
            all_services.update(script.get("services", []))

        if all_services:
            f.write("### Most Used Services\n\n")
            f.write("| Service | Usage Count |\n")
            f.write("|---------|-------------|\n")

            for service, count in all_services.most_common(15):
                f.write(f"| `{service}` | {count} |\n")

            f.write("\n")

        # Footer
        f.write("---\n\n")
        f.write(
            f"*Generated by HA Context Generator v1.0 at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n"
        )
        f.write("*Based on MCP Server test patterns for improved accuracy*\n")


# --- MAIN ---
