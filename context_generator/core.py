"""Core entry points for isolated Home Assistant context generation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .analyzers import (
    AutomationAnalyzer,
    CacheAnalyzer,
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
from .config import GenerationConfig, GenerationMode
from .formatters import ReportGenerator
from .provenance import ProvenanceTracker
from .runtime import GenerationRuntime, generation_scope
from .snapshot import ComprehensiveSnapshotCollector
from .utils import invalidate_registry_cache

_logger = logging.getLogger(__name__)


class GenerationError(RuntimeError):
    """Controlled context-generation failure."""


def run_generation(config: GenerationConfig) -> dict[str, Any]:
    """Run one complete generation without mutating process-wide configuration."""
    config.output_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    provenance = ProvenanceTracker()
    runtime = GenerationRuntime(config=config, provenance=provenance)

    with generation_scope(runtime):
        invalidate_registry_cache()

        registry = RegistryCollector()
        if not registry.collect():
            raise GenerationError("Required Home Assistant data is unavailable")

        automation = AutomationAnalyzer(registry)
        automation.collect()
        automation.analyze()

        dashboard = DashboardAnalyzer(registry)
        dashboard.analyze()

        logs = LogAnalyzer()
        logs.analyze(config.log_hours)

        templates = TemplateEntityCollector(registry)
        templates.collect()

        history = HistoryAnalyzer(registry)
        history.analyze(hours=config.history_hours)

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

        cache = CacheAnalyzer(registry)
        cache.collect()

        snapshot_collector = ComprehensiveSnapshotCollector(config, provenance)
        snapshot = snapshot_collector.collect()

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
            cache=cache,
            generation_config=config,
            provenance=provenance,
            comprehensive_snapshot=snapshot,
        )
        generator.generate(str(config.output_path))

    if not config.output_path.exists():
        raise GenerationError("Context generator did not create an artifact")
    output_bytes = config.output_path.stat().st_size
    if output_bytes > config.max_output_bytes:
        config.output_path.unlink(missing_ok=True)
        raise GenerationError("Generated context exceeds configured output limit")

    summary = provenance.summary()
    return {
        "output_file": str(config.output_path),
        "config_path": str(config.config_path),
        "mode": config.mode,
        "output_bytes": output_bytes,
        "completeness": summary["completeness"],
        "source_counts": summary["counts"],
        "entities": len(registry.states),
        "registered_entities": len(registry.entities),
        "devices": len(registry.devices),
        "areas": len(registry.areas),
        "automations": len(automation.automation_analysis),
        "scripts": len(automation.script_analysis),
        "scenes": len(automation.scene_analysis),
    }


def generate_context_file(
    config_path: str | None = None,
    output_path: str | None = None,
    ha_url: str | None = None,
    ha_token: str | None = None,
    mode: GenerationMode = "hybrid",
) -> dict[str, Any]:
    """Generate a context artifact from explicit, per-call configuration."""
    defaults = GenerationConfig.from_env()
    config = GenerationConfig(
        config_path=Path(config_path) if config_path is not None else defaults.config_path,
        output_path=Path(output_path) if output_path is not None else defaults.output_path,
        ha_url=ha_url if ha_url is not None else defaults.ha_url,
        ha_token=ha_token if ha_token is not None else defaults.ha_token,
        mode=mode,
        history_hours=defaults.history_hours,
        log_hours=defaults.log_hours,
        calendar_days=defaults.calendar_days,
        max_output_bytes=defaults.max_output_bytes,
        max_source_bytes=defaults.max_source_bytes,
    )
    return run_generation(config)


def main() -> None:
    """CLI-compatible entry point using environment-derived configuration."""
    run_generation(GenerationConfig.from_env())


if __name__ == "__main__":
    try:
        main()
    except GenerationError as exc:
        _logger.error("Context generation failed: %s", exc)
        raise SystemExit(1) from exc
