"""
Configuration Management Tools
Provides tools for reading, searching, validating, and analyzing Home Assistant configuration files.
Focuses on token efficiency for AI interactions.
"""

import json
import logging
import os
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml

from tools.utils import _error_response, _success_response, load_registry, make_ha_request
from tools.yaml_utils import HomeAssistantLoader, load_yaml_file

_logger = logging.getLogger(__name__)

TOOLS_VERSION = "1.0.0"


# ========================================
# INTERNAL HELPERS
# ========================================


def _load_yaml_file_internal(file_path: str, config_path: str) -> Any | None:
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


def _extract_entities_and_services(  # type: ignore[no-untyped-def]
    data: Any,
    found_entities: set[str],
    found_services: set[str],
    found_templates: set[str],
):
    """Recursively extracts entity_ids, service calls, and templates."""
    entity_pattern = re.compile(
        r"\b(?:sensor|binary_sensor|light|switch|climate|cover|input_\w+|automation|script|person|device_tracker|"
        r"media_player|camera|lock|fan|vacuum|weather|sun|zone|timer|counter|number|select|button|scene|group|alarm_control_panel)\.[a-zA-Z0-9_]+\b"
    )
    service_pattern = re.compile(r"\b[a-z_]+\.[a-z_]+\b")
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
            _extract_entities_and_services(value, found_entities, found_services, found_templates)
    elif isinstance(data, list):
        for item in data:
            _extract_entities_and_services(item, found_entities, found_services, found_templates)


def _search_dict_internal(
    obj: Any,
    entity_id: str | None,
    service: str | None,
    platform: str | None,
    device_class: str | None,
    path: str = "",
    parent_context: Any = None,
) -> list[Any]:
    """Recursive search within a parsed YAML dict for matching params."""
    matches = []
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
            matches.extend(
                _search_dict_internal(
                    value,
                    entity_id,
                    service,
                    platform,
                    device_class,
                    f"{path}.{key}" if path else key,
                    obj,
                )
            )
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            matches.extend(
                _search_dict_internal(
                    item,
                    entity_id,
                    service,
                    platform,
                    device_class,
                    f"{path}[{idx}]",
                    obj,
                )
            )
    return matches


# ========================================
# BUSINESS LOGIC FUNCTIONS
# ========================================


def _do_get_main_configuration(config_path: str) -> dict[str, Any]:
    config_file_path = "configuration.yaml"
    data = _load_yaml_file_internal(config_file_path, config_path)
    if data is None:
        return {"success": False, "error": f"{config_file_path} not found or invalid YAML"}
    sanitized_data = _sanitize_config(data)
    return {
        "success": True,
        "data": yaml.dump(
            sanitized_data,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ),
    }


def _do_list_custom_components(config_path: str) -> dict[str, Any]:
    custom_dir = Path(config_path) / "custom_components"
    if not custom_dir.exists():
        return {"success": False, "error": "custom_components directory not found"}
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
                    with open(manifest_path, encoding="utf-8") as f:
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
    return {
        "success": True,
        "total_custom_components": len(components),
        "components": components,
    }


def _do_list_themes(config_path: str) -> dict[str, Any]:
    themes_dir = Path(config_path) / "themes"
    if not themes_dir.exists():
        return {"success": False, "error": "themes directory not found"}
    themes = []
    for theme_file in os.listdir(themes_dir):
        if theme_file.endswith((".yaml", ".yml")):
            info = {"file": theme_file}
            try:
                data = _load_yaml_file_internal(f"themes/{theme_file}", config_path)
                if data:
                    info["theme_names"] = list(data.keys())  # type: ignore[assignment]
            except Exception as e:
                info["parse_error"] = str(e)
            themes.append(info)
    return {"success": True, "total_theme_files": len(themes), "themes": themes}


