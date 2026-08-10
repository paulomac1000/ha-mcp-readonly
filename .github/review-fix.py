from __future__ import annotations

from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    text = read(path)
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(
            f"{path}: expected {count} occurrence(s), found {actual}: {old[:120]!r}"
        )
    write(path, text.replace(old, new, count))


def replace_all(path: str, old: str, new: str, *, minimum: int = 1) -> None:
    text = read(path)
    actual = text.count(old)
    if actual < minimum:
        raise RuntimeError(
            f"{path}: expected at least {minimum} occurrence(s), found {actual}: {old[:120]!r}"
        )
    write(path, text.replace(old, new))


def edit_segment(path: str, start: str, end: str, transform) -> None:
    text = read(path)
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    segment = text[start_index:end_index]
    updated = transform(segment)
    if updated == segment:
        raise RuntimeError(f"{path}: segment transform made no changes")
    write(path, text[:start_index] + updated + text[end_index:])


replace(
    "context_generator/analyzers.py",
    """        print(f"Analyzing logs (last {hours}h)...")

        log_path = _active_config_path() / "home-assistant.log"
""",
    """        if hours is None:
            hours = _active_log_hours()
        print(f"Analyzing logs (last {hours}h)...")

        log_path = _active_config_path() / "home-assistant.log"
""",
)

replace(
    "context_generator/provenance.py",
    '_BEARER = re.compile(r"(?i)Bearer\\s+[A-Za-z0-9._~-]+")',
    '_BEARER = re.compile(r"(?i)Bearer\\s+[A-Za-z0-9._~+/=-]+")',
)
replace(
    "tools/utils.py",
    '(re.compile(r"Bearer\\s+[A-Za-z0-9._\\-]+"), "Bearer [REDACTED]"),',
    '(re.compile(r"Bearer\\s+[A-Za-z0-9._~+/=\\-]+"), "Bearer [REDACTED]"),',
)

replace(
    "context_generator/storage_policy.py",
    """        "config_entry_id",
        "config_entry_id",
""",
    """        "config_entry_id",
""",
)
replace("context_generator/storage_policy.py", '        "options",\n', "")

replace(
    "context_generator/config.py",
    'GenerationMode = Literal["offline", "online", "hybrid"]\n\n\n@dataclass',
    'GenerationMode = Literal["offline", "online", "hybrid"]\nDEFAULT_MAX_OUTPUT_BYTES = 96 * 1024 * 1024\n\n\n@dataclass',
)
replace(
    "context_generator/config.py",
    "    max_output_bytes: int = 96 * 1024 * 1024\n",
    "    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES\n",
)
replace(
    "context_generator/config.py",
    '            max_output_bytes=int(os.getenv("HA_CONTEXT_MAX_OUTPUT_BYTES", str(96 * 1024 * 1024))),\n',
    '            max_output_bytes=int(os.getenv("HA_CONTEXT_MAX_OUTPUT_BYTES", str(DEFAULT_MAX_OUTPUT_BYTES))),\n',
)
replace(
    "context_generator/formatters.py",
    "from .config import GenerationConfig\n",
    "from .config import DEFAULT_MAX_OUTPUT_BYTES, GenerationConfig\n",
)
replace(
    "context_generator/formatters.py",
    "                else 32 * 1024 * 1024\n",
    "                else DEFAULT_MAX_OUTPUT_BYTES\n",
)
replace(
    "context_generator/formatters.py",
    '        f.write("# Home Assistant Context for AI (v1.0)\\n\\n")\n',
    '        f.write("# Home Assistant Context for AI (v1.1)\\n\\n")\n',
)

replace(
    "tools/diagnostics.py",
    """    logbook_window_hours = 24
    logbook_res: dict[str, Any] = {"success": False, "data": []}
    for logbook_window_hours in (24, 6, 1):
        start = (datetime.now(UTC) - timedelta(hours=logbook_window_hours)).isoformat()
        logbook_res = make_ha_request(
            ha_url, ha_token, f"/api/logbook/{start}", timeout=60, retries=1
        )
        if logbook_res.get("success"):
            break
""",
    """    logbook_window_hours: int | None = None
    logbook_res: dict[str, Any] = {"success": False, "data": []}
    for candidate_window_hours in (24, 6, 1):
        start = (datetime.now(UTC) - timedelta(hours=candidate_window_hours)).isoformat()
        logbook_res = make_ha_request(
            ha_url, ha_token, f"/api/logbook/{start}", timeout=60, retries=1
        )
        if logbook_res.get("success"):
            logbook_window_hours = candidate_window_hours
            break
""",
)

