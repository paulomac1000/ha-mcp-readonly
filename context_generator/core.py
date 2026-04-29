"""Core entry points for context generation."""

from . import constants
from .analyzers import (
    AutomationAnalyzer,
    DashboardAnalyzer,
    HistoryAnalyzer,
    LogAnalyzer,
    RegistryCollector,
    TemplateEntityCollector,
)
from .formatters import ReportGenerator
from .utils import invalidate_registry_cache


def main():
    """Run the full context generation pipeline."""
    print("\n" + "=" * 60)
    print("HOME ASSISTANT CONTEXT GENERATOR FOR AI (V7)")
    print("   Full MCP Integration - Based on test patterns")
    print("=" * 60 + "\n")

    # Invalidate cache at start
    invalidate_registry_cache()

    # Collectors
    registry = RegistryCollector()
    if not registry.collect():
        print("Failed to collect data from registry. Check connection to HA.")
        exit(1)

    automation = AutomationAnalyzer(registry)
    automation.collect()
    automation.analyze()

    dashboard = DashboardAnalyzer(registry)
    dashboard.analyze()

    logs = LogAnalyzer()
    logs.analyze(constants.LOG_HOURS_BACK)

    templates = TemplateEntityCollector(registry)
    templates.collect()

    history = HistoryAnalyzer(registry)
    history.analyze(hours=1)  # Last hour for recent changes

    # Generate report
    generator = ReportGenerator(registry, automation, dashboard, logs, templates, history)
    generator.generate(constants.OUTPUT_FILE)

    # Summary
    print("\n" + "=" * 60)
    print("GENERATION SUMMARY")
    print("=" * 60)
    print(f"   Output file: {constants.OUTPUT_FILE}")
    print(f"   Entities: {len(registry.states)}")
    print(f"   Automations: {len(automation.automation_analysis)}")
    print(f"   Scripts: {len(automation.script_analysis)}")
    print(f"   Scenes: {len(automation.scene_analysis)}")
    print(f"   Ghost entities: {len(automation.ghost_entities)}")
    print(f"   Conflicts: {len(automation.conflicting_entities)}")
    print(f"   Dashboard entities: {len(dashboard.entity_in_dashboards)}")
    print(f"   Log errors: {len(logs.errors)}")
    print(f"   Template entities: {len(templates.template_entities)}")
    print("=" * 60 + "\n")


def generate_context_file(
    config_path: str = None,
    output_path: str = None,
    ha_url: str = None,
    ha_token: str = None,
    mode: str = "hybrid",
) -> dict:
    """Generate HA context file.

    Wrapper around main() that allows overriding paths and credentials.
    Runs the full context generation pipeline.

    Args:
        config_path: Override HA config path.
        output_path: Override output file path.
        ha_url: Override HA URL.
        ha_token: Override HA token.
        mode: "online", "offline", or "hybrid".

    Returns:
        Dictionary with generation statistics.
    """
    import os

    # Override constants for this run
    if config_path:
        constants.HA_CONFIG_PATH = config_path
    if output_path:
        constants.OUTPUT_FILE = output_path
    if ha_url:
        constants.HA_URL = ha_url
    if ha_token:
        constants.HA_TOKEN = ha_token

    # Ensure output directory exists
    out_dir = os.path.dirname(constants.OUTPUT_FILE)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Run generation
    main()

    return {
        "output_file": constants.OUTPUT_FILE,
        "config_path": constants.HA_CONFIG_PATH,
        "mode": mode,
    }


if __name__ == "__main__":
    main()
