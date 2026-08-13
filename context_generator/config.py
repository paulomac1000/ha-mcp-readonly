"""Immutable configuration for one context-generation run."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

GenerationMode = Literal["offline", "online", "hybrid"]
DEFAULT_MAX_OUTPUT_BYTES = 96 * 1024 * 1024
MAX_CONTEXT_ARTIFACT_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    """All inputs needed by one isolated context-generation run."""

    config_path: Path
    output_path: Path
    ha_url: str
    ha_token: str
    mode: GenerationMode = "hybrid"
    history_hours: int = 1
    log_hours: int = 24
    calendar_days: int = 30
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_source_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.mode not in {"offline", "online", "hybrid"}:
            raise ValueError("mode must be offline, online, or hybrid")
        if self.history_hours < 0 or self.log_hours < 0 or self.calendar_days < 0:
            raise ValueError("history_hours, log_hours and calendar_days cannot be negative")
        if self.max_output_bytes < 1024 or self.max_source_bytes < 1024:
            raise ValueError("generation size limits are too small")

    @property
    def network_enabled(self) -> bool:
        return self.mode != "offline" and bool(self.ha_url and self.ha_token)

    @property
    def network_required(self) -> bool:
        return self.mode == "online"

    @classmethod
    def from_env(cls) -> GenerationConfig:
        mode = os.getenv("HA_CONTEXT_MODE", "hybrid").strip().casefold()
        if mode not in {"offline", "online", "hybrid"}:
            raise ValueError("HA_CONTEXT_MODE must be offline, online, or hybrid")
        output = os.getenv("OUTPUT_PATH", "ha-ai-context.md")
        return cls(
            config_path=Path(os.getenv("HA_CONFIG_PATH", "/config")),
            output_path=Path(output),
            ha_url=os.getenv("HA_URL", "http://homeassistant:8123"),
            ha_token=os.getenv("HA_TOKEN", ""),
            mode=mode,  # type: ignore[arg-type]
            history_hours=int(os.getenv("HA_CONTEXT_HISTORY_HOURS", "1")),
            log_hours=int(os.getenv("HA_CONTEXT_LOG_HOURS", "24")),
            calendar_days=int(os.getenv("HA_CONTEXT_CALENDAR_DAYS", "30")),
            max_output_bytes=int(
                os.getenv("HA_CONTEXT_MAX_OUTPUT_BYTES", str(DEFAULT_MAX_OUTPUT_BYTES))
            ),
            max_source_bytes=int(os.getenv("HA_CONTEXT_MAX_SOURCE_BYTES", str(64 * 1024 * 1024))),
        )
