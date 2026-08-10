"""Shared helpers for tests that register synthetic operation manifests."""

from tools.manifests import (
    get_all_manifests,
    make_manifest,
    register_manifest,
    set_active_tools,
)


def register_test_manifest(name: str, **updates: object) -> None:
    manifest = make_manifest(name)
    manifest.update(updates)
    active = set(get_all_manifests(active_only=True))
    register_manifest(name, manifest)
    set_active_tools(active | {name})
