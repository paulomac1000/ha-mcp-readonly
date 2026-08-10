#!/usr/bin/env python3
"""Temporary deterministic patcher for the latest PR #22 review findings."""

from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    """Replace exactly one expected source fragment or fail closed."""
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def apply() -> None:
    """Apply the two non-workflow review fixes."""
    replace_once(
        Path("tests/protocol/test_home_assistant_upstream_contract.py"),
        'Path(__file__).with_name("cassettes").joinpath("recorded_ha_upstream.json").read_text()',
        'Path(__file__).with_name("cassettes").joinpath("recorded_ha_upstream.json").read_text(encoding="utf-8")',
    )

    replace_once(
        Path("tools/filesystem_explorer.py"),
        '''            if re.search(re.escape(pattern), content, re.IGNORECASE):
                matches: list[dict[str, Any]] = []
                for match in re.finditer(re.escape(pattern), content, re.IGNORECASE):
                    match_start = max(0, match.start() - 30)
                    match_end = min(len(content), match.end() + 30)
                    match_context = content[match_start:match_end].replace("\\n", " ").strip()
                    matches.append({"position": match.start(), "context": match_context})
                    if len(matches) >= 3:
                        break
                results.append(
                    {
                        "path": relative_path.as_posix(),
                        "absolute_path": str(filepath),
                        "matches_count": len(
                            list(re.finditer(re.escape(pattern), content, re.IGNORECASE))
                        ),
                        "sample_matches": matches[:3],
                    }
                )
''',
        '''            matches: list[dict[str, Any]] = []
            matches_count = 0
            for match in re.finditer(re.escape(pattern), content, re.IGNORECASE):
                matches_count += 1
                if len(matches) >= 3:
                    continue
                match_start = max(0, match.start() - 30)
                match_end = min(len(content), match.end() + 30)
                match_context = content[match_start:match_end].replace("\\n", " ").strip()
                matches.append({"position": match.start(), "context": match_context})
            if matches_count:
                results.append(
                    {
                        "path": relative_path.as_posix(),
                        "absolute_path": str(filepath),
                        "matches_count": matches_count,
                        "sample_matches": matches,
                    }
                )
''',
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        apply()


if __name__ == "__main__":
    main()
