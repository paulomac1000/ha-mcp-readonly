"""Contract tests for configured context artifact sizes."""

from pathlib import Path

import pytest

from context_generator.config import (
    DEFAULT_MAX_OUTPUT_BYTES,
    MAX_CONTEXT_ARTIFACT_BYTES,
    GenerationConfig,
)


def _config(max_output_bytes: int) -> GenerationConfig:
    return GenerationConfig(
        config_path=Path("config"),
        output_path=Path("output/context.md"),
        ha_url="",
        ha_token="",
        max_output_bytes=max_output_bytes,
    )


def test_default_context_output_limit_remains_96_mib() -> None:
    assert DEFAULT_MAX_OUTPUT_BYTES == 96 * 1024 * 1024
    assert _config(DEFAULT_MAX_OUTPUT_BYTES).max_output_bytes == DEFAULT_MAX_OUTPUT_BYTES


def test_context_output_limit_accepts_shared_maximum() -> None:
    assert _config(MAX_CONTEXT_ARTIFACT_BYTES).max_output_bytes == MAX_CONTEXT_ARTIFACT_BYTES


def test_context_output_limit_rejects_one_byte_above_shared_maximum() -> None:
    with pytest.raises(ValueError, match="exceeds the supported artifact size"):
        _config(MAX_CONTEXT_ARTIFACT_BYTES + 1)
