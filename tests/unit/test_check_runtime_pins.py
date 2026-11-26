"""Verify the constrained server environment check without reading host state."""

from unittest.mock import patch

import pytest

from scripts.check_runtime_pins import verify_runtime_pins


@pytest.mark.parametrize(
    ("constraints", "expected"),
    [
        ("FastMCP==3.4.7 # server\nmcp==1.29.1\nstarlette==1.6.0", []),
        ("fastmcp==3.4.7\nmcp==1.29.1", ["Missing exact pin for starlette"]),
        (
            "fastmcp==3.4.6\nmcp==1.29.1\nstarlette==1.6.0",
            ["fastmcp installed 3.4.7, pinned 3.4.6"],
        ),
        (
            "fastmcp==3.4.7\nfastmcp==3.4.7\nmcp==1.29.1\nstarlette==1.6.0",
            ["Duplicate exact pin for fastmcp"],
        ),
    ],
)
def test_verify_runtime_pins(constraints: str, expected: list[str]) -> None:
    installed = {"fastmcp": "3.4.7", "mcp": "1.29.1", "starlette": "1.6.0"}
    with patch("scripts.check_runtime_pins.version", side_effect=installed.__getitem__):
        assert verify_runtime_pins(constraints) == expected
