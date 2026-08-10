"""Project and capability versions must remain one coherent public contract."""

import json
from pathlib import Path

from version import __version__


def test_every_static_capability_manifest_matches_project_version() -> None:
    manifest_path = Path(__file__).resolve().parents[2] / "tools" / "tool_manifests.json"
    manifests = json.loads(manifest_path.read_text(encoding="utf-8"))
    stale = {
        name: manifest.get("extensions", {}).get("tool_version")
        for name, manifest in manifests.items()
        if manifest.get("extensions", {}).get("tool_version") != __version__
    }
    assert stale == {}