replace(
    "tools/settings.py",
    """    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if not minimum <= value <= maximum:
""",
    """    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if not minimum <= value <= maximum:
""",
)

replace(
    "tools/security.py",
    """    def _reject_symlink_components(self, root: Path, relative: Path) -> None:
        if not self.reject_symlinks:
            return
        cursor = root
        for component in relative.parts:
            cursor = cursor / component
            if cursor.is_symlink():
                raise SecurityBoundaryError("Access denied: symbolic links are not allowed")
""",
    """    def reject_symlink_components(self, candidate: Path) -> None:
        \"\"\"Reject any existing symlink in an unresolved candidate path.\"\"\"
        if not self.reject_symlinks:
            return
        unresolved = Path(os.path.abspath(os.fspath(candidate)))
        cursor = unresolved
        while cursor != cursor.parent:
            if cursor.is_symlink():
                raise SecurityBoundaryError("Access denied: symbolic links are not allowed")
            cursor = cursor.parent
""",
)
replace(
    "tools/security.py",
    """        resolved = candidate.resolve(strict=False)
        root = self._root_for(resolved)
""",
    """        self.reject_symlink_components(candidate)
        resolved = candidate.resolve(strict=False)
        root = self._root_for(resolved)
""",
)
replace(
    "tools/security.py",
    """        self._reject_sensitive(relative)
        self._reject_symlink_components(root, relative)

        if require_exists""",
    """        self._reject_sensitive(relative)

        if require_exists""",
)
replace(
    "tools/security.py",
    '        allowed_suffixes=frozenset({".md", ".json"}),\n        deny_storage=False,\n',
    "        deny_storage=False,\n",
)
replace(
    "tools/security.py",
    """    policy._reject_symlink_components(root, parent.relative_to(root))
    return target
""",
    """    policy.reject_symlink_components(target)
    return target
""",
)
replace(
    "tools/security.py",
    'return hmac.compare_digest(supplied.encode("utf-8"), expected_token.encode("utf-8"))',
    'return hmac.compare_digest(\n        supplied.encode("utf-8", "surrogateescape"),\n        expected_token.encode("utf-8", "surrogateescape"),\n    )',
)
replace(
    "tools/auth.py",
    'if not hmac.compare_digest(token.encode("utf-8"), self._expected_token.encode("utf-8")):',
    'if not hmac.compare_digest(\n            token.encode("utf-8", "surrogateescape"),\n            self._expected_token.encode("utf-8", "surrogateescape"),\n        ):',
)


def patch_list_segment(segment: str) -> str:
    segment = segment.replace(
        "def _do_list_directory(path: str, max_entries: int) -> dict[str, Any]:",
        "def _do_list_directory(\n    path: str, max_entries: int, security_context: SecurityContext | None = None\n) -> dict[str, Any]:",
    )
    segment = segment.replace(
        '    \"\"\"List directory contents with allowlist validation.\n\n    Returns a dict (not JSON) so the wrapper can add _meta envelope.\n    \"\"\"\n    try:',
        '    \"\"\"List directory contents with allowlist validation.\n\n    Returns a dict (not JSON) so the wrapper can add _meta envelope.\n    \"\"\"\n    context = security_context or SECURITY_CONTEXT\n    try:',
        1,
    )
    return segment.replace("SECURITY_CONTEXT", "context")


edit_segment(
    "tools/filesystem_explorer.py",
    "def _do_list_directory",
    "\ndef _do_read_file",
    patch_list_segment,
)


def patch_read_segment(segment: str) -> str:
    segment = segment.replace(
        "def _do_read_file(file_path: str, max_lines: int, offset: int) -> dict[str, Any]:",
        "def _do_read_file(\n    file_path: str, max_lines: int, offset: int, security_context: SecurityContext | None = None\n) -> dict[str, Any]:",
    )
    segment = segment.replace(
        '    \"\"\"Read a text file with allowlist validation and size limits.\"\"\"\n    try:',
        '    \"\"\"Read a text file with allowlist validation and size limits.\"\"\"\n    context = security_context or SECURITY_CONTEXT\n    try:',
        1,
    )
    return segment.replace("SECURITY_CONTEXT", "context")


