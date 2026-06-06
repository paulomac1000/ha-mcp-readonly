"""HA Semantic Graph scanner — builds GraphIndex from HA config files.

Scans Home Assistant configuration (registries, automations, scripts,
scenes, dashboards) and populates a typed dependency graph of entities,
devices, areas, integrations, automations, and their relationships.

Usage::

    from ha_graph.scanner import build_graph_index
    index = build_graph_index("/config", ha_url="http://ha:8123", ha_token="...")

The scanner tolerates missing and corrupt files — a partial graph is always
returned with warnings collected in ``index.stats["warnings"]``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ha_graph.extractors import (
    extract_controlled_entities,
    extract_entities_from_data,
    extract_entities_from_template,
    extract_services,
    extract_trigger_info,
)
from ha_graph.models import GraphEdge, GraphIndex, GraphNode

SEARCH_DIRS = ["", "automations", "scripts", "scenes"]
IGNORABLE_DOMAINS = {"sun", "update", "persistent_notification", "zone", "scene", "script"}


class HomeAssistantGraphScanner:
    """Scans HA config files and builds a semantic dependency graph.

    Args:
        config_path: Path to the Home Assistant configuration directory
            (contains ``configuration.yaml``, ``.storage/``, etc.).
        ha_url: Optional Home Assistant base URL (reserved for future
            online augmentation, e.g. live state lookups).
        ha_token: Optional long-lived access token (reserved for future
            online augmentation).
    """

    def __init__(
        self,
        config_path: str,
        ha_url: str | None = None,
        ha_token: str | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.ha_url = ha_url
        self.ha_token = ha_token
        self.warnings: list[str] = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def scan(self, index: GraphIndex) -> None:
        """Run all scanners and populate *index* in-place.

        Args:
            index: An empty (or pre-seeded) :class:`GraphIndex` to fill.
        """
        self._scan_configuration(index)
        self._scan_registries(index)
        self._scan_automations(index)
        self._scan_scripts(index)
        self._scan_scenes(index)
        self._scan_dashboards(index)
        index.built_at = time.time()
        index.stats["warnings"] = self.warnings
        index.stats["node_types"] = self._count_types(index)
        index.stats["edge_relations"] = self._count_relations(index)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_yaml(self, *path_parts: str) -> list[dict] | dict | None:
        """Load a YAML file relative to *config_path*.

        Returns ``None`` if the file does not exist, an empty ``list``
        on parse errors (warning recorded), or the parsed data.
        """
        file_path = self.config_path.joinpath(*path_parts)
        if not file_path.exists():
            return None
        try:
            from tools.yaml_utils import load_yaml_file

            data = load_yaml_file(str(file_path))
            if not isinstance(data, list):
                data = [data] if data else []
            return data
        except Exception as e:
            self.warnings.append(f"Failed to parse {file_path}: {e}")
            return []

    def _load_storage(self, name: str) -> dict:
        """Load a ``.storage/<name>`` JSON registry file.

        Returns an empty dict on failure (warning recorded).
        """
        from tools.utils import load_registry

        try:
            return load_registry(name, str(self.config_path)) or {}
        except Exception as e:
            self.warnings.append(f"Failed to load .storage/{name}: {e}")
            return {}

    def _add_file_node(self, index: GraphIndex, file_path: str) -> str:
        """Ensure a ``file:`` node exists and return its id."""
        file_id = f"file:{file_path}"
        index.add_node(
            GraphNode(id=file_id, type="file", name=file_path)
        )
        return file_id

    def _add_entity_node(
        self, index: GraphIndex, entity_id: str, **extra: Any
    ) -> str:
        """Ensure an ``entity:`` node exists, merging *extra* metadata.

        Since :class:`GraphNode` is frozen, existing nodes are replaced
        with a new instance when additional metadata is supplied.
        """
        node_id = f"entity:{entity_id}"
        existing = index.nodes.get(node_id)

        if existing is None:
            domain = entity_id.split(".")[0] if "." in entity_id else "unknown"
            index.add_node(
                GraphNode(
                    id=node_id,
                    type="entity",
                    name=entity_id,
                    metadata={"domain": domain, **extra},
                )
            )
        elif extra:
            # Merge metadata and replace (dataclass is frozen)
            merged = dict(existing.metadata)
            merged.update(extra)
            index.nodes[node_id] = GraphNode(
                id=node_id,
                type="entity",
                name=existing.name,
                metadata=merged,
            )

        return node_id

    def _count_types(self, index: GraphIndex) -> dict[str, int]:
        """Return counts of nodes per type."""
        counts: dict[str, int] = {}
        for node in index.nodes.values():
            counts[node.type] = counts.get(node.type, 0) + 1
        return counts

    def _count_relations(self, index: GraphIndex) -> dict[str, int]:
        """Return counts of edges per relation."""
        counts: dict[str, int] = {}
        for edge in index.edges:
            counts[edge.relation] = counts.get(edge.relation, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # Scanner: configuration.yaml
    # ------------------------------------------------------------------

    def _scan_configuration(self, index: GraphIndex) -> None:
        """Discover ``!include`` directives in ``configuration.yaml``."""
        cfg = self._load_yaml("configuration.yaml")
        file_id = self._add_file_node(index, "configuration.yaml")
        if not cfg:
            return
        for item in cfg if isinstance(cfg, list) else [cfg]:
            if not isinstance(item, dict):
                continue
            for value in item.values():
                if isinstance(value, str) and value.startswith("!include"):
                    target = value.replace("!include ", "")
                    target_id = f"file:{target}"
                    index.add_node(
                        GraphNode(id=target_id, type="file", name=target)
                    )
                    index.add_edge(
                        GraphEdge(
                            source=file_id,
                            target=target_id,
                            relation="includes",
                            file_path="configuration.yaml",
                        )
                    )

    # ------------------------------------------------------------------
    # Scanner: registries (.storage/*)
    # ------------------------------------------------------------------

    def _scan_registries(self, index: GraphIndex) -> None:
        """Load entity, device, area, and config-entry registries."""
        # -- entity registry --------------------------------------------------
        entity_reg = self._load_storage("core.entity_registry")
        entities = (
            entity_reg.get("data", {}).get("entities", [])
            if entity_reg
            else []
        )
        for ent in entities:
            eid = ent.get("entity_id", "")
            if not eid:
                continue
            self._add_entity_node(
                index,
                eid,
                platform=ent.get("platform"),
                device_id=ent.get("device_id") or None,
                area_id=ent.get("area_id") or None,
                config_entry_id=ent.get("config_entry_id") or None,
                original_name=ent.get("original_name"),
            )

        # -- device registry --------------------------------------------------
        device_reg = self._load_storage("core.device_registry")
        devices = (
            device_reg.get("data", {}).get("devices", [])
            if device_reg
            else []
        )
        for dev in devices:
            did = dev.get("id", "")
            if not did:
                continue
            device_id = f"device:{did}"
            index.add_node(
                GraphNode(
                    id=device_id,
                    type="device",
                    name=dev.get("name", did),
                    metadata={
                        "model": dev.get("model"),
                        "manufacturer": dev.get("manufacturer"),
                        "area_id": dev.get("area_id"),
                    },
                )
            )
            area_id = dev.get("area_id")
            if area_id:
                area_node_id = f"area:{area_id}"
                index.add_node(
                    GraphNode(id=area_node_id, type="area", name=area_id)
                )
                index.add_edge(
                    GraphEdge(
                        source=device_id,
                        target=area_node_id,
                        relation="belongs_to_area",
                    )
                )

        # -- area registry ----------------------------------------------------
        area_reg = self._load_storage("core.area_registry")
        areas = (
            area_reg.get("data", {}).get("areas", []) if area_reg else []
        )
        for area in areas:
            aid = area.get("area_id", "")
            if aid:
                index.add_node(
                    GraphNode(
                        id=f"area:{aid}",
                        type="area",
                        name=area.get("name", aid),
                    )
                )

        # -- config entries ---------------------------------------------------
        cfg_entries = self._load_storage("core.config_entries")
        entries = (
            cfg_entries.get("data", {}).get("entries", [])
            if cfg_entries
            else []
        )
        for entry in entries:
            domain = entry.get("domain", "")
            if domain:
                integration_id = f"integration:{domain}"
                index.add_node(
                    GraphNode(
                        id=integration_id,
                        type="integration",
                        name=domain,
                        metadata={
                            "version": entry.get("version"),
                            "state": entry.get("state"),
                        },
                    )
                )

        # -- link entities → devices / integrations --------------------------
        for node in index.nodes.values():
            if node.type != "entity":
                continue
            device_id = node.metadata.get("device_id")
            if device_id:
                index.add_edge(
                    GraphEdge(
                        source=node.id,
                        target=f"device:{device_id}",
                        relation="belongs_to_device",
                    )
                )
            platform = node.metadata.get("platform")
            if platform:
                index.add_edge(
                    GraphEdge(
                        source=node.id,
                        target=f"integration:{platform}",
                        relation="from_integration",
                    )
                )

    # ------------------------------------------------------------------
    # Scanner: automations.yaml
    # ------------------------------------------------------------------

    def _scan_automations(self, index: GraphIndex) -> None:
        """Parse ``automations.yaml`` into graph nodes and edges."""
        data = self._load_yaml("automations.yaml")
        if not data:
            return
        file_id = self._add_file_node(index, "automations.yaml")
        items = data if isinstance(data, list) else [data]

        for i, auto in enumerate(items):
            if not isinstance(auto, dict):
                continue
            auto_id = auto.get("id") or f"unnamed_{i}"
            alias = auto.get("alias", auto_id)
            node_id = f"automation:{auto_id}"
            index.add_node(
                GraphNode(
                    id=node_id,
                    type="automation",
                    name=alias,
                    metadata={"mode": auto.get("mode", "single")},
                )
            )
            index.add_edge(
                GraphEdge(
                    source=node_id,
                    target=file_id,
                    relation="defined_in",
                    file_path="automations.yaml",
                    object_path=f"[{i}]",
                )
            )

            # -- triggers -------------------------------------------------
            triggers = auto.get("trigger", [])
            triggers = triggers if isinstance(triggers, list) else [triggers]
            for entity_id, platform in extract_trigger_info(triggers):
                target_id = self._add_entity_node(index, entity_id)
                index.add_edge(
                    GraphEdge(
                        source=node_id,
                        target=target_id,
                        relation="triggers_on",
                        confidence="exact",
                        file_path="automations.yaml",
                        object_path=f"[{i}].trigger",
                        evidence=f"platform:{platform}",
                    )
                )

            # -- conditions → reads ---------------------------------------
            conditions = auto.get("condition", [])
            conditions = (
                conditions if isinstance(conditions, list) else [conditions]
            )
            cond_entities = extract_entities_from_data(conditions)
            for eid in cond_entities:
                target_id = self._add_entity_node(index, eid)
                index.add_edge(
                    GraphEdge(
                        source=node_id,
                        target=target_id,
                        relation="reads",
                        confidence="inferred",
                        file_path="automations.yaml",
                        object_path=f"[{i}].condition",
                    )
                )

            # -- actions ---------------------------------------------------
            actions = auto.get("action", [])
            actions = actions if isinstance(actions, list) else [actions]

            # Controlled entities (target.entity_id in service calls)
            controlled = extract_controlled_entities(actions)
            for eid in controlled:
                target_id = self._add_entity_node(index, eid)
                index.add_edge(
                    GraphEdge(
                        source=node_id,
                        target=target_id,
                        relation="controls",
                        confidence="inferred",
                        file_path="automations.yaml",
                        object_path=f"[{i}].action",
                    )
                )

            # Services called
            services = extract_services(actions)
            for svc in services:
                svc_id = f"service:{svc}"
                index.add_node(
                    GraphNode(id=svc_id, type="service", name=svc)
                )
                index.add_edge(
                    GraphEdge(
                        source=node_id,
                        target=svc_id,
                        relation="calls_service",
                        confidence="exact",
                        file_path="automations.yaml",
                        object_path=f"[{i}].action",
                    )
                )

            # Template entities from action data
            for item in actions:
                if not isinstance(item, dict):
                    continue
                for text_val in self._find_template_fields(item):
                    for eid, _conf in extract_entities_from_template(text_val):
                        target_id = self._add_entity_node(index, eid)
                        index.add_edge(
                            GraphEdge(
                                source=node_id,
                                target=target_id,
                                relation="reads",
                                confidence="inferred",
                            )
                        )

    def _find_template_fields(self, item: dict) -> list[str]:
        """Recursively collect Jinja2 template strings from a dict."""
        results: list[str] = []
        for value in item.values():
            if isinstance(value, str) and "{{" in value:
                results.append(value)
            elif isinstance(value, dict):
                results.extend(self._find_template_fields(value))
            elif isinstance(value, list):
                for v in value:
                    if isinstance(v, dict):
                        results.extend(self._find_template_fields(v))
                    elif isinstance(v, str) and "{{" in v:
                        results.append(v)
        return results

    # ------------------------------------------------------------------
    # Scanner: scripts.yaml
    # ------------------------------------------------------------------

    def _scan_scripts(self, index: GraphIndex) -> None:
        """Parse ``scripts.yaml`` into graph nodes and edges."""
        data = self._load_yaml("scripts.yaml")
        if not data:
            return
        file_id = self._add_file_node(index, "scripts.yaml")

        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                continue
            for script_id, script_data in item.items():
                if not isinstance(script_data, dict):
                    continue
                node_id = f"script:{script_id}"
                alias = script_data.get("alias", script_id)
                index.add_node(
                    GraphNode(id=node_id, type="script", name=alias)
                )
                index.add_edge(
                    GraphEdge(
                        source=node_id,
                        target=file_id,
                        relation="defined_in",
                    )
                )

                seq = script_data.get("sequence", [])
                seq = seq if isinstance(seq, list) else [seq]

                # Controlled entities
                for eid in extract_controlled_entities(seq):
                    target_id = self._add_entity_node(index, eid)
                    index.add_edge(
                        GraphEdge(
                            source=node_id,
                            target=target_id,
                            relation="controls",
                        )
                    )

                # Services
                for svc in extract_services(seq):
                    svc_id = f"service:{svc}"
                    index.add_node(
                        GraphNode(id=svc_id, type="service", name=svc)
                    )
                    index.add_edge(
                        GraphEdge(
                            source=node_id,
                            target=svc_id,
                            relation="calls_service",
                        )
                    )

                # Script → script calls
                for action in seq:
                    if not isinstance(action, dict):
                        continue
                    svc = action.get("service") or action.get("action", "")
                    if isinstance(svc, str) and svc.startswith("script."):
                        called_script = svc.replace("script.", "")
                        called_id = f"script:{called_script}"
                        index.add_node(
                            GraphNode(
                                id=called_id,
                                type="script",
                                name=called_script,
                            )
                        )
                        index.add_edge(
                            GraphEdge(
                                source=node_id,
                                target=called_id,
                                relation="calls_script",
                            )
                        )

    # ------------------------------------------------------------------
    # Scanner: scenes.yaml
    # ------------------------------------------------------------------

    def _scan_scenes(self, index: GraphIndex) -> None:
        """Parse ``scenes.yaml`` into graph nodes and edges."""
        data = self._load_yaml("scenes.yaml")
        if not data:
            return
        file_id = self._add_file_node(index, "scenes.yaml")

        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                continue
            scene_id = item.get("id") or item.get("name", "unnamed")
            node_id = f"scene:{scene_id}"
            index.add_node(
                GraphNode(
                    id=node_id,
                    type="scene",
                    name=item.get("name", scene_id),
                )
            )
            index.add_edge(
                GraphEdge(
                    source=node_id,
                    target=file_id,
                    relation="defined_in",
                )
            )
            entities = item.get("entities", {})
            if isinstance(entities, dict):
                for eid in entities:
                    target_id = self._add_entity_node(index, eid)
                    index.add_edge(
                        GraphEdge(
                            source=node_id,
                            target=target_id,
                            relation="controls",
                        )
                    )

    # ------------------------------------------------------------------
    # Scanner: Lovelace dashboards (.storage/lovelace*)
    # ------------------------------------------------------------------

    def _scan_dashboards(self, index: GraphIndex) -> None:
        """Parse ``.storage/lovelace*`` JSON files as dashboards."""
        storage_path = self.config_path / ".storage"
        if not storage_path.exists():
            return

        dash_files = sorted(storage_path.glob("lovelace*"))
        for dash_file in dash_files:
            try:
                data = json.loads(dash_file.read_text(encoding="utf-8"))
            except Exception as e:
                self.warnings.append(
                    f"Failed to read dashboard {dash_file}: {e}"
                )
                continue

            dash_name = (
                dash_file.stem.replace("lovelace", "").lstrip("._") or "main"
            )
            node_id = f"dashboard:{dash_name}"
            index.add_node(
                GraphNode(id=node_id, type="dashboard", name=dash_name)
            )
            index.add_edge(
                GraphEdge(
                    source=node_id,
                    target=f"file:.storage/{dash_file.name}",
                    relation="defined_in",
                )
            )

            config = data.get("data", {}).get("config", {})
            views = config.get("views", [])
            for view in views:
                cards = view.get("cards", [])
                for eid in extract_entities_from_data(cards):
                    target_id = self._add_entity_node(index, eid)
                    index.add_edge(
                        GraphEdge(
                            source=node_id,
                            target=target_id,
                            relation="displays",
                            confidence="inferred",
                        )
                    )


# =========================================================================
# Module-level convenience
# =========================================================================


def build_graph_index(
    config_path: str,
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> GraphIndex:
    """Build a HA Semantic Graph from configuration files and registries.

    Scans all configuration files (YAML and .storage JSON) and returns
    a :class:`GraphIndex` containing typed nodes and edges representing
    the entity, automation, script, scene, dashboard, device, area, and
    integration topology.

    The scanner tolerates missing and corrupt files — a partial graph
    is always returned. Check ``index.stats["warnings"]`` for any issues
    encountered during scanning.

    Args:
        config_path: Path to the HA config directory (e.g. ``"/config"``).
        ha_url: Reserved for future online augmentation.
        ha_token: Reserved for future online authentication.

    Returns:
        A populated :class:`GraphIndex`. The ``built_at`` timestamp is
        set to the epoch seconds when the scan completed.
    """
    index = GraphIndex()
    scanner = HomeAssistantGraphScanner(config_path, ha_url, ha_token)
    scanner.scan(index)
    return index
