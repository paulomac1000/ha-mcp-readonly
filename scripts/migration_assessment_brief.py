#!/usr/bin/env python3
"""Print immutable inputs required for a canonical provider-backed assessment."""

from __future__ import annotations

import argparse
import json
import re

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _revision(value: str) -> str:
    normalized = value.strip().lower()
    if not _SHA_RE.fullmatch(normalized):
        raise argparse.ArgumentTypeError("--revision must be a full 40-character commit SHA")
    return normalized


def _positive_pr(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--pr must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("--pr must be a positive integer")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", required=True, type=_revision)
    parser.add_argument("--pr", type=_positive_pr, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "repository": "paulomac1000/ha-mcp-readonly",
                "revision": args.revision,
                "pull_request": args.pr,
                "required_external_evidence": [
                    "successful exact-head CI jobs and artifact digests",
                    "official mcp==1.29.1 stdio smoke for negotiated 2025-11-25 on the exact wheel",
                    "official mcp==1.29.1 Streamable HTTP smoke for negotiated 2025-11-25 on the exact container",
                    "real Home Assistant smoke, E2E, and integration suites",
                    "independent GitHub APPROVED review bound to the same revision",
                ],
                "unsupported_claims": [
                    "Do not claim MCP 2026-07-28 compatibility for the FastMCP 3.x lane without separate exact-artifact evidence."
                ],
                "ai_skills_revision": "b54fc6b27ea80b36a70d5de73445970e17f55789",
                "decision_before_external_evidence": "request-changes",
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