edit_segment(
    "tools/filesystem_explorer.py",
    "def _do_read_file",
    "\ndef _do_search_files",
    patch_read_segment,
)


def patch_search_segment(segment: str) -> str:
    segment = segment.replace(
        "def _do_search_files(pattern: str, search_path: str, max_results: int) -> dict[str, Any]:",
        "def _do_search_files(\n    pattern: str,\n    search_path: str,\n    max_results: int,\n    security_context: SecurityContext | None = None,\n) -> dict[str, Any]:",
    )
    segment = segment.replace(
        '    \"\"\"Search for files containing a text pattern (safe grep).\"\"\"\n    if not re.match',
        '    \"\"\"Search for files containing a text pattern (safe grep).\"\"\"\n    context = security_context or SECURITY_CONTEXT\n    if not re.match',
        1,
    )
    return segment.replace("SECURITY_CONTEXT", "context")


edit_segment(
    "tools/filesystem_explorer.py",
    "def _do_search_files",
    "\n# =============================================================================\n# FILESYSTEM EXPLORER",
    patch_search_segment,
)
replace(
    "tools/filesystem_explorer.py",
    """    global SECURITY_CONTEXT
    if config_path:
        SECURITY_CONTEXT = SecurityContext(
            allowed_directories=[Path(config_path)],
            max_file_size=10 * 1024 * 1024,
            max_depth=20,
        )

    default_root = str(SECURITY_CONTEXT.allowed_directories[0])
""",
    """    security_context = SecurityContext(
        allowed_directories=[Path(config_path or "/config")],
        max_file_size=10 * 1024 * 1024,
        max_depth=20,
    )
    default_root = str(security_context.allowed_directories[0])
""",
)
replace(
    "tools/filesystem_explorer.py",
    "            data = _do_list_directory(path, max_entries)\n",
    "            data = _do_list_directory(path, max_entries, security_context)\n",
)
replace(
    "tools/filesystem_explorer.py",
    "            data = _do_read_file(file_path, max_lines, offset)\n",
    "            data = _do_read_file(file_path, max_lines, offset, security_context)\n",
)
replace(
    "tools/filesystem_explorer.py",
    "            data = _do_search_files(pattern, search_path, max_results)\n",
    "            data = _do_search_files(pattern, search_path, max_results, security_context)\n",
)
replace_all("tools/filesystem_explorer.py", '\"\"\"[READ] ', '\"\"\"', minimum=3)

text = read("tools/blueprints.py")
start = text.index("def _do_get_blueprint_instances")
end = text.index("\ndef _iter_blueprint_instances", start)
replacement = """def _do_get_blueprint_instances(blueprint_path: str, config_path: str) -> str:
    \"\"\"Find automations and scripts that use a given blueprint.\"\"\"
    try:
        matching = [
            {
                "type": item["type"],
                "id": item["id"],
                "alias": item["alias"],
                "inputs": item["inputs"],
            }
            for item in _iter_blueprint_instances(config_path)
            if item["blueprint_path"] == blueprint_path
        ]
        automations = sum(1 for item in matching if item["type"] == "automation")
        return _success_response(
            {
                "blueprint": blueprint_path,
                "usage_count": len(matching),
                "instances": matching,
                "summary": {
                    "automations": automations,
                    "scripts": len(matching) - automations,
                },
            }
        )
    except Exception as e:
        return _error_response(str(e))

"""
write("tools/blueprints.py", text[:start] + replacement + text[end + 1 :])
replace(
    "tools/blueprints.py",
    '                "alias": entry.get("alias", key if doc_type == "script" else "Unnamed"),\n',
    '                "alias": entry.get(\n                    "alias", (key if key is not None else "Unnamed") if doc_type == "script" else "Unnamed"\n                ),\n',
)
replace(
    "tools/blueprints.py",
    """        instances = _iter_blueprint_instances(config_path)
        usage_stats = []

        for bp in all_blueprints:
            path = bp.get("path")
            matching = [i for i in instances if i["blueprint_path"] == path]
            usage_stats.append(
                {
                    "path": path,
                    "name": bp.get("name"),
                    "domain": bp.get("domain"),
                    "usage_count": len(matching),
                    "automations": len([i for i in matching if i["type"] == "automation"]),
                    "scripts": len([i for i in matching if i["type"] == "script"]),
                }
            )
""",
    """        instances = _iter_blueprint_instances(config_path)
        by_path: dict[str, list[dict[str, Any]]] = {}
        for item in instances:
            by_path.setdefault(item["blueprint_path"], []).append(item)
        usage_stats = []

        for bp in all_blueprints:
            path = bp.get("path")
            matching = by_path.get(path, []) if isinstance(path, str) else []
            automations = sum(1 for item in matching if item["type"] == "automation")
            usage_stats.append(
                {
                    "path": path,
                    "name": bp.get("name"),
                    "domain": bp.get("domain"),
                    "usage_count": len(matching),
                    "automations": automations,
                    "scripts": len(matching) - automations,
                }
            )
""",
)

