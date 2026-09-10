"""Immutable configuration for one context-generation run."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .budget import PROFILES, resolve_sections

GenerationMode = Literal["offline", "online", "hybrid"]
DEFAULT_MAX_OUTPUT_BYTES = 96 * 1024 * 1024
MAX_CONTEXT_ARTIFACT_BYTES = 128 * 1024 * 1024
BUDGET_POLICIES = frozenset({"auto", "fail", "truncate"})


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
    profile: str = "full"
    include_sections: tuple[str, ...] | None = None
    include_repository_files: bool = True
    include_storage_records: bool = True
    on_budget_exceeded: str = "auto"

    def __post_init__(self) -> None:
        if self.mode not in {"offline", "online", "hybrid"}:
            raise ValueError("mode must be offline, online, or hybrid")
        if self.history_hours < 0 or self.log_hours < 0 or self.calendar_days < 0:
            raise ValueError("history_hours, log_hours and calendar_days cannot be negative")
        if self.max_output_bytes < 1024 or self.max_source_bytes < 1024:
            raise ValueError("generation size limits are too small")
        if self.max_output_bytes > MAX_CONTEXT_ARTIFACT_BYTES:
            raise ValueError(
                "HA_CONTEXT_MAX_OUTPUT_BYTES exceeds the supported artifact size "
                f"({MAX_CONTEXT_ARTIFACT_BYTES} bytes)"
            )
        if self.profile not in PROFILES:
            valid_profiles = ", ".join(sorted(PROFILES))
            raise ValueError(f"unknown profile {self.profile!r}; valid profiles: {valid_profiles}")
        if self.on_budget_exceeded not in BUDGET_POLICIES:
            valid_policies = ", ".join(sorted(BUDGET_POLICIES))
            raise ValueError(
                f"unknown budget overflow policy {self.on_budget_exceeded!r}; "
                f"valid options: {valid_policies}"
            )
        if not isinstance(self.include_repository_files, bool):
            raise ValueError("include_repository_files must be a boolean")
        if not isinstance(self.include_storage_records, bool):
            raise ValueError("include_storage_records must be a boolean")
        # resolve_sections doubles as eager validation (result intentionally discarded).
        resolve_sections(self.profile, self.include_sections)

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
        profile, include_sections = _resolve_profile_from_env()
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
            profile=profile,
            include_sections=include_sections,
            include_repository_files=_env_flag("HA_CONTEXT_INCLUDE_FILES", True),
            include_storage_records=_env_flag("HA_CONTEXT_INCLUDE_STORAGE", True),
            on_budget_exceeded=os.getenv("HA_CONTEXT_ON_BUDGET_EXCEEDED", "auto")
            .strip()
            .casefold(),
        )


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _resolve_profile_from_env() -> tuple[str, tuple[str, ...] | None]:
    """Resolve HA_CONTEXT_PROFILE, HA_CONTEXT_DETAIL and HA_CONTEXT_SECTIONS.

    Aliases are normalized before the canonical run configuration is built so
    the frozen dataclass only ever stores resolved values. Supplying both an
    explicit profile and a conflicting explicit detail is rejected.

    Returns:
        Tuple of the resolved profile name and the optional section selection.
    """
    profile_raw = os.getenv("HA_CONTEXT_PROFILE")
    detail_raw = os.getenv("HA_CONTEXT_DETAIL")
    profile = (profile_raw or "full").strip().casefold()
    sections_raw = os.getenv("HA_CONTEXT_SECTIONS")
    include_sections: tuple[str, ...] | None = None
    if sections_raw is not None and sections_raw.strip():
        include_sections = tuple(part.strip() for part in sections_raw.split(",") if part.strip())
    if detail_raw is not None and detail_raw.strip():
        detail = detail_raw.strip().casefold()
        if detail not in {"full", "compact"}:
            raise ValueError("HA_CONTEXT_DETAIL must be full or compact")
        if profile_raw is not None and profile_raw.strip() and profile != detail:
            raise ValueError("HA_CONTEXT_PROFILE and HA_CONTEXT_DETAIL conflict")
        if profile == "full" and detail == "compact":
            profile = "compact"
    return profile, include_sections
