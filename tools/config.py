"""
Configuration Management Tools
Provides tools for reading, searching, validating, and analyzing Home Assistant configuration files.
Focuses on token efficiency for AI interactions.
"""

import json
import os
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Optional, Set

import yaml

from tools.utils import load_registry, make_ha_request
from tools.yaml_utils import HomeAssistantLoader, load_yaml_file


def register_config_tools(
    mcp, config_path: str, ha_url: Optional[str] = None, ha_token: Optional[str] = None
):
    """
    Registers tools for managing Home Assistant configuration.
    """

    # ========================================
    # 🛠️ INTERNAL HELPERS
    # ========================================

    def _load_yaml_file_internal(file_path: str) -> Optional[Any]:
        """Loads a YAML file using the shared HomeAssistantLoader."""
        full_path = Path(config_path) / file_path
        if not full_path.exists():
            return None
        return load_yaml_file(str(full_path))

    def _sanitize_config(obj: Any) -> Any:
        """Removes sensitive data from a config object."""
        sensitive_keys = [
            "password",
            "token",
            "api_key",
            "secret",
            "client_id",
            "client_secret",
            "ssid",
            "key",
        ]
        if isinstance(obj, dict):
            return {
                k: "***REDACTED***"
                if any(sens in k.lower() for sens in sensitive_keys)
                else _sanitize_config(v)
                for k, v in obj.items()
            }
        elif isinstance(obj, list):
            return [_sanitize_config(item) for item in obj]
        else:
            return obj

    def _extract_entities_and_services(
        data: Any,
        found_entities: Set[str],
        found_services: Set[str],
        found_templates: Set[str],
    ):
        """Recursively extracts entity_ids, service calls, and templates."""
        entity_pattern = re.compile(
            r"\b(?:sensor|binary_sensor|light|switch|climate|cover|input_\w+|automation|script|person|device_tracker|"
            r"media_player|camera|lock|fan|vacuum|weather|sun|zone|timer|counter|number|select|button|scene|group|alarm_control_panel)\.[a-zA-Z0-9_]+\b"
        )
        service_pattern = re.compile(r"\b[a-z_]+\.[a-z_]+\b")  # domain.service
        template_pattern = re.compile(r"\{\{.*?\}\}|\{%.*?%\}")

        if isinstance(data, dict):
            for key, value in data.items():
                if key == "entity_id":
                    if isinstance(value, str):
                        found_entities.update(entity_pattern.findall(value))
                    elif isinstance(value, list):
                        for v in value:
                            if isinstance(v, str):
                                found_entities.update(entity_pattern.findall(v))
                elif key == "service":
                    if isinstance(value, str):
                        found_services.add(value)

                if isinstance(value, str):
                    found_entities.update(entity_pattern.findall(value))
                    found_services.update(service_pattern.findall(value))
                    found_templates.update(template_pattern.findall(value))

                _extract_entities_and_services(
                    value, found_entities, found_services, found_templates
                )

        elif isinstance(data, list):
            for item in data:
                _extract_entities_and_services(
                    item, found_entities, found_services, found_templates
                )

    # ========================================
    # ⚙️ GENERAL CONFIGURATION TOOLS
    # ========================================

    @mcp.tool()
    def get_main_configuration() -> str:
        """
        Fetches main configuration from file `configuration.yaml`.
        Returns structured YAML, with sensitive data (passwords, tokens) redacted.
        """
        try:
            config_file_path = "configuration.yaml"
            data = _load_yaml_file_internal(config_file_path)

            if data is None:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"{config_file_path} not found or invalid YAML",
                    },
                    indent=2,
                )

            sanitized_data = _sanitize_config(data)
            return yaml.dump(
                sanitized_data,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    def list_custom_components() -> str:
        """
        Fetches list of installed custom components (custom_components/).
        """
        try:
            custom_dir = Path(config_path) / "custom_components"
            if not custom_dir.exists():
                return json.dumps(
                    {
                        "success": False,
                        "error": "custom_components directory not found",
                    },
                    indent=2,
                )

            components = []
            for component_name in os.listdir(custom_dir):
                component_path = custom_dir / component_name
                if component_path.is_dir():
                    manifest_path = component_path / "manifest.json"
                    info = {
                        "name": component_name,
                        "path": str(component_path.relative_to(config_path)),
                    }
                    if manifest_path.exists():
                        try:
                            with open(manifest_path, "r", encoding="utf-8") as f:
                                manifest = json.load(f)
                            info.update(
                                {
                                    "version": manifest.get("version", "unknown"),
                                    "domain": manifest.get("domain", component_name),
                                    "name_full": manifest.get("name", component_name),
                                }
                            )
                        except Exception as e:
                            info["manifest_error"] = str(e)
                    components.append(info)

            return json.dumps(
                {
                    "success": True,
                    "total_custom_components": len(components),
                    "components": components,
                },
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    def list_themes() -> str:
        """
        Fetches list of installed themes (themes/).
        """
        try:
            themes_dir = Path(config_path) / "themes"
            if not themes_dir.exists():
                return json.dumps(
                    {"success": False, "error": "themes directory not found"}, indent=2
                )

            themes = []
            for theme_file in os.listdir(themes_dir):
                if theme_file.endswith((".yaml", ".yml")):
                    theme_path = themes_dir / theme_file
                    info = {"file": theme_file}
                    try:
                        data = _load_yaml_file_internal(str(theme_path.relative_to(config_path)))
                        if data:
                            info["theme_names"] = list(data.keys())
                    except Exception as e:
                        info["parse_error"] = str(e)
                    themes.append(info)

            return json.dumps(
                {"success": True, "total_theme_files": len(themes), "themes": themes},
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    def get_config_structure() -> str:
        """
        Returns structure of directories and files in the Home Assistant configuration directory.
        """
        try:
            structure = {}
            for root, dirs, files in os.walk(config_path):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
                relative_root = Path(root).relative_to(config_path)
                if str(relative_root) == ".":
                    relative_root = Path("root")

                yaml_files = [f for f in files if f.endswith((".yaml", ".yml"))]
                json_files = [f for f in files if f.endswith(".json")]
                other_files = [
                    f
                    for f in files
                    if not f.endswith((".yaml", ".yml", ".json")) and not f.startswith(".")
                ]

                if yaml_files or json_files or other_files or dirs:
                    structure[str(relative_root)] = {
                        "subdirectories": dirs,
                        "yaml_files": yaml_files,
                        "json_files": json_files,
                        "other_files_sample": other_files[:10],
                    }
            return json.dumps(
                {"success": True, "structure": structure}, indent=2, ensure_ascii=False
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    def read_config_file(file_path: str) -> str:
        """
        Odczytuje zavalue konkretnego fileu konfiguracyjnego.
        """
        try:
            full_path = Path(config_path) / file_path

            # Security check - ensure path is within config directory
            real_path = os.path.realpath(full_path)
            real_config = os.path.realpath(config_path)

            if not real_path.startswith(real_config):
                return json.dumps(
                    {
                        "success": False,
                        "error": "Access denied - path outside configuration directory",
                    },
                    indent=2,
                )

            if not os.path.isfile(full_path):
                return json.dumps(
                    {"success": False, "error": f"File '{file_path}' not found"},
                    indent=2,
                )

            file_size = os.path.getsize(full_path)
            max_size = 200 * 1024  # 200KB limit for reading raw content (AI token limit)

            with open(full_path, "r", encoding="utf-8") as f:
                if file_size > max_size:
                    content = f.read(max_size)
                    return f"File too large ({file_size} bytes). Showing first {max_size // 1024}KB:\n\n{content}"
                else:
                    content = f.read()

            return content
        except FileNotFoundError:
            return json.dumps(
                {"success": False, "error": f"File '{file_path}' not found"}, indent=2
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    # ========================================
    # 🚀 OPTIMIZED CONFIGURATION SEARCH
    # ========================================

    @mcp.tool()
    def search_in_config_batch(
        search_terms: str, file_types: str = "yaml", match_mode: str = "any"
    ) -> str:
        """
        🚀 OPTIMIZED - searches wiele fraz tekstowych w wielu fileach konfiguracyjnych.
        """
        try:
            terms = [term.strip() for term in search_terms.split(",") if term.strip()]
            if not terms:
                return json.dumps({"success": False, "error": "No search terms provided"}, indent=2)

            extensions = []
            if file_types in ["yaml", "all"]:
                extensions.extend([".yaml", ".yml"])
            if file_types in ["json", "all"]:
                extensions.append(".json")

            results_by_term = {term: [] for term in terms}
            matching_files = []

            for root, dirs, files in os.walk(config_path):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]

                for file in files:
                    if any(file.endswith(ext) for ext in extensions):
                        file_path = os.path.join(root, file)
                        relative_path = os.path.relpath(file_path, config_path)

                        try:
                            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                                content = f.read()

                            content_lower = content.lower()
                            lines = content.split("\n")

                            current_file_matched_terms = []
                            current_file_matches_detail = {}

                            for term in terms:
                                term_lower = term.lower()
                                if term_lower in content_lower:
                                    current_file_matched_terms.append(term)

                                    # Find matching lines for this term
                                    matching_lines = []
                                    for i, line in enumerate(lines, 1):
                                        if term_lower in line.lower():
                                            matching_lines.append(
                                                {
                                                    "line": i,
                                                    "content": line.strip()[:200],
                                                }
                                            )
                                            if len(matching_lines) >= 5:
                                                break

                                    current_file_matches_detail[term] = {
                                        "count": len(matching_lines),
                                        "lines": matching_lines,
                                    }
                                    results_by_term[term].append(
                                        {
                                            "file": relative_path,
                                            "matches": len(matching_lines),
                                        }
                                    )

                            if (
                                match_mode == "all"
                                and len(current_file_matched_terms) == len(terms)
                            ) or (match_mode == "any" and len(current_file_matched_terms) > 0):
                                matching_files.append(
                                    {
                                        "file": relative_path,
                                        "matched_terms": current_file_matched_terms,
                                        "matches_detail": current_file_matches_detail,
                                    }
                                )
                        except Exception:
                            continue

            # Limit results
            for term in results_by_term:
                results_by_term[term] = results_by_term[term][:20]
            matching_files = matching_files[:30]

            return json.dumps(
                {
                    "success": True,
                    "search_terms": terms,
                    "match_mode": match_mode,
                    "summary": {
                        "files_matching_criteria": len(matching_files),
                        "results_per_term": {term: len(results_by_term[term]) for term in terms},
                    },
                    "matching_files": matching_files,
                    "results_by_term": results_by_term,
                },
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    def search_in_config(search_term: str, file_types: str = "yaml") -> str:
        """
        Searches for a specific phrase in all configuration files.
        """
        return search_in_config_batch(
            search_terms=search_term, file_types=file_types, match_mode="any"
        )

    @mcp.tool()
    def search_config_by_params(
        entity_id: Optional[str] = None,
        service: Optional[str] = None,
        platform: Optional[str] = None,
        device_class: Optional[str] = None,
        file_pattern: Optional[str] = None,
    ) -> str:
        """
        🚀 OPTIMIZED - searches w konfiguracji po parameterach YAML.
        """
        try:
            if not any([entity_id, service, platform, device_class]):
                return json.dumps(
                    {
                        "success": False,
                        "error": "At least one search parameter required",
                    },
                    indent=2,
                )

            results = []
            search_files = []

            for root, dirs, files in os.walk(config_path):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]

                for file in files:
                    if file.endswith((".yaml", ".yml")):
                        file_path = os.path.join(root, file)
                        relative_path = os.path.relpath(file_path, config_path)

                        if file_pattern and not fnmatch(relative_path, file_pattern):
                            continue

                        search_files.append((file_path, relative_path))

            for file_path, relative_path in search_files:
                try:
                    data = _load_yaml_file_internal(relative_path)

                    if not data:
                        continue

                    matches = []

                    def search_dict(obj, path="", parent_context=None):
                        if isinstance(obj, dict):
                            match_info = {}

                            if entity_id and obj.get("entity_id") == entity_id:
                                match_info["matched_entity_id"] = entity_id

                            if service and obj.get("service") == service:
                                match_info["matched_service"] = service

                            if platform and obj.get("platform") == platform:
                                match_info["matched_platform"] = platform

                            if device_class and obj.get("device_class") == device_class:
                                match_info["matched_device_class"] = device_class

                            for key, value in obj.items():
                                if isinstance(value, str):
                                    if entity_id and entity_id in value:
                                        match_info["matched_in_template"] = entity_id
                                    if service and service in value:
                                        match_info["matched_service_in_template"] = service

                            if match_info:
                                matches.append(
                                    {
                                        "path": path,
                                        "match_info": match_info,
                                        "context": obj,
                                        "parent_context": parent_context,
                                    }
                                )

                            for key, value in obj.items():
                                search_dict(value, f"{path}.{key}" if path else key, obj)

                        elif isinstance(obj, list):
                            for idx, item in enumerate(obj):
                                search_dict(item, f"{path}[{idx}]", obj)

                    search_dict(data)

                    if matches:
                        results.append(
                            {
                                "file": relative_path,
                                "match_count": len(matches),
                                "matches": matches[:10],
                            }
                        )

                except Exception:
                    continue

            results = results[:30]

            return json.dumps(
                {
                    "success": True,
                    "search_params": {
                        "entity_id": entity_id,
                        "service": service,
                        "platform": platform,
                        "device_class": device_class,
                        "file_pattern": file_pattern,
                    },
                    "summary": {
                        "files_searched": len(search_files),
                        "files_with_matches": len(results),
                        "total_matches": sum(r["match_count"] for r in results),
                    },
                    "results": results,
                },
                indent=2,
                ensure_ascii=False,
            )

        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    def validate_yaml_syntax(
        file_path: Optional[str] = None,
        yaml_content: Optional[str] = None,
        check_entities_services: bool = False,
        check_templates: bool = False,
    ) -> str:
        """
        🚀 PRE-DEPLOYMENT CHECK - validates YAML and checks references to entities/services/templates.
        """
        issues = []
        parsed_data = None

        try:
            if yaml_content:
                parsed_data = yaml.load(yaml_content, Loader=HomeAssistantLoader)
            elif file_path:
                parsed_data = _load_yaml_file_internal(file_path)
                if parsed_data is None and not (Path(config_path) / file_path).exists():
                    return json.dumps(
                        {
                            "success": False,
                            "syntax_valid": False,
                            "error": f"File '{file_path}' not found",
                        },
                        indent=2,
                    )
            else:
                return json.dumps(
                    {
                        "success": False,
                        "error": "Either 'file_path' or 'yaml_content' must be provided",
                    },
                    indent=2,
                )

            if parsed_data is None and yaml_content:
                issues.append({"type": "syntax_warning", "message": "YAML content is empty"})

        except yaml.YAMLError as e:
            return json.dumps(
                {
                    "success": False,
                    "syntax_valid": False,
                    "error": f"YAML syntax error: {str(e)}",
                },
                indent=2,
            )

        if (check_entities_services or check_templates) and (not ha_url or not ha_token):
            issues.append(
                {
                    "type": "api_missing_warning",
                    "message": "HA API URL/Token not configured, skipping runtime checks",
                }
            )
        elif parsed_data:
            found_entities: Set[str] = set()
            found_services: Set[str] = set()
            found_templates: Set[str] = set()
            _extract_entities_and_services(
                parsed_data, found_entities, found_services, found_templates
            )

            if check_entities_services and ha_url and ha_token:
                all_states_result = make_ha_request(ha_url, ha_token, "/api/states")
                if all_states_result["success"]:
                    states_dict = {s["entity_id"]: s for s in all_states_result["data"]}
                    for entity_id in found_entities:
                        if entity_id in states_dict:
                            state = states_dict[entity_id]["state"]
                            if state == "unavailable":
                                issues.append(
                                    {
                                        "type": "entity_unavailable",
                                        "message": f"Entity '{entity_id}' is currently 'unavailable'",
                                    }
                                )
                        else:
                            issues.append(
                                {
                                    "type": "entity_not_found",
                                    "message": f"Entity '{entity_id}' not found in Home Assistant",
                                }
                            )

                services_result = make_ha_request(ha_url, ha_token, "/api/services")
                if services_result["success"]:
                    all_services = set()
                    for domain_data in services_result["data"]:
                        domain = domain_data["domain"]
                        for service_name in domain_data.get("services", {}):
                            all_services.add(f"{domain}.{service_name}")
                    for service_call in found_services:
                        if service_call not in all_services:
                            issues.append(
                                {
                                    "type": "service_not_found",
                                    "message": f"Service '{service_call}' not found in Home Assistant",
                                }
                            )

            if check_templates and ha_url and ha_token and found_templates:
                for template_str in list(found_templates)[:10]:
                    template_result = make_ha_request(
                        ha_url,
                        ha_token,
                        "/api/template",
                        method="POST",
                        data={"template": template_str},
                    )
                    if not template_result["success"]:
                        issues.append(
                            {
                                "type": "template_error",
                                "message": f"Template syntax error: '{template_str[:100]}...' -> {template_result.get('error')}",
                            }
                        )

        return json.dumps(
            {"success": True, "syntax_valid": True, "issues": issues},
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    def get_lovelace_entity_usage(entity_id: str) -> str:
        """
        🚀 LOVELACE ENTITY USAGE - Locates entity in Lovelace dashboards.
        """
        try:
            usage_results = []

            dashboards_registry = load_registry("lovelace.dashboards", config_path)
            if not dashboards_registry:
                # If empty, try to find default file
                if not (Path(config_path) / ".storage/lovelace").exists():
                    return json.dumps(
                        {
                            "success": False,
                            "error": "Could not load lovelace.dashboards registry",
                        },
                        indent=2,
                    )

            dashboards_data = dashboards_registry.get("data", {}).get("items", [])
            dashboard_ids = [d.get("url_path", "lovelace") for d in dashboards_data]
            if "lovelace" not in dashboard_ids:
                dashboard_ids.insert(0, "lovelace")

            for dashboard_id in dashboard_ids:
                registry_name = (
                    f"lovelace.{dashboard_id}" if dashboard_id != "lovelace" else "lovelace"
                )
                lovelace_config = load_registry(registry_name, config_path)

                if not lovelace_config:
                    continue

                views = lovelace_config.get("data", {}).get("config", {}).get("views", [])

                for view_idx, view in enumerate(views):
                    cards = view.get("cards", [])
                    for card_idx, card in enumerate(cards):
                        card_str = json.dumps(card)

                        if entity_id in card_str:
                            role = "unknown"
                            if card.get("entity") == entity_id:
                                role = "main_entity"
                            elif "entities" in card:
                                entities_list = card["entities"]
                                if any(
                                    e == entity_id
                                    or (isinstance(e, dict) and e.get("entity") == entity_id)
                                    for e in entities_list
                                ):
                                    role = "entities_list"

                            usage_results.append(
                                {
                                    "dashboard": dashboard_id,
                                    "view_title": view.get("title", f"View {view_idx + 1}"),
                                    "card_type": card.get("type", "unknown"),
                                    "card_position": card_idx,
                                    "entity_id": entity_id,
                                    "role": role,
                                }
                            )

            return json.dumps(
                {
                    "success": True,
                    "entity_id": entity_id,
                    "usage_count": len(usage_results),
                    "usage": usage_results[:50],
                },
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)
