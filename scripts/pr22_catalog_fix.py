from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"{path}: expected one target")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Capability introspection needs the actual deployment-supported operation names,
# not every declared manifest (which also includes disabled developer tools).
replace_once(
    "tools/operations.py",
    '''    def tool(
        self, *decorator_args: Any, **decorator_kwargs: Any
    ) -> Callable[[Callable[..., Any]], Any]:
''',
    '''    def names(self) -> set[str]:
        """Return names exposed by this deployment's application registry."""
        return self._registry.names()

    def tool(
        self, *decorator_args: Any, **decorator_kwargs: Any
    ) -> Callable[[Callable[..., Any]], Any]:
''',
)
replace_once(
    "tools/capabilities.py",
    '''def _do_describe_ha_capabilities() -> dict[str, Any]:
    """Build supported and active catalogs without contacting external dependencies."""
    supported_manifests = get_all_manifests(active_only=False)
    initialized = active_profile_initialized()
    active_manifests = get_all_manifests(active_only=True) if initialized else {}
    inactive_reasons = get_inactive_reasons() if initialized else {}
    active_names = set(active_manifests)
''',
    '''def _do_describe_ha_capabilities(
    supported_names: set[str] | None = None,
) -> dict[str, Any]:
    """Build supported and active catalogs without contacting external dependencies."""
    declared_manifests = get_all_manifests(active_only=False)
    supported_manifests = (
        declared_manifests
        if supported_names is None
        else {
            name: manifest
            for name, manifest in declared_manifests.items()
            if name in supported_names
        }
    )
    initialized = active_profile_initialized()
    active_manifests = get_all_manifests(active_only=True) if initialized else {}
    inactive_reasons = get_inactive_reasons() if initialized else {}
    active_names = set(active_manifests) & set(supported_manifests)
''',
)
replace_once(
    "tools/capabilities.py",
    '''        try:
            return _success_response(_do_describe_ha_capabilities())
        except Exception as exc:
''',
    '''        try:
            names = getattr(mcp, "names", None)
            supported_names = names() if callable(names) else None
            return _success_response(
                _do_describe_ha_capabilities(supported_names=supported_names)
            )
        except Exception as exc:
''',
)

# Protocol contract: all reported supported items are exactly the MCP-exposed set.
replace_once(
    "tests/protocol/test_mcp_protocol.py",
    '''            assert payload["supported_tool_count"] == expected
            assert len(payload["tools"]) == expected
''',
    '''            assert payload["supported_tool_count"] == expected
            assert len(payload["tools"]) == expected
            assert {item["name"] for item in payload["tools"]} == {tool.name for tool in tools}
''',
)

# The protected publisher intentionally uses one package-scoped promotion credential
# because Docker credentials are keyed by registry hostname. Document the exact scope
# instead of claiming separate read/write logins can coexist for the same ghcr.io host.
replace_once(
    "SECURITY.md",
    '''- Quarantine write credentials and protected release credentials must be distinct and scoped to their respective repositories/environments.
''',
    '''- Candidate quarantine-write credentials and the protected promotion credential must be distinct. Because source and destination both use `ghcr.io`, the protected promotion credential is a single package-scoped credential with **read-only** access to the quarantine package and **write** access to the release package; it must not be exposed to candidate build/test jobs.
''',
)