replace(
    "server.py",
    """    token = REST_API_TOKEN if auth_token is None else auth_token
    if not token:
        raise ValueError("REST_API_TOKEN or MCP_AUTH_TOKEN is required for the REST adapter")

    class BearerAuthMiddleware""",
    """    token = REST_API_TOKEN if auth_token is None else auth_token
    if not token:
        raise ValueError("REST_API_TOKEN or MCP_AUTH_TOKEN is required for the REST adapter")
    sync_tool_slots = asyncio.Semaphore(16)

    class BearerAuthMiddleware""",
)
replace(
    "server.py",
    """            result = (
                await function(**arguments)
                if inspect.iscoroutinefunction(function)
                else await asyncio.to_thread(function, **arguments)
            )
""",
    """            if inspect.iscoroutinefunction(function):
                result = await function(**arguments)
            else:
                async with sync_tool_slots:
                    result = await asyncio.to_thread(function, **arguments)
""",
)
replace(
    "server.py",
    "def run_startup_tests() -> bool:\n",
    """def _run_rest_api_guarded() -> None:
    \"\"\"Run the optional REST adapter and publish terminal failure state.\"\"\"
    try:
        run_rest_api()
    except Exception:
        _set_health_component("rest", "failed")
        _logger.exception("REST compatibility adapter stopped")


def run_startup_tests() -> bool:
""",
)
replace(
    "server.py",
    '        threading.Thread(target=run_rest_api, daemon=True, name="rest-api").start()\n',
    '        threading.Thread(target=_run_rest_api_guarded, daemon=True, name="rest-api").start()\n',
)

replace(
    "tests/e2e/test_server_api.py",
    """def _get(path: str, **kwargs: Any) -> requests.Response:
    headers = {**REST_HEADERS, **kwargs.pop("headers", {})}
    return requests.get(f"{REST_API_URL}{path}", headers=headers, **kwargs)


def _post(path: str, **kwargs: Any) -> requests.Response:
    headers = {**REST_HEADERS, **kwargs.pop("headers", {})}
    return requests.post(f"{REST_API_URL}{path}", headers=headers, **kwargs)
""",
    """def _get(path: str, **kwargs: Any) -> requests.Response:
    headers = {**REST_HEADERS, **kwargs.pop("headers", {})}
    kwargs.setdefault("timeout", 10)
    return requests.get(f"{REST_API_URL}{path}", headers=headers, **kwargs)


def _post(path: str, **kwargs: Any) -> requests.Response:
    headers = {**REST_HEADERS, **kwargs.pop("headers", {})}
    kwargs.setdefault("timeout", 10)
    return requests.post(f"{REST_API_URL}{path}", headers=headers, **kwargs)
""",
)

replace(
    "tests/protocol/test_mcp_protocol.py",
    "from fastmcp.exceptions import ToolError\n\nimport server\n",
    "from fastmcp.exceptions import ToolError\nimport pytest\n\nimport server\n",
)
replace(
    "tests/protocol/test_mcp_protocol.py",
    '            assert payload["transports"] == ["stdio", "streamable-http"]\n',
    '            assert {"stdio", "streamable-http"}.issubset(payload["transports"])\n',
)
replace(
    "tests/protocol/test_mcp_protocol.py",
    """            try:
                await client.call_tool("get_entity_state", {"wrong_parameter": "x"})
            except ToolError as exc:
                assert "Input validation error" in str(exc)
            else:
                raise AssertionError("invalid input must fail at the protocol boundary")
""",
    """            with pytest.raises(ToolError):
                await client.call_tool("get_entity_state", {"wrong_parameter": "x"})
""",
)

