"""Provide section selection and byte-budget staging for Markdown report generation."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, BinaryIO

SECTION_ORDER: tuple[str, ...] = (
    "executive_summary",
    "source_provenance",
    "cache_health",
    "system_health",
    "integration_status",
    "topology",
    "automation_logic",
    "entity_dependency_graph",
    "conflict_analysis",
    "template_entities",
    "persons",
    "zones",
    "energy",
    "helpers",
    "services",
    "hacs",
    "dashboards",
    "logs",
    "recent_changes",
    "snapshot",
    "quick_reference",
)

SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "runtime": ("integration_status",),
    "health": ("system_health", "cache_health"),
    "provenance": ("source_provenance",),
    "logs": ("logs",),
}

# repositoryFiles is a boolean configuration option, not a selectable report section.
OPTION_ALIASES: dict[str, str] = {
    "repositoryFiles": "include_repository_files",
}

if TYPE_CHECKING:
    from _typeshed import SupportsWrite

PROFILES: dict[str, frozenset[str] | None] = {
    "full": None,
    "agent": frozenset(
        section
        for section in SECTION_ORDER
        if section not in {"snapshot", "logs", "recent_changes"}
    ),
    "compact": frozenset(
        {
            "executive_summary",
            "source_provenance",
            "system_health",
            "topology",
            "quick_reference",
        }
    ),
}


def resolve_sections(
    profile: str,
    include_sections: Sequence[str] | None,
) -> tuple[str, ...]:
    """Resolve a profile and optional section selection to canonical section keys.

    Args:
        profile: Named section profile.
        include_sections: Explicit canonical section keys or supported aliases. When
            provided, this selection overrides the profile's section membership.

    Returns:
        Canonical, deduplicated section keys ordered according to SECTION_ORDER.

    Raises:
        ValueError: If the profile or a section is unknown, or no sections resolve.
    """
    if profile not in PROFILES:
        valid_profiles = ", ".join(sorted(PROFILES))
        raise ValueError(f"unknown profile {profile!r}; valid profiles: {valid_profiles}")

    if include_sections is None:
        profile_sections = PROFILES[profile]
        if profile_sections is None:
            return SECTION_ORDER

        resolved_profile = tuple(
            section for section in SECTION_ORDER if section in profile_sections
        )
        if not resolved_profile:
            raise ValueError("resolved section set is empty")
        return resolved_profile

    canonical_sections = frozenset(SECTION_ORDER)
    resolved: set[str] = set()

    for requested in include_sections:
        if requested in SECTION_ALIASES:
            resolved.update(SECTION_ALIASES[requested])
            continue

        if requested in canonical_sections:
            resolved.add(requested)
            continue

        valid_options = ", ".join(sorted(canonical_sections.union(SECTION_ALIASES)))
        raise ValueError(f"unknown section {requested!r}; valid options: {valid_options}")

    ordered = tuple(section for section in SECTION_ORDER if section in resolved)
    if not ordered:
        raise ValueError("resolved section set is empty")

    return ordered


def resolve_overflow_policy(on_budget_exceeded: str, profile: str) -> str:
    """Resolve the configured output-budget overflow policy.

    Args:
        on_budget_exceeded: Configured policy: fail, truncate, or auto.
        profile: Active generation profile.

    Returns:
        Either "fail" or "truncate".

    Raises:
        ValueError: If the configured policy is unsupported.
    """
    if on_budget_exceeded in {"fail", "truncate"}:
        return on_budget_exceeded

    if on_budget_exceeded == "auto":
        return "fail" if profile == "full" else "truncate"

    valid_options = "auto, fail, truncate"
    raise ValueError(
        f"unknown budget overflow policy {on_budget_exceeded!r}; valid options: {valid_options}"
    )


@dataclass(frozen=True, slots=True)
class OmittedSection:
    """Describe a selected section omitted from the final artifact."""

    section: str
    reason: str
    section_bytes: int


@dataclass(frozen=True, slots=True)
class SectionManifest:
    """Describe section selection and rendering outcomes for one report."""

    requested: tuple[str, ...]
    selected: tuple[str, ...]
    rendered: tuple[str, ...]
    omitted: tuple[OmittedSection, ...]
    truncated: bool

    @property
    def omitted_bytes(self) -> int:
        """Return the total staged byte size of omitted sections."""
        return sum(item.section_bytes for item in self.omitted)

    def to_json_dict(self) -> dict[str, Any]:
        """Convert the manifest to JSON-compatible built-in containers.

        Returns:
            A dictionary containing lists and scalar values only.
        """
        return {
            "requested": list(self.requested),
            "selected": list(self.selected),
            "rendered": list(self.rendered),
            "omitted": [
                {
                    "section": item.section,
                    "reason": item.reason,
                    "section_bytes": item.section_bytes,
                }
                for item in self.omitted
            ],
            "truncated": self.truncated,
        }


class BudgetedSectionWriter:
    """Stage report sections before committing them to a bounded binary artifact."""

    _SPOOL_MAX_SIZE = 256 * 1024

    def __init__(self, handle: BinaryIO, max_bytes: int, policy: str) -> None:
        """Initialize a budget-aware section writer.

        Args:
            handle: Open binary output handle positioned on the atomic temp artifact.
            max_bytes: Maximum permitted artifact size in bytes.
            policy: Resolved overflow policy associated with this generation run.
        """
        self._handle = handle
        self._max_bytes = max_bytes
        self._policy = policy
        self._handle.seek(0, os.SEEK_END)
        self._running_size = self._handle.tell()
        self._requested: list[str] = []
        self._selected: list[str] = []
        self._rendered: list[str] = []
        self._omitted: list[OmittedSection] = []

    def write_mandatory(self, section: str, render: Callable[[SupportsWrite[bytes]], None]) -> None:
        """Stage and commit a mandatory section when it fits the byte budget.

        Args:
            section: Section key used for manifest reporting.
            render: Callback that writes the complete section to a binary handle.

        Raises:
            ValueError: If the staged section would exceed the byte budget.
        """
        staged = self._stage(render)
        size = len(staged)

        self._requested.append(section)
        self._selected.append(section)

        if self._running_size + size > self._max_bytes:
            raise ValueError(
                f"mandatory section {section!r} does not fit within the {self._max_bytes} byte budget"
            )

        self._commit(staged)
        self._rendered.append(section)

    def write_optional(self, section: str, render: Callable[[SupportsWrite[bytes]], None]) -> bool:
        """Stage an optional section and commit it only when it fits.

        Args:
            section: Section key used for manifest reporting.
            render: Callback that writes the complete section to a binary handle.

        Returns:
            True when the section was committed, otherwise False.
        """
        staged = self._stage(render)
        size = len(staged)

        self._requested.append(section)
        self._selected.append(section)

        if self._running_size + size > self._max_bytes:
            self._omitted.append(
                OmittedSection(
                    section=section,
                    reason="output budget exceeded",
                    section_bytes=size,
                )
            )
            return False

        self._commit(staged)
        self._rendered.append(section)
        return True

    @property
    def manifest(self) -> SectionManifest:
        """Return an immutable snapshot of the current section manifest."""
        return SectionManifest(
            requested=tuple(self._requested),
            selected=tuple(self._selected),
            rendered=tuple(self._rendered),
            omitted=tuple(self._omitted),
            truncated=bool(self._omitted),
        )

    def _stage(self, render: Callable[[SupportsWrite[bytes]], None]) -> bytes:
        with tempfile.SpooledTemporaryFile(max_size=self._SPOOL_MAX_SIZE, mode="w+b") as staged:
            render(staged)
            staged.seek(0)
            return staged.read()

    def _commit(self, staged: bytes) -> None:
        written = self._handle.write(staged)
        if written is not None and written != len(staged):
            raise OSError(f"short write: expected {len(staged)} bytes, wrote {written}")
        self._running_size += len(staged)


def artifact_digest(path: str | os.PathLike[str]) -> str:
    """Calculate the SHA-256 digest of an artifact using bounded reads.

    Args:
        path: Filesystem path to the artifact.

    Returns:
        Lowercase hexadecimal SHA-256 digest of the artifact bytes.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