def _do_get_config_structure(config_path: str) -> dict[str, Any]:
    structure = {}
    for root, dirs, files in os.walk(config_path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        relative_root = Path(root).relative_to(config_path)
        if str(relative_root) == ".":
            relative_root = Path("root")
        yaml_files = [f for f in files if f.endswith((".yaml", ".yml"))]
        json_files = [f for f in files if f.endswith(".json")]
        other_files = [
            f for f in files if not f.endswith((".yaml", ".yml", ".json")) and not f.startswith(".")
        ]
        if yaml_files or json_files or other_files or dirs:
            structure[str(relative_root)] = {
                "subdirectories": dirs,
                "yaml_files": yaml_files,
                "json_files": json_files,
                "other_files_sample": other_files[:10],
            }
    return {"success": True, "structure": structure}


def _do_read_config_file(
    file_path: str, max_lines: int, offset: int, config_path: str
) -> dict[str, Any]:
    full_path = Path(config_path) / file_path
    real_path = os.path.realpath(full_path)
    real_config = os.path.realpath(config_path)
    if not real_path.startswith(real_config):
        return {"success": False, "error": "Access denied - path outside configuration directory"}
    if not os.path.isfile(full_path):
        return {"success": False, "error": f"File '{file_path}' not found"}
    file_size = os.path.getsize(full_path)
    max_size = 200 * 1024
    if offset < 1:
        offset = 1
    try:
        with open(full_path, encoding="utf-8") as f:
            if file_size > max_size:
                lines = []
                for i, line in enumerate(f):
                    if i < offset - 1:
                        continue
                    if i >= offset - 1 + max_lines:
                        lines.append("...")
                        break
                    lines.append(line.rstrip("\n"))
                return {"success": True, "content": "\n".join(lines)}
            else:
                if offset == 1 and max_lines >= 200:
                    return {"success": True, "content": f.read()}
                lines = []
                for i, line in enumerate(f):
                    if i < offset - 1:
                        continue
                    if i >= offset - 1 + max_lines:
                        lines.append("...")
                        break
                    lines.append(line.rstrip("\n"))
                return {"success": True, "content": "\n".join(lines)}
    except FileNotFoundError:
        return {"success": False, "error": f"File '{file_path}' not found"}


def _do_search_in_config_batch(
    search_terms: str,
    file_types: str,
    match_mode: str,
    config_path: str,
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> dict[str, Any]:
    terms = [term.strip() for term in search_terms.split(",") if term.strip()]
    if not terms:
        return {"success": False, "error": "No search terms provided"}
    extensions = []
    if file_types in ["yaml", "all"]:
        extensions.extend([".yaml", ".yml"])
    if file_types in ["json", "all"]:
        extensions.append(".json")
    results_by_term = {term: [] for term in terms}  # type: ignore[var-annotated]
    matching_files = []
    for root, dirs, files in os.walk(config_path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for file in files:
            if any(file.endswith(ext) for ext in extensions):
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, config_path)
                try:
                    with open(file_path, encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    content_lower = content.lower()
                    lines = content.split("\n")
                    current_file_matched_terms = []
                    current_file_matches_detail = {}
                    for term in terms:
                        term_lower = term.lower()
                        if term_lower in content_lower:
                            current_file_matched_terms.append(term)
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
                    if (match_mode == "all" and len(current_file_matched_terms) == len(terms)) or (
                        match_mode == "any" and len(current_file_matched_terms) > 0
                    ):
                        matching_files.append(
                            {
                                "file": relative_path,
                                "matched_terms": current_file_matched_terms,
                                "matches_detail": current_file_matches_detail,
                            }
                        )
                except Exception:
                    continue
    for term in results_by_term:
        results_by_term[term] = results_by_term[term][:20]
    matching_files = matching_files[:30]
    return {
        "success": True,
        "search_terms": terms,
        "match_mode": match_mode,
        "summary": {
            "files_matching_criteria": len(matching_files),
            "results_per_term": {term: len(results_by_term[term]) for term in terms},
        },
        "matching_files": matching_files,
        "results_by_term": results_by_term,
    }


def _do_search_config_by_params(
    entity_id: str | None = None,
    service: str | None = None,
    platform: str | None = None,
    device_class: str | None = None,
    file_pattern: str | None = None,
    config_path: str | None = None,
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> dict[str, Any]:
    if not any([entity_id, service, platform, device_class]):
        return {"success": False, "error": "At least one search parameter required"}
    results = []
    search_files = []
    for root, dirs, files in os.walk(config_path):  # type: ignore[type-var]
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]  # type: ignore[union-attr]
        for file in files:
            if file.endswith((".yaml", ".yml")):  # type: ignore[union-attr]
                file_path = os.path.join(root, file)  # type: ignore[arg-type]
                relative_path = os.path.relpath(file_path, config_path)
                if file_pattern and not fnmatch(relative_path, file_pattern):
                    continue
                search_files.append((file_path, relative_path))
    for file_path, relative_path in search_files:
        try:
            data = _load_yaml_file_internal(relative_path, config_path)  # type: ignore[arg-type]
            if not data:
                continue
            matches = _search_dict_internal(
                data,
                entity_id,
                service,
                platform,
                device_class,
            )
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
    return {
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
    }


def _do_validate_yaml_syntax(
    file_path: str | None = None,
    yaml_content: str | None = None,
    check_entities_services: bool = False,
    check_templates: bool = False,
    config_path: str | None = None,
    ha_url: str | None = None,
    ha_token: str | None = None,
) -> dict[str, Any]:
    issues = []
    parsed_data = None
    if yaml_content:
        try:
            parsed_data = yaml.load(yaml_content, Loader=HomeAssistantLoader)  # nosec B506
        except yaml.YAMLError as e:
            return {
                "success": False,
                "syntax_valid": False,
                "error": f"YAML syntax error: {str(e)}",
            }
    elif file_path:
        parsed_data = _load_yaml_file_internal(file_path, config_path)  # type: ignore[arg-type]
        if parsed_data is None and not (Path(config_path) / file_path).exists():  # type: ignore[arg-type]
            return {
                "success": False,
                "syntax_valid": False,
                "error": f"File '{file_path}' not found",
            }
    else:
        return {"success": False, "error": "Either 'file_path' or 'yaml_content' must be provided"}
    if parsed_data is None and yaml_content:
        issues.append({"type": "syntax_warning", "message": "YAML content is empty"})
    if (check_entities_services or check_templates) and (not ha_url or not ha_token):
        issues.append(
            {
                "type": "api_missing_warning",
                "message": "HA API URL/Token not configured, skipping runtime checks",
            }
        )
    elif parsed_data:
        found_entities: set[str] = set()
        found_services: set[str] = set()
        found_templates: set[str] = set()
        _extract_entities_and_services(parsed_data, found_entities, found_services, found_templates)
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
    return {"success": True, "syntax_valid": True, "issues": issues}


def _do_get_lovelace_entity_usage(entity_id: str, config_path: str) -> dict[str, Any]:
    usage_results = []
    dashboards_registry = load_registry("lovelace.dashboards", config_path)
    if not dashboards_registry:
        if not (Path(config_path) / ".storage/lovelace").exists():
            return {"success": False, "error": "Could not load lovelace.dashboards registry"}
    dashboards_data = dashboards_registry.get("data", {}).get("items", [])
    dashboard_ids = [d.get("url_path", "lovelace") for d in dashboards_data]
    if "lovelace" not in dashboard_ids:
        dashboard_ids.insert(0, "lovelace")
    for dashboard_id in dashboard_ids:
        registry_name = f"lovelace.{dashboard_id}" if dashboard_id != "lovelace" else "lovelace"
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
                            e == entity_id or (isinstance(e, dict) and e.get("entity") == entity_id)
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
    return {
        "success": True,
        "entity_id": entity_id,
        "usage_count": len(usage_results),
        "usage": usage_results[:50],
    }


# ========================================
# TOOL REGISTRATION
# ========================================


def register_config_tools(  # type: ignore[no-untyped-def]
    mcp, config_path: str, ha_url: str | None = None, ha_token: str | None = None
):
    """
    Registers tools for managing Home Assistant configuration.
    """

    @mcp.tool()
    def get_main_configuration() -> str:
        """[READ] Fetches main configuration from file `configuration.yaml`.
        Returns structured YAML, with sensitive data (passwords, tokens) redacted.
        """
        try:
            result = _do_get_main_configuration(config_path)
            return _success_response(result)
        except Exception as e:
            _logger.exception("get_main_configuration failed")
            return _error_response(str(e))

    @mcp.tool()
    def list_custom_components() -> str:
        """[READ] Fetches list of installed custom components (custom_components/)."""
        try:
            result = _do_list_custom_components(config_path)
            return _success_response(result)
        except Exception as e:
            _logger.exception("list_custom_components failed")
            return _error_response(str(e))

    @mcp.tool()
    def list_themes() -> str:
        """[READ] Fetches list of installed themes (themes/).

        Returns:
            JSON with list of installed themes, their paths, and metadata.
        """
        try:
            result = _do_list_themes(config_path)
            return _success_response(result)
        except Exception as e:
            _logger.exception("list_themes failed")
            return _error_response(str(e))

    @mcp.tool()
    def get_config_structure() -> str:
        """[READ] Returns structure of directories and files in the Home Assistant configuration directory.

        Returns:
            JSON with directory tree structure of the HA config directory.
        """
        try:
            result = _do_get_config_structure(config_path)
            return _success_response(result)
        except Exception as e:
            _logger.exception("get_config_structure failed")
            return _error_response(str(e))

    @mcp.tool()
    def read_config_file(file_path: str, max_lines: int = 200, offset: int = 1) -> str:
        """[READ] Read a configuration file with optional line offset and limit.

        Args:
            file_path: Path relative to the HA config directory (e.g. "automations.yaml").
            max_lines: Maximum number of lines to return (default 200).
            offset: Line number to start reading from, 1-indexed (default 1).

        Returns:
            File content as text, or JSON error.
        """
        try:
            result = _do_read_config_file(file_path, max_lines, offset, config_path)
            if result.get("success") and "content" in result:
                return result["content"]  # type: ignore[no-any-return]
            return _success_response(result)
        except Exception as e:
            _logger.exception("read_config_file failed")
            return _error_response(str(e))

    @mcp.tool()
    def search_in_config_batch(
        search_terms: str, file_types: str = "yaml", match_mode: str = "any"
    ) -> str:
        """[READ] Search for multiple text phrases across many config files simultaneously.

        Args:
            search_terms: Space or comma-separated search phrases.
            file_types: File extension filter (default "yaml").
            match_mode: "any" to match any term, "all" to match all (default "any").

        Returns:
            JSON with matched files, line numbers, and context snippets.
        """
        try:
            result = _do_search_in_config_batch(
                search_terms=search_terms,
                file_types=file_types,
                match_mode=match_mode,
                config_path=config_path,
            )
            return _success_response(result)
        except Exception as e:
            _logger.exception("search_in_config_batch failed")
            return _error_response(str(e))

    @mcp.tool()
    def search_in_config(search_term: str, file_types: str = "yaml") -> str:
        """[READ] Searches for a specific phrase in all configuration files.

        Args:
            search_term: Phrase to search for in config files.
            file_types: File extension filter (default "yaml").

        Returns:
            JSON with list of matching files and line numbers.
        """
        try:
            result = _do_search_in_config_batch(
                search_terms=search_term,
                file_types=file_types,
                match_mode="any",
                config_path=config_path,
            )
            return _success_response(result)
        except Exception as e:
            _logger.exception("search_in_config failed")
            return _error_response(str(e))

    @mcp.tool()
    def search_config_by_params(
        entity_id: str | None = None,
        service: str | None = None,
        platform: str | None = None,
        device_class: str | None = None,
        file_pattern: str | None = None,
    ) -> str:
        """[READ] Search YAML configuration files by structured parameters (entity_id, service, platform, device_class).

        Args:
            entity_id: Optional entity id to search for.
            service: Optional service name to search for.
            platform: Optional platform name to filter by.
            device_class: Optional device class to filter by.
            file_pattern: Optional file name pattern to limit search scope.

        Returns:
            JSON with matched config entries and their locations.
        """
        try:
            result = _do_search_config_by_params(
                entity_id=entity_id,
                service=service,
                platform=platform,
                device_class=device_class,
                file_pattern=file_pattern,
                config_path=config_path,
            )
            return _success_response(result)
        except Exception as e:
            _logger.exception("search_config_by_params failed")
            return _error_response(str(e))

    @mcp.tool()
    def validate_yaml_syntax(
        file_path: str | None = None,
        yaml_content: str | None = None,
        check_entities_services: bool = False,
        check_templates: bool = False,
    ) -> str:
        """[READ] Pre-deployment check: validates YAML syntax and checks references to entities/services/templates.

        Args:
            file_path: Path to YAML file to validate.
            yaml_content: Raw YAML string to validate (alternative to file_path).
            check_entities_services: Whether to validate referenced entities and services.
            check_templates: Whether to test Jinja2 templates.

        Returns:
            JSON with validation result, errors, warnings, and entity/service checks.
        """
        try:
            result = _do_validate_yaml_syntax(
                file_path=file_path,
                yaml_content=yaml_content,
                check_entities_services=check_entities_services,
                check_templates=check_templates,
                config_path=config_path,
                ha_url=ha_url,
                ha_token=ha_token,
            )
            return _success_response(result)
        except Exception as e:
            _logger.exception("validate_yaml_syntax failed")
            return _error_response(str(e))

    @mcp.tool()
    def get_lovelace_entity_usage(entity_id: str) -> str:
        """[READ] Locate an entity across all Lovelace dashboards.

        Args:
            entity_id: Entity id to locate in Lovelace dashboards.

        Returns:
            JSON with dashboard locations, card types, and view positions where entity is used.
        """
        try:
            result = _do_get_lovelace_entity_usage(entity_id, config_path)
            return _success_response(result)
        except Exception as e:
            _logger.exception("get_lovelace_entity_usage failed")
            return _error_response(str(e))
