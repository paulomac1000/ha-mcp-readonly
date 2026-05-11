"""Core entry points for context generation."""

import os

from . import constants
from .analyzers import (
    AutomationAnalyzer,
    DashboardAnalyzer,
    EnergyAnalyzer,
    HacsAnalyzer,
    HelperAnalyzer,
    HistoryAnalyzer,
    LogAnalyzer,
    PersonAnalyzer,
    RegistryCollector,
    ServiceCatalogAnalyzer,
    TemplateEntityCollector,
    ZoneAnalyzer,
)
from .formatters import ReportGenerator
from .utils import invalidate_registry_cache


def main():
    """Run the full context generation pipeline."""
    # Re-read env vars at runtime — constants may have been imported before env was set
    constants.HA_URL = os.getenv("HA_URL", constants.HA_URL)
    constants.HA_TOKEN = os.getenv("HA_TOKEN", constants.HA_TOKEN)
    constants.HA_CONFIG_PATH = os.getenv("HA_CONFIG_PATH", constants.HA_CONFIG_PATH)
    constants.OUTPUT_FILE = os.getenv("OUTPUT_PATH", constants.OUTPUT_FILE)

    print("\n" + "=" * 60)
    print("HOME ASSISTANT CONTEXT GENERATOR FOR AI (v1.0)")
    print("   Comprehensive smart home context for AI assistants")
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
    history.analyze(hours=1)

    persons = PersonAnalyzer(registry)
    persons.collect()

    zones = ZoneAnalyzer(registry)
    zones.collect()

    energy = EnergyAnalyzer(registry)
    energy.collect()

    helpers = HelperAnalyzer(registry)
    helpers.collect()

    services = ServiceCatalogAnalyzer(registry)
    services.collect()

    hacs = HacsAnalyzer(registry)
    hacs.collect()

    # Generate report
    generator = ReportGenerator(
        registry,
        automation,
        dashboard,
        logs,
        templates,
        history,
        persons,
        zones,
        energy,
        helpers,
        services,
        hacs,
    )
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
    print(f"   Persons: {len(persons.persons)}")
    print(f"   Zones: {len(zones.zones)}")
    print(f"   Energy sensors: {len(energy.energy_sensors)}")
    print(
        f"   Helpers: {len(helpers.timers)}T/{len(helpers.counters)}C/{len(helpers.input_booleans)}B/{len(helpers.input_numbers)}N"
    )
    print(f"   Services: {services.total_services}")
    print(f"   HACS: {len(hacs.hacs_repos)} repos, {len(hacs.custom_components)} custom")
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
        os.environ["HA_CONFIG_PATH"] = config_path
        constants.HA_CONFIG_PATH = config_path
    if output_path:
        constants.OUTPUT_FILE = output_path
    if ha_url:
        os.environ["HA_URL"] = ha_url
        constants.HA_URL = ha_url
    if ha_token:
        os.environ["HA_TOKEN"] = ha_token
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
