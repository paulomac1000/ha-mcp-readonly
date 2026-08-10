#!/usr/bin/env python3
from pathlib import Path

source_path = Path(__file__).with_name("pr22_new_review_fix.py")
source = source_path.read_text(encoding="utf-8")
source = source.replace(
    "def _normalized_key(value: Any) -> str:\\n    return _NORMALIZE_KEY.sub",
    "def _normalized_key(value: object) -> str:\\n    return _NORMALIZE_KEY.sub",
)
source = source.replace(
    "'def _normalized_key(value: Any) -> str:\\n'",
    "'def _normalized_key(value: object) -> str:\\n'",
)
namespace = {"__file__": str(source_path), "__name__": "__main__"}
exec(compile(source, str(source_path), "exec"), namespace)