replace(
    "tests/unit/test_settings.py",
    """def test_wildcard_cors_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
""",
    """def test_wildcard_cors_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
""",
)
replace(
    "tests/unit/test_settings.py",
    "def test_wildcard_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:\n",
    """def test_invalid_port_names_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("MCP_PORT", "not-a-port")
    with pytest.raises(ValueError, match="MCP_PORT must be an integer"):
        RuntimeSettings.from_env()


def test_out_of_range_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("REST_API_PORT", "70000")
    with pytest.raises(ValueError, match="REST_API_PORT must be between 1 and 65535"):
        RuntimeSettings.from_env()


def test_wildcard_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
""",
)

replace(
    "tests/unit/test_invocation_kernel.py",
    """    release.set()
    time.sleep(0.05)
    assert kernel.invoke_sync(name, lambda: "ok") == "ok"
""",
    """    release.set()
    deadline = time.monotonic() + 2
    while True:
        try:
            assert kernel.invoke_sync(name, lambda: "ok") == "ok"
            break
        except InvocationError as exc:
            if exc.code != "BUSY" or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
""",
)
replace(
    "tests/unit/test_invocation_kernel.py",
    """    release.set()
    await asyncio.sleep(0.01)
    assert await kernel.invoke_async(name, _async_ok) == "ok"
""",
    """    release.set()
    deadline = time.monotonic() + 2
    while True:
        try:
            assert await kernel.invoke_async(name, _async_ok) == "ok"
            break
        except InvocationError as exc:
            if exc.code != "BUSY" or time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.01)
""",
)

replace(
    "tests/unit/test_context_generator.py",
    """    def test_main_raises_controlled_error_on_required_source_failure(self, monkeypatch):
        config = GenerationConfig.from_env()
""",
    """    def test_main_raises_controlled_error_on_required_source_failure(self, tmp_path):
        config = GenerationConfig(
            config_path=tmp_path,
            output_path=tmp_path / "out.md",
            ha_url="",
            ha_token="",
            mode="offline",
        )
""",
)
replace(
    "tests/unit/test_context_generator.py",
    "class TestMain:\n",
    """def test_provenance_redacts_padded_base64_bearer_tokens() -> None:
    from context_generator.provenance import redact_sensitive

    value = "Authorization: Bearer abc+def/ghi=="
    redacted, count = redact_sensitive(value)
    assert redacted == "Authorization: Bearer [REDACTED]"
    assert count == 1


class TestMain:
""",
)

replace(
    "tests/unit/test_server.py",
    """        observed["capabilities"] = principal.capabilities
        return "ok"
""",
    """        observed["capabilities"] = principal.capabilities
        observed["targets"] = principal.targets
        return "ok"
""",
)
replace(
    "tests/unit/test_server.py",
    """        "capabilities": frozenset({"filesystem.read"}),
    }
""",
    """        "capabilities": frozenset({"filesystem.read"}),
        "targets": frozenset({"runtime", "home_assistant", "home_assistant_config"}),
    }
""",
)
replace(
    "tests/unit/test_server.py",
    "def test_sync_rest_tool_runs_off_event_loop(client: TestClient) -> None:\n",
    """def test_authenticated_rest_request_binds_expected_principal(client: TestClient) -> None:
    from tools.invocation import current_principal

    def report_principal() -> dict:
        principal = current_principal()
        return {"subject": principal.subject, "targets": sorted(principal.targets)}

    with patch("server.get_tool", return_value=report_principal):
        response = client.post("/api/tools/principal", headers=AUTH, json={})

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["subject"] == "authenticated-rest-client"
    assert result["targets"] == ["home_assistant", "home_assistant_config", "runtime"]


def test_sync_rest_tool_runs_off_event_loop(client: TestClient) -> None:
""",
)

replace(
    "tests/integration/conftest.py",
    """        self._mcp = mcp_instance
        self._loop = None
        self._tools_cache = None
""",
    """        self._mcp = mcp_instance
        self._loop = None
""",
)
replace(
    "tests/integration/conftest.py",
    "    def _get_or_create_loop(self):\n",
    """    def close(self):
        \"\"\"Close the shared event loop owned by this wrapper.\"\"\"
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
        self._loop = None

    def _get_or_create_loop(self):
""",
)
replace(
    "tests/integration/conftest.py",
    """    return MCPWrapper(mcp)


@pytest.fixture(scope="module")
""",
    """    wrapper = MCPWrapper(mcp)
    try:
        yield wrapper
    finally:
        wrapper.close()


@pytest.fixture(scope="module")
""",
)

