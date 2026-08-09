#!/usr/bin/env python3
"""Print the immutable inputs required for a canonical provider-backed assessment."""

from __future__ import annotations

import argparse
import json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", required=True)
    parser.add_argument("--pr", type=int, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "repository": "paulomac1000/ha-mcp-readonly",
                "revision": args.revision,
                "pull_request": args.pr,
                "required_external_evidence": [
                    "successful exact-head CI jobs and artifact digests",
                    "official mcp==2.0.0 stdio smoke of the exact wheel",
                    "official mcp==2.0.0 Streamable HTTP smoke of the exact container",
                    "real Home Assistant smoke, E2E, and integration suites",
                    "independent GitHub APPROVED review bound to the same revision",
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
