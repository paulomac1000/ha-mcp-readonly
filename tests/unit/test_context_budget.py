"""Unit tests for section selection and byte-budget staging."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from context_generator.budget import (
    OPTION_ALIASES,
    PROFILES,
    SECTION_ALIASES,
    SECTION_ORDER,
    BudgetedSectionWriter,
    OmittedSection,
    artifact_digest,
    resolve_overflow_policy,
    resolve_sections,
)


def _binary_handle() -> BinaryIO:
    return cast(BinaryIO, io.BytesIO())


def _render_bytes(payload: bytes) -> Callable[[BinaryIO], None]:
    def render(handle: BinaryIO) -> None:
        handle.write(payload)

    return render


def _read_handle(handle: BinaryIO) -> bytes:
    handle.seek(0)
    return handle.read()


def test_section_constants_expose_expected_alias_contract() -> None:
    assert SECTION_ALIASES["runtime"] == ("integration_status",)
    assert SECTION_ALIASES["health"] == ("system_health", "cache_health")
    assert SECTION_ALIASES["provenance"] == ("source_provenance",)
    assert SECTION_ALIASES["logs"] == ("logs",)
    assert "repositoryFiles" not in SECTION_ALIASES
    assert OPTION_ALIASES["repositoryFiles"] == "include_repository_files"


def test_full_profile_defaults_to_every_section() -> None:
    assert resolve_sections("full", None) == SECTION_ORDER
    assert PROFILES["full"] is None


def test_agent_profile_excludes_exactly_large_or_churn_sections() -> None:
    resolved = resolve_sections("agent", None)

    assert resolved == tuple(
        section
        for section in SECTION_ORDER
        if section not in {"snapshot", "logs", "recent_changes"}
    )
    assert set(SECTION_ORDER) - set(resolved) == {
        "snapshot",
        "logs",
        "recent_changes",
    }


def test_compact_profile_contains_exact_expected_sections() -> None:
    assert resolve_sections("compact", None) == (
        "executive_summary",
        "source_provenance",
        "system_health",
        "topology",
        "quick_reference",
    )


def test_alias_expansion_supports_multi_section_alias_and_deduplication() -> None:
    resolved = resolve_sections(
        "full",
        (
            "health",
            "cache_health",
            "runtime",
            "integration_status",
            "provenance",
            "logs",
        ),
    )

    assert resolved == (
        "source_provenance",
        "cache_health",
        "system_health",
        "integration_status",
        "logs",
    )


def test_explicit_sections_override_profile_membership() -> None:
    assert resolve_sections("compact", ("snapshot", "energy")) == (
        "energy",
        "snapshot",
    )


def test_explicit_sections_are_returned_in_canonical_order() -> None:
    resolved = resolve_sections(
        "full",
        (
            "quick_reference",
            "executive_summary",
            "zones",
            "source_provenance",
        ),
    )

    assert resolved == (
        "executive_summary",
        "source_provenance",
        "zones",
        "quick_reference",
    )


@pytest.mark.parametrize("profile", ("unknown", "", "FULL"))
def test_unknown_profile_raises_with_offending_value(profile: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_sections(profile, None)

    message = str(exc_info.value)
    assert repr(profile) in message
    assert "agent" in message
    assert "compact" in message
    assert "full" in message


@pytest.mark.parametrize(
    "section",
    (
        "unknown_section",
        "repositoryFiles",
        "ExecutiveSummary",
    ),
)
def test_unknown_section_raises_with_offending_value(section: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_sections("full", (section,))

    message = str(exc_info.value)
    assert repr(section) in message
    assert "executive_summary" in message
    assert "health" in message
    assert "runtime" in message


def test_empty_explicit_section_set_raises() -> None:
    with pytest.raises(ValueError, match="resolved section set is empty"):
        resolve_sections("agent", ())


@pytest.mark.parametrize("policy", ("fail", "truncate"))
def test_explicit_overflow_policy_passes_through(policy: str) -> None:
    assert resolve_overflow_policy(policy, "full") == policy
    assert resolve_overflow_policy(policy, "agent") == policy


@pytest.mark.parametrize(
    ("profile", "expected"),
    (
        ("full", "fail"),
        ("agent", "truncate"),
        ("compact", "truncate"),
    ),
)
def test_auto_overflow_policy_depends_on_profile(profile: str, expected: str) -> None:
    assert resolve_overflow_policy("auto", profile) == expected


@pytest.mark.parametrize("policy", ("invalid", "", "FAIL"))
def test_invalid_overflow_policy_raises(policy: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_overflow_policy(policy, "full")

    message = str(exc_info.value)
    assert repr(policy) in message
    assert "auto" in message
    assert "fail" in message
    assert "truncate" in message


def test_mandatory_section_fits_and_is_committed_exactly() -> None:
    handle = _binary_handle()
    writer = BudgetedSectionWriter(handle, max_bytes=5, policy="fail")

    writer.write_mandatory("header", _render_bytes(b"abcde"))

    assert _read_handle(handle) == b"abcde"
    assert writer.manifest.requested == ("header",)
    assert writer.manifest.selected == ("header",)
    assert writer.manifest.rendered == ("header",)
    assert writer.manifest.omitted == ()
    assert writer.manifest.truncated is False
    assert writer.manifest.omitted_bytes == 0


def test_mandatory_overflow_raises_without_committing_staged_bytes() -> None:
    handle = _binary_handle()
    handle.write(b"base")
    writer = BudgetedSectionWriter(handle, max_bytes=7, policy="fail")

    with pytest.raises(
        ValueError,
        match=r"mandatory section 'header' does not fit within the 7 byte budget",
    ):
        writer.write_mandatory("header", _render_bytes(b"toolong"))

    assert _read_handle(handle) == b"base"
    assert writer.manifest.requested == ("header",)
    assert writer.manifest.selected == ("header",)
    assert writer.manifest.rendered == ()
    assert writer.manifest.omitted == ()
    assert writer.manifest.truncated is False


def test_optional_section_commits_when_it_fits() -> None:
    handle = _binary_handle()
    writer = BudgetedSectionWriter(handle, max_bytes=16, policy="truncate")

    result = writer.write_optional("executive_summary", _render_bytes(b"summary"))

    assert result is True
    assert _read_handle(handle) == b"summary"
    assert writer.manifest.rendered == ("executive_summary",)
    assert writer.manifest.omitted == ()
    assert writer.manifest.truncated is False


def test_optional_overflow_records_exact_staged_size_and_commits_nothing() -> None:
    handle = _binary_handle()
    handle.write(b"1234")
    writer = BudgetedSectionWriter(handle, max_bytes=8, policy="truncate")

    result = writer.write_optional("snapshot", _render_bytes(b"12345"))

    assert result is False
    assert _read_handle(handle) == b"1234"
    assert writer.manifest.omitted == (
        OmittedSection(
            section="snapshot",
            reason="output budget exceeded",
            section_bytes=5,
        ),
    )
    assert writer.manifest.omitted_bytes == 5
    assert writer.manifest.truncated is True


def test_mixed_sequence_produces_complete_manifest_in_call_order() -> None:
    handle = _binary_handle()
    writer = BudgetedSectionWriter(handle, max_bytes=12, policy="truncate")

    writer.write_mandatory("header", _render_bytes(b"HH"))
    first_result = writer.write_optional("executive_summary", _render_bytes(b"AAAA"))
    omitted_result = writer.write_optional("snapshot", _render_bytes(b"XXXXXXXX"))
    final_result = writer.write_optional("quick_reference", _render_bytes(b"BBB"))

    assert first_result is True
    assert omitted_result is False
    assert final_result is True
    assert _read_handle(handle) == b"HHAAAABBB"

    manifest = writer.manifest
    assert manifest.requested == (
        "header",
        "executive_summary",
        "snapshot",
        "quick_reference",
    )
    assert manifest.selected == (
        "header",
        "executive_summary",
        "snapshot",
        "quick_reference",
    )
    assert manifest.rendered == (
        "header",
        "executive_summary",
        "quick_reference",
    )
    assert manifest.omitted == (
        OmittedSection(
            section="snapshot",
            reason="output budget exceeded",
            section_bytes=8,
        ),
    )
    assert manifest.truncated is True
    assert manifest.omitted_bytes == 8

    assert manifest.to_json_dict() == {
        "requested": [
            "header",
            "executive_summary",
            "snapshot",
            "quick_reference",
        ],
        "selected": [
            "header",
            "executive_summary",
            "snapshot",
            "quick_reference",
        ],
        "rendered": [
            "header",
            "executive_summary",
            "quick_reference",
        ],
        "omitted": [
            {
                "section": "snapshot",
                "reason": "output budget exceeded",
                "section_bytes": 8,
            }
        ],
        "truncated": True,
    }


def test_large_section_beyond_spool_threshold_is_staged_and_committed_exactly() -> None:
    payload = b"x" * (300 * 1024)
    handle = _binary_handle()
    writer = BudgetedSectionWriter(handle, max_bytes=len(payload), policy="truncate")

    result = writer.write_optional("snapshot", _render_bytes(payload))

    assert result is True
    assert _read_handle(handle) == payload
    assert writer.manifest.rendered == ("snapshot",)
    assert writer.manifest.omitted_bytes == 0


def test_writer_rejects_unresolved_policy_values() -> None:
    with pytest.raises(ValueError, match="unknown resolved overflow policy"):
        BudgetedSectionWriter(_binary_handle(), max_bytes=64, policy="auto")

    with pytest.raises(ValueError, match="unknown resolved overflow policy"):
        BudgetedSectionWriter(_binary_handle(), max_bytes=64, policy="anything")


def test_writer_records_omission_under_fail_policy_caller_enforces_publication() -> None:
    """The writer itself always truncates; fail semantics belong to the caller."""
    handle = _binary_handle()
    writer = BudgetedSectionWriter(handle, max_bytes=4, policy="fail")

    assert writer.write_optional("snapshot", _render_bytes(b"12345")) is False
    assert _read_handle(handle) == b""
    assert writer.manifest.omitted[0].section == "snapshot"
    assert writer.manifest.truncated is True


def test_multibyte_utf8_boundary_commit_on_exact_fit() -> None:
    """UTF-8 multibyte sections commit when encoded bytes land exactly on the budget."""
    payload = "ąśż".encode()
    assert len(payload) == 6
    handle = _binary_handle()
    writer = BudgetedSectionWriter(handle, max_bytes=6, policy="truncate")

    assert writer.write_optional("topology", _render_bytes(payload)) is True
    assert _read_handle(handle) == payload


def test_multibyte_utf8_boundary_omits_one_byte_under_budget() -> None:
    payload = "ąśż".encode()
    handle = _binary_handle()
    writer = BudgetedSectionWriter(handle, max_bytes=5, policy="truncate")

    assert writer.write_optional("topology", _render_bytes(payload)) is False
    assert writer.manifest.omitted[0].section_bytes == 6
    assert _read_handle(handle) == b""


def test_commit_streams_sections_larger_than_copy_chunk() -> None:
    payload = b"y" * (1024 * 1024 + 4096)
    handle = _binary_handle()
    writer = BudgetedSectionWriter(handle, max_bytes=len(payload), policy="truncate")

    assert writer.write_optional("snapshot", _render_bytes(payload)) is True
    assert _read_handle(handle) == payload
    assert writer.manifest.rendered == ("snapshot",)
    assert writer.manifest.omitted_bytes == 0


def test_artifact_digest_matches_hashlib_sha256(tmp_path: Path) -> None:
    content = b"header\nsection\n\x00binary-safe\n"
    artifact = tmp_path / "report.md"
    artifact.write_bytes(content)

    assert artifact_digest(artifact) == hashlib.sha256(content).hexdigest()


def test_artifact_digest_is_stable_for_identical_content(tmp_path: Path) -> None:
    content = b"same artifact bytes"
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_bytes(content)
    second.write_bytes(content)

    first_digest = artifact_digest(first)
    second_digest = artifact_digest(second)

    assert first_digest == second_digest
    assert first_digest == artifact_digest(first)