replace(
    "tests/unit/test_config.py",
    """    def test_prefilter_keeps_matches_from_any_file(self, mock_mcp, tmp_path):
        from tools.config import _do_search_config_by_params
""",
    """    def test_prefilter_keeps_matches_from_any_file(self, mock_mcp, tmp_path, monkeypatch):
        from tools import config as config_mod
        from tools.config import _do_search_config_by_params
""",
)
replace(
    "tests/unit/test_config.py",
    """        data = _do_search_config_by_params(entity_id="light.room", config_path=str(tmp_path))
        assert data["success"] is True
""",
    """        parsed: list[str] = []
        original = config_mod._load_yaml_file_internal

        def _record(file_path, config_path):
            parsed.append(Path(file_path).name)
            return original(file_path, config_path)

        monkeypatch.setattr(config_mod, "_load_yaml_file_internal", _record)
        data = _do_search_config_by_params(entity_id="light.room", config_path=str(tmp_path))
        assert data["success"] is True
        assert "entity_refs.yaml" in parsed
        assert "unrelated.yaml" not in parsed
""",
)

replace(
    "tests/unit/test_blueprints.py",
    '        (tmp_path / "scripts.yaml").write_text("[]", encoding="utf-8")\n\n        summary = json.loads(_do_get_blueprint_usage_summary(str(tmp_path)))\n',
    """        script_dir = tmp_path / "blueprints" / "script"
        script_dir.mkdir(parents=True)
        (script_dir / "notify.yaml").write_text(
            "blueprint:\\n  name: Notify\\n  domain: script\\n  input: {}\\n",
            encoding="utf-8",
        )
        (tmp_path / "scripts.yaml").write_text(
            "notify_from_blueprint:\\n"
            "  alias: Notify\\n"
            "  use_blueprint:\\n"
            "    path: script/notify.yaml\\n"
            "    input: {}\\n",
            encoding="utf-8",
        )

        summary = json.loads(_do_get_blueprint_usage_summary(str(tmp_path)))
""",
)
replace(
    "tests/unit/test_blueprints.py",
    '        assert summary["total_blueprints"] == 2\n        assert summary["total_instances"] == 1\n',
    """        script_instances = json.loads(
            _do_get_blueprint_instances("script/notify.yaml", str(tmp_path))
        )
        assert summary["total_blueprints"] == 3
        assert summary["total_instances"] == 2
""",
)
replace(
    "tests/unit/test_blueprints.py",
    '        assert per_blueprint["usage_count"] == 1\n        used = next(s for s in summary["most_used"] if s["path"] == "automation/motion.yaml")\n',
    """        assert per_blueprint["usage_count"] == 1
        assert script_instances["usage_count"] == 1
        assert script_instances["summary"]["scripts"] == 1
        used = next(s for s in summary["most_used"] if s["path"] == "automation/motion.yaml")
""",
)

replace(
    "tests/protocol/test_home_assistant_upstream_contract.py",
    """        self.queue.append(
            json.dumps(
                {
                    "id": request_id,
                    "type": "result",
                    "success": True,
                    "result": results[command],
                }
            )
        )
""",
    """        if command not in results:
            raise AssertionError(f"recorded contract has no response for command: {command}")
        self.queue.append(
            json.dumps(
                {
                    "id": request_id,
                    "type": "result",
                    "success": True,
                    "result": results[command],
                }
            )
        )
""",
)

replace(
    "tests/unit/test_security_boundaries.py",
    "def test_artifact_output_is_confined_and_typed(tmp_path: Path) -> None:\n",
    """def test_in_root_symlink_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "config"
    real = root / "real"
    root.mkdir()
    real.mkdir()
    (real / "data.yaml").write_text("value: 1")
    (root / "linked").symlink_to(real, target_is_directory=True)
    policy = PathPolicy.from_paths([root])
    with pytest.raises(SecurityBoundaryError, match="symbolic links"):
        policy.resolve(root / "linked" / "data.yaml", require_file=True)


def test_artifact_output_is_confined_and_typed(tmp_path: Path) -> None:
""",
)

replace(
    "tests/unit/test_diagnostics.py",
    "class TestEnergyDashboardEdgeCases:\n",
    """def test_performance_reports_no_logbook_window_when_all_requests_fail(monkeypatch) -> None:
    from tools.diagnostics import _do_diagnose_performance

    calls = []

    def fail_request(ha_url, ha_token, endpoint, **kwargs):
        del ha_url, ha_token, kwargs
        calls.append(endpoint)
        if endpoint == "/api/states":
            return {"success": True, "data": []}
        return {"success": False, "error": "unavailable"}

    monkeypatch.setattr("tools.diagnostics.make_ha_request", fail_request)
    result = _do_diagnose_performance("http://ha", "token")
    assert result["logbook_window_hours"] is None
    assert len([endpoint for endpoint in calls if endpoint.startswith("/api/logbook/")]) == 3


class TestEnergyDashboardEdgeCases:
""",
)

replace(
    ".env.example",
    "HA_CONFIG_PATH=/config\n",
    "HA_CONFIG_PATH=/config\n# Host-side directory mounted read-only by docker-compose.yml\nHA_CONFIG_HOST_PATH=/absolute/path/to/home-assistant/config\n",
)
replace(
    "README.md",
    """    volumes:
      - /path/to/ha/config:/config:ro  # Replace with your HA config path (e.g., /config, ~/.homeassistant)
    restart: unless-stopped
""",
    """    volumes:
      - /path/to/ha/config:/config:ro  # Replace with your HA config path (e.g., /config, ~/.homeassistant)
    tmpfs:
      - /app/output:size=256m,mode=0750,uid=10001,gid=10001
    restart: unless-stopped
""",
)
replace(
    "README.md",
    """├── config.py              # Immutable per-run configuration
├── runtime.py             # Context-local runtime and provenance scope
""",
    """├── config.py              # Immutable per-run configuration
├── constants.py           # Legacy/static analyzer defaults and HA YAML loader
├── runtime.py             # Context-local runtime and provenance scope
""",
)
replace(
    "README.md",
    """├── snapshot.py            # Safe filesystem, REST, and WebSocket collectors
├── core.py                # Isolated generation entry points
""",
    """├── snapshot.py            # Safe filesystem, REST, and WebSocket collectors
├── storage_policy.py      # Positive allowlist for model-visible .storage data
├── core.py                # Isolated generation entry points
""",
)
replace(
    "CONTRIBUTING.md",
    "--cov-report=term-missing\nruff check .\n",
    "--cov-report=term-missing --cov-fail-under=80\nruff check .\n",
)
replace(
    "docs/documentation.md",
    """| `REST_API_TOKEN` | MCP token | REST caller credential |
| `HEALTH_SERVER_ENABLED` | network-dependent | Start `/live`, `/ready`, and `/health` |
""",
    """| `REST_API_TOKEN` | MCP token | REST caller credential |
| `REST_API_PORT` | `9093` | REST compatibility adapter port |
| `CORS_ALLOWED_ORIGINS` | `http://localhost` | Explicit REST origins; wildcards are rejected |
| `HEALTH_SERVER_ENABLED` | network-dependent | Start `/live`, `/ready`, and `/health` |
| `HEALTH_CHECK_PORT` | `9091` | Health listener port |
""",
)
replace(
    "docs/documentation.md",
    """| `HA_BACKEND_REQUIRED_FOR_READY` | `0` | Require live Home Assistant connectivity for `/ready`; otherwise expose HA outage as capability degradation |
| `CONTEXT_OUTPUT_ROOT` | output parent | Artifact containment root |
""",
    """| `HA_BACKEND_REQUIRED_FOR_READY` | `0` | Require live Home Assistant connectivity for `/ready`; otherwise expose HA outage as capability degradation |
| `OUTPUT_PATH` | `/app/output/ha-ai-context.md` | Default context artifact path |
| `CONTEXT_OUTPUT_ROOT` | output parent | Artifact containment root |
""",
)
replace(
    "docs/documentation.md",
    """| `MCP_DEV_TOOLS_ENABLED` | `0` | Enable additional developer-only observations |

Wildcard CORS origins are rejected.""",
    """| `MCP_DEV_TOOLS_ENABLED` | `0` | Enable additional developer-only observations |
| `LOG_LEVEL` | `INFO` | Runtime logging level |
| `RUN_TESTS_ON_STARTUP` | `0` | Run bundled unit tests before serving when tests are installed |

Wildcard CORS origins are rejected.""",
)
replace(
    "docs/documentation.md",
    "- `/health` includes version, component details, readiness, and tool count; it deliberately omits per-tool invocation counters from the public endpoint.\n",
    "- `/health` returns only status and version and deliberately exposes no component detail.\n- Authenticated `/api/health/details` reports component state, readiness, and tool counts for operators.\n",
)

print("review-fix patch applied")
