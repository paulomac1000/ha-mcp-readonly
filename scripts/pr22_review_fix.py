from __future__ import annotations

import re
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path}: expected one replacement target, found {count}: {old[:100]!r}"
        )
    write(path, text.replace(old, new, 1))


def replace_between(path: str, start: str, end: str, replacement: str) -> None:
    text = read(path)
    left = text.find(start)
    if left < 0:
        raise SystemExit(f"{path}: start marker not found: {start!r}")
    right = text.find(end, left + len(start))
    if right < 0:
        raise SystemExit(f"{path}: end marker not found: {end!r}")
    write(path, text[:left] + replacement + text[right:])


# ai-skills document frontmatter uses singular `owner`.
for doc in (
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "README.md",
    "SECURITY.md",
    "docs/documentation.md",
    "docs/testing-guidelines.md",
):
    text = read(doc)
    updated, count = re.subn(r"(?m)^owners:", "owner:", text, count=1)
    if count != 1:
        raise SystemExit(f"{doc}: expected one owners frontmatter key, found {count}")
    write(doc, updated)

# Auth should be the reason an anonymous MCP request is rejected.
replace_once(
    "tests/e2e/test_server_api.py",
    '''    def test_mcp_endpoint_rejects_anonymous_requests(self):
        mcp_port = int(os.getenv("MCP_PORT", "9092"))
        response = requests.get(f"http://localhost:{mcp_port}/mcp", timeout=5)
        assert response.status_code in (401, 403)
''',
    '''    def test_mcp_endpoint_rejects_anonymous_requests(self):
        mcp_port = int(os.getenv("MCP_PORT", "9092"))
        response = requests.post(
            f"http://localhost:{mcp_port}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "anonymous-auth-probe", "version": "1"},
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            timeout=5,
        )
        assert response.status_code in (401, 403)
''',
)

# Restore all process-global manifest state after each test.
replace_once(
    "tests/unit/test_manifests.py",
    "import asyncio\nimport json\n",
    "import asyncio\nimport copy\nimport json\n",
)
replace_once(
    "tests/unit/test_manifests.py",
    "import pytest\n\nfrom tools.invocation",
    "import pytest\n\nimport tools.manifests as manifests_module\nfrom tools.invocation",
)
replace_once(
    "tests/unit/test_manifests.py",
    '''@dataclass
class FakeTool:
    fn: object
    description: str = ""


def _register''',
    '''@dataclass
class FakeTool:
    fn: object
    description: str = ""


@pytest.fixture(autouse=True)
def _restore_manifest_state():
    manifests = copy.deepcopy(manifests_module._TOOL_MANIFESTS)
    active = manifests_module._ACTIVE_TOOL_NAMES
    reasons = dict(manifests_module._INACTIVE_REASONS)
    try:
        yield
    finally:
        manifests_module._TOOL_MANIFESTS.clear()
        manifests_module._TOOL_MANIFESTS.update(manifests)
        manifests_module._ACTIVE_TOOL_NAMES = active
        manifests_module._INACTIVE_REASONS.clear()
        manifests_module._INACTIVE_REASONS.update(reasons)


def _register''',
)

# Do not synthesize person.None in live test discovery.
replace_once(
    "tests/e2e/conftest.py",
    '''    person = _first_item("get_persons", "persons")
    if person:
        context["person_entity_id"] = f"person.{person.get('id')}"
''',
    '''    person = _first_item("get_persons", "persons")
    if person:
        person_id = person.get("id")
        if person_id:
            context["person_entity_id"] = f"person.{person_id}"
''',
)

# FastMCP accepts an argument mapping, not positional tool arguments.
replace_once(
    "tests/integration/conftest.py",
    '''    def call_tool(self, name, *args, **kwargs):
        """Execute a tool through the supported FastMCP client."""
        from fastmcp import Client

        async def _call():''',
    '''    def call_tool(self, name, *args, **kwargs):
        """Execute a tool through the supported FastMCP client."""
        if args:
            raise TypeError("MCPWrapper.call_tool accepts keyword tool arguments only")
        from fastmcp import Client

        async def _call():''',
)

# Auth skip guards must check that the token itself is configured.
for path in ("tests/smoke/conftest.py", "tests/e2e/conftest.py"):
    replace_once(
        path,
        'REST_API_TOKEN = os.getenv("REST_API_TOKEN") or os.getenv("MCP_AUTH_TOKEN", "")\nREST_HEADERS = {"Authorization": f"Bearer {REST_API_TOKEN}"}\n',
        'REST_API_TOKEN = os.getenv("REST_API_TOKEN") or os.getenv("MCP_AUTH_TOKEN", "")\nREST_AUTH_CONFIGURED = bool(REST_API_TOKEN)\nREST_HEADERS = {"Authorization": f"Bearer {REST_API_TOKEN}"}\n',
    )

for path in (
    "tests/smoke/test_critical_tools.py",
    "tests/smoke/test_input_validation.py",
    "tests/smoke/test_response_format.py",
):
    replace_once(
        path,
        "from .conftest import HA_TOKEN, REST_API_URL, REST_HEADERS, _server_running",
        "from .conftest import HA_TOKEN, REST_API_URL, REST_AUTH_CONFIGURED, REST_HEADERS, _server_running",
    )
    replace_once(
        path,
        '    or not REST_HEADERS["Authorization"].startswith("Bearer ")\n',
        "    or not REST_AUTH_CONFIGURED\n",
    )

replace_once(
    "tests/e2e/test_all_tools_smoke.py",
    "from .conftest import HA_TOKEN, REST_API_URL, REST_HEADERS, _server_running, discover_live_context",
    "from .conftest import (\n    HA_TOKEN,\n    REST_API_URL,\n    REST_AUTH_CONFIGURED,\n    REST_HEADERS,\n    _server_running,\n    discover_live_context,\n)",
)
replace_once(
    "tests/e2e/test_all_tools_smoke.py",
    '    or not REST_HEADERS["Authorization"].startswith("Bearer ")\n',
    "    or not REST_AUTH_CONFIGURED\n",
)
replace_once(
    "tests/smoke/test_response_format.py",
    '''            data, status = _call_tool_safe(name)
            if status == 400 and data.get("error", {}).get("code") == "INVALID_ARGUMENTS":
                # Tool requires parameters — out of scope for the zero-param envelope check.
                continue
            if data is None:
                errors.append(f"{name}: HTTP {status or 'timeout'}")
                continue
''',
    '''            data, status = _call_tool_safe(name)
            if data is None:
                errors.append(f"{name}: HTTP {status or 'timeout'}")
                continue
            error = data.get("error")
            if status == 400 and isinstance(error, dict) and error.get("code") == "INVALID_ARGUMENTS":
                # Tool requires parameters — out of scope for the zero-param envelope check.
                continue
''',
)

# Docker auth is keyed by hostname. Use one protected promotion credential with
# read access to quarantine and write access to the release package.
replace_once(
    ".github/workflows/publish.yml",
    '''      - name: Log in to quarantine read-only credential
        uses: docker/login-action@650006c6eb7dba73a995cc03b0b2d7f5ca915bee # v4
        with:
          registry: ghcr.io
          username: ${{ secrets.QUARANTINE_REGISTRY_USERNAME }}
          password: ${{ secrets.QUARANTINE_REGISTRY_READ_TOKEN }}
      - name: Log in to release registry
        uses: docker/login-action@650006c6eb7dba73a995cc03b0b2d7f5ca915bee # v4
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
''',
    '''      # Docker credentials are keyed by registry hostname, so two ghcr.io logins
      # overwrite each other. Keep this protected environment credential out of
      # the candidate job and grant it only read access to quarantine plus write
      # access to the release package.
      - name: Log in with protected promotion credential
        uses: docker/login-action@650006c6eb7dba73a995cc03b0b2d7f5ca915bee # v4
        with:
          registry: ghcr.io
          username: ${{ secrets.PROMOTION_REGISTRY_USERNAME }}
          password: ${{ secrets.PROMOTION_REGISTRY_TOKEN }}
''',
)

# Snapshot websocket handling and local traversal.
replace_once("context_generator/snapshot.py", "import logging\n", "import logging\nimport os\n")
replace_once(
    "context_generator/snapshot.py",
    "_WS_SOURCES: tuple[tuple[str, str, dict[str, Any]], ...] = (\n",
    '''class WebSocketProtocolError(RuntimeError):
    """The websocket stream can no longer be safely correlated by request id."""


_WS_SOURCES: tuple[tuple[str, str, dict[str, Any]], ...] = (
''',
)
replace_once(
    "context_generator/snapshot.py",
    '''        if response.get("id") != request_id or response.get("type") != "result":
            raise RuntimeError("unexpected websocket command response")
''',
    '''        if response.get("id") != request_id or response.get("type") != "result":
            raise WebSocketProtocolError("unexpected websocket command response")
''',
)
replace_once(
    "context_generator/snapshot.py",
    '''                    try:
                        result = self._ws_command(ws, request_id, command, extra)
                    except Exception as exc:
                        self._unavailable(
''',
    '''                    try:
                        result = self._ws_command(ws, request_id, command, extra)
                    except WebSocketProtocolError:
                        raise
                    except Exception as exc:
                        self._unavailable(
''',
)

new_weather = '''    def _collect_weather_forecasts(self, ws: Any, request_id: int) -> int:
        states = self.data["rest"].get("states_api") or []
        forecasts: dict[str, dict[str, Any]] = {}
        total = 0
        # HA weather feature bits: forecast daily=1, hourly=2, twice_daily=4.
        feature_types = ((1, "daily"), (2, "hourly"), (4, "twice_daily"))
        for item in states:
            if not isinstance(item, dict):
                continue
            entity_id = item.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id.startswith("weather."):
                continue
            raw_attrs = item.get("attributes")
            attrs: dict[str, Any] = raw_attrs if isinstance(raw_attrs, dict) else {}
            supported = attrs.get("supported_features", 0)
            try:
                feature_mask = int(supported or 0)
            except (TypeError, ValueError):
                feature_mask = 0
            per_entity: dict[str, Any] = {}
            for bit, forecast_type in feature_types:
                if not feature_mask & bit:
                    continue
                source = f"weather_forecast:{entity_id}:{forecast_type}"
                subscription_id = request_id
                unsubscribe_id = request_id + 1
                request_id += 2
                try:
                    ws.send(
                        json.dumps(
                            {
                                "id": subscription_id,
                                "type": "weather/subscribe_forecast",
                                "entity_id": entity_id,
                                "forecast_type": forecast_type,
                            }
                        )
                    )
                    ack = self._ws_recv_json(ws)
                    if ack.get("id") != subscription_id or ack.get("type") != "result":
                        raise WebSocketProtocolError("unexpected forecast subscription response")
                    if ack.get("success") is not True:
                        raise RuntimeError("forecast subscription failed")
                    event = self._ws_recv_json(ws)
                    if event.get("id") != subscription_id or event.get("type") != "event":
                        raise WebSocketProtocolError("unexpected forecast subscription event")
                    event_payload = event.get("event")
                    if not isinstance(event_payload, dict):
                        raise WebSocketProtocolError("forecast event payload is invalid")
                    forecast = event_payload.get("forecast", [])
                    safe, redactions = redact_sensitive(forecast)
                    self._ws_command(
                        ws,
                        unsubscribe_id,
                        "unsubscribe_events",
                        {"subscription": subscription_id},
                    )
                except WebSocketProtocolError:
                    raise
                except Exception as exc:
                    per_entity[forecast_type] = None
                    self.provenance.record(
                        source,
                        method="websocket",
                        status="unavailable",
                        reason=type(exc).__name__,
                        requested="weather/subscribe_forecast",
                    )
                else:
                    per_entity[forecast_type] = safe
                    count = record_count(safe)
                    total += count
                    self.provenance.record(
                        source,
                        method="websocket",
                        status="complete",
                        records=count,
                        size_bytes=self._encoded_size(safe),
                        redacted_fields=redactions,
                        requested="weather/subscribe_forecast",
                    )
            if per_entity:
                forecasts[entity_id] = per_entity
        self.data["websocket"]["weather_forecasts"] = forecasts
        self.provenance.record(
            "weather_forecasts",
            method="websocket",
            status="complete",
            records=total,
            size_bytes=self._encoded_size(forecasts),
            requested="weather/subscribe_forecast",
        )
        return request_id

'''
replace_between(
    "context_generator/snapshot.py",
    "    def _collect_weather_forecasts(self, ws: Any, request_id: int) -> int:\n",
    "    def _collect_files(self) -> None:\n",
    new_weather,
)

text = read("context_generator/snapshot.py")
marker = "    def _collect_files(self) -> None:\n"
start = text.find(marker)
if start < 0:
    raise SystemExit("context_generator/snapshot.py: _collect_files marker missing")
new_files = '''    def _collect_files(self) -> None:
        root = self.config.config_path.resolve(strict=False)
        output: dict[str, Any] = {}
        total_bytes = 0
        if not root.is_dir():
            self._unavailable(
                "files", "config_tree", method="filesystem", reason="config root unavailable"
            )
            return

        for current_root, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            current = Path(current_root)
            safe_dirs: list[str] = []
            for dirname in sorted(dirnames):
                candidate = current / dirname
                if dirname.casefold() in _BLOCKED_DIRS or candidate.is_symlink():
                    continue
                safe_dirs.append(dirname)
            dirnames[:] = safe_dirs

            for filename in sorted(filenames):
                path = current / filename
                try:
                    relative = path.relative_to(root)
                except ValueError:
                    continue
                if path.is_symlink() or not path.is_file():
                    continue
                name = path.name.casefold()
                if name in _BLOCKED_NAMES or any(
                    name.startswith(prefix) for prefix in _BLOCKED_PREFIXES
                ):
                    self.provenance.record(
                        f"file:{relative.as_posix()}",
                        method="filesystem",
                        status="skipped",
                        reason="policy: credential-bearing source blocked",
                    )
                    continue
                if path.suffix.casefold() not in _TEXT_SUFFIXES and ".storage" not in relative.parts:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if (
                    size > self.config.max_source_bytes
                    or total_bytes + size > self.config.max_source_bytes
                ):
                    self.provenance.record(
                        f"file:{relative.as_posix()}",
                        method="filesystem",
                        status="partial",
                        size_bytes=size,
                        reason="aggregate source-size limit reached",
                    )
                    continue
                try:
                    raw = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                value: Any = raw
                if path.suffix.casefold() == ".json" or ".storage" in relative.parts:
                    try:
                        value = json.loads(raw)
                    except json.JSONDecodeError:
                        value = raw
                if ".storage" in relative.parts:
                    projected = sanitize_model_visible_storage(path.name, value)
                    if projected is None:
                        self.provenance.record(
                            f"file:{relative.as_posix()}",
                            method="filesystem",
                            status="skipped",
                            size_bytes=size,
                            reason=(
                                "policy: .storage schema is not on the positive model-visible allowlist; "
                                f"allowed={','.join(sorted(SAFE_STORAGE_SANITIZERS))}"
                            ),
                        )
                        continue
                    value = projected
                safe, redactions = redact_sensitive(value)
                output[relative.as_posix()] = safe
                total_bytes += size
                self.provenance.record(
                    f"file:{relative.as_posix()}",
                    method="filesystem",
                    status="complete",
                    records=record_count(safe),
                    size_bytes=self._encoded_size(safe),
                    redacted_fields=redactions,
                )
        self.data["files"]["config_tree"] = output
        self.provenance.record(
            "filesystem_snapshot",
            method="filesystem",
            status="complete",
            records=len(output),
            size_bytes=self._encoded_size(output),
        )
'''
write("context_generator/snapshot.py", text[:start] + new_files)

# Context collection fails closed outside an explicit GenerationConfig.
replace_once(
    "context_generator/utils.py",
    '''    if config is None:
        return constants.HA_URL, constants.HA_TOKEN, True, "legacy"
''',
    '''    if config is None:
        return "", "", False, "unconfigured"
''',
)
replace_once(
    "context_generator/utils.py",
    '''    # Context collection follows the same conservative no-automatic-retry
    # contract as public READ operations. A failed source is recorded in the
    # provenance matrix rather than being retried implicitly.
    for attempt in range(1):
        try:
            if method == "GET":
                response = requests.get(f"{ha_url}{endpoint}", headers=headers, timeout=timeout)
            elif method == "POST":
                response = requests.post(
                    f"{ha_url}{endpoint}", headers=headers, json=data, timeout=timeout
                )
            else:
                return {"success": False, "error": f"Unsupported method: {method}"}

            response.raise_for_status()
            try:
                payload: Any = response.json()
            except ValueError:
                payload = response.text
            _record_source(
                source, method="rest", status="complete", data=payload, requested=endpoint
            )
            return {"success": True, "data": payload}

        except requests.exceptions.HTTPError as e:
            reason = f"HTTP {e.response.status_code}: {str(e)}"
            _record_source(
                source, method="rest", status="unavailable", reason=reason, requested=endpoint
            )
            return {"success": False, "error": reason}
        except requests.exceptions.Timeout:
            reason = "Request timeout"
            _record_source(
                source, method="rest", status="unavailable", reason=reason, requested=endpoint
            )
            return {"success": False, "error": reason}
        except Exception as e:
            reason = type(e).__name__
            _record_source(
                source, method="rest", status="unavailable", reason=reason, requested=endpoint
            )
            return {"success": False, "error": str(e)}

    return {"success": False, "error": "Request failed"}
''',
    '''    # Context collection follows the same conservative no-automatic-retry
    # contract as public READ operations. A failed source is recorded in the
    # provenance matrix rather than being retried implicitly.
    normalized_method = method.upper()
    try:
        if normalized_method == "GET":
            response = requests.get(f"{ha_url}{endpoint}", headers=headers, timeout=timeout)
        elif normalized_method == "POST":
            response = requests.post(
                f"{ha_url}{endpoint}", headers=headers, json=data, timeout=timeout
            )
        else:
            return {"success": False, "error": f"Unsupported method: {method}"}

        response.raise_for_status()
        try:
            payload: Any = response.json()
        except ValueError:
            payload = response.text
        _record_source(source, method="rest", status="complete", data=payload, requested=endpoint)
        return {"success": True, "data": payload}

    except requests.exceptions.HTTPError as e:
        reason = f"HTTP {e.response.status_code}: {str(e)}"
        _record_source(source, method="rest", status="unavailable", reason=reason, requested=endpoint)
        return {"success": False, "error": reason}
    except requests.exceptions.Timeout:
        reason = "Request timeout"
        _record_source(source, method="rest", status="unavailable", reason=reason, requested=endpoint)
        return {"success": False, "error": reason}
    except Exception as e:
        reason = type(e).__name__
        _record_source(source, method="rest", status="unavailable", reason=reason, requested=endpoint)
        return {"success": False, "error": str(e)}
''',
)

# User-derived concurrency keys must not accumulate forever.
replace_once("tools/invocation.py", "import time\n", "import time\nimport weakref\n")
replace_once(
    "tools/invocation.py",
    '''class InvocationKernel:
    """Enforce manifests, authorization, deadlines, concurrency and output bounds."""
''',
    '''class _KeyedSemaphore(threading.BoundedSemaphore):
    """Semaphore carrying the manifest limit for conflict validation."""

    def __init__(self, limit: int) -> None:
        super().__init__(limit)
        self.declared_limit = limit


class InvocationKernel:
    """Enforce manifests, authorization, deadlines, concurrency and output bounds."""
''',
)
replace_once(
    "tools/invocation.py",
    '''        self._locks: dict[str, tuple[int, threading.BoundedSemaphore]] = {}
        self._locks_guard = threading.Lock()
''',
    '''        # In-flight invocations and callbacks retain a strong reference; idle
        # user-derived keys disappear when no operation references the semaphore.
        self._locks: weakref.WeakValueDictionary[str, _KeyedSemaphore] = (
            weakref.WeakValueDictionary()
        )
        self._locks_guard = threading.Lock()
''',
)
replace_once(
    "tools/invocation.py",
    '''    def _semaphore(self, key: str, limit: int) -> threading.BoundedSemaphore:
        with self._locks_guard:
            existing = self._locks.get(key)
            if existing is None:
                semaphore = threading.BoundedSemaphore(limit)
                self._locks[key] = (limit, semaphore)
                return semaphore
            existing_limit, semaphore = existing
            if existing_limit != limit:
                raise InvocationError(
                    "MANIFEST_INVALID",
                    f"Concurrency key '{key}' is declared with conflicting limits",
                )
            return semaphore
''',
    '''    def _semaphore(self, key: str, limit: int) -> threading.BoundedSemaphore:
        with self._locks_guard:
            semaphore = self._locks.get(key)
            if semaphore is None:
                semaphore = _KeyedSemaphore(limit)
                self._locks[key] = semaphore
                return semaphore
            if semaphore.declared_limit != limit:
                raise InvocationError(
                    "MANIFEST_INVALID",
                    f"Concurrency key '{key}' is declared with conflicting limits",
                )
            return semaphore
''',
)

# Search stops globally at the cap and counts only files actually searched.
path = "tools/filesystem_explorer.py"
text = read(path)
start = text.find("def _do_search_files(pattern: str, search_path: str, max_results: int) -> dict[str, Any]:\n")
end = text.find("\n\n# =============================================================================\n# FILESYSTEM EXPLORER", start)
if start < 0 or end < 0:
    raise SystemExit("tools/filesystem_explorer.py: search function markers missing")
new_search = '''def _do_search_files(pattern: str, search_path: str, max_results: int) -> dict[str, Any]:
    """Search for files containing a text pattern (safe grep)."""
    if not re.match(r"^[a-zA-Z0-9\\s\\-_\\.\\/\\\\:\\[\\]\\(\\)\\{\\}\\+\\*\\?\\^\\$\\|@#%&=!<>~\\']+$", pattern):
        return create_error_response(
            "INVALID_PARAM",
            "Invalid search pattern: pattern contains blocked special characters",
            retryable=False,
        )

    try:
        target = SECURITY_CONTEXT.validate_path(search_path)
    except PermissionError as e:
        return create_error_response("ACCESS_DENIED", str(e), retryable=False)

    if not target.is_dir():
        return create_error_response(
            "INVALID_PARAM", f"Search path must be a directory: {target}", retryable=False
        )

    results: list[dict[str, Any]] = []
    files_searched = 0
    truncated = False

    for root, dirs, files in os.walk(target, followlinks=False):
        if len(results) >= max_results:
            truncated = True
            dirs[:] = []
            break
        if root.count(os.sep) - str(target).count(os.sep) > SECURITY_CONTEXT.max_depth:
            dirs[:] = []
            continue
        safe_dirs: list[str] = []
        for dirname in dirs:
            try:
                SECURITY_CONTEXT.validate_path(Path(root) / dirname)
            except PermissionError:
                continue
            safe_dirs.append(dirname)
        dirs[:] = safe_dirs

        for filename in files:
            if len(results) >= max_results:
                truncated = True
                dirs[:] = []
                break

            filepath = Path(root) / filename
            try:
                filepath = SECURITY_CONTEXT.validate_text_file(filepath)
            except PermissionError:
                continue
            if SECURITY_CONTEXT.is_binary_file(filepath):
                continue
            try:
                if filepath.stat().st_size > 2 * 1024 * 1024:
                    continue
            except FileNotFoundError:
                continue

            try:
                with open(filepath, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue
            files_searched += 1
            if re.search(re.escape(pattern), content, re.IGNORECASE):
                matches: list[dict[str, Any]] = []
                for match in re.finditer(re.escape(pattern), content, re.IGNORECASE):
                    match_start = max(0, match.start() - 30)
                    match_end = min(len(content), match.end() + 30)
                    context = content[match_start:match_end].replace("\n", " ").strip()
                    matches.append({"position": match.start(), "context": context})
                    if len(matches) >= 3:
                        break
                results.append(
                    {
                        "path": str(filepath.relative_to(target)),
                        "absolute_path": str(filepath),
                        "matches_count": len(matches),
                        "sample_matches": matches[:3],
                    }
                )

    return {
        "success": True,
        "pattern": pattern,
        "search_path": str(target),
        "files_searched": files_searched,
        "results_count": len(results),
        "results": results,
        "truncated": truncated,
    }
'''
write(path, text[:start] + new_search + text[end:])

# Normalize the HA helper method exactly once.
replace_once(
    "tools/utils.py",
    '''    if retries < 1:
        raise ValueError("retries must be at least 1")
    if method.upper() == "POST" and retries != 1:
        raise ValueError(
            "POST retries require operation-specific handling and are not supported here"
        )

    url = f"{ha_url}{endpoint}"
''',
    '''    if retries < 1:
        raise ValueError("retries must be at least 1")
    normalized_method = method.upper()
    if normalized_method not in {"GET", "POST"}:
        raise ValueError(f"Unsupported HTTP method: {method}")
    if normalized_method == "POST" and retries != 1:
        raise ValueError(
            "POST retries require operation-specific handling and are not supported here"
        )

    url = f"{ha_url}{endpoint}"
''',
)
replace_once(
    "tools/utils.py",
    '''            if method == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=request_timeout)
            else:
                response = requests.get(url, headers=headers, timeout=request_timeout)
''',
    '''            if normalized_method == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=request_timeout)
            else:
                response = requests.get(url, headers=headers, timeout=request_timeout)
''',
)

# Runtime socket ports are bounded to the valid range.
replace_once(
    "tools/settings.py",
    '''            health_check_port=int(os.getenv("HEALTH_CHECK_PORT", "9091")),
            mcp_port=int(os.getenv("MCP_PORT", "9092")),
            rest_api_port=int(os.getenv("REST_API_PORT", "9093")),
''',
    '''            health_check_port=_env_int("HEALTH_CHECK_PORT", 9091, minimum=1, maximum=65535),
            mcp_port=_env_int("MCP_PORT", 9092, minimum=1, maximum=65535),
            rest_api_port=_env_int("REST_API_PORT", 9093, minimum=1, maximum=65535),
''',
)

# Do not hold transport startup behind repeated backend reachability probes.
replace_once(
    "server.py",
    '''    # The startup probe is bounded but retried: a container or network that is
    # still warming up can fail a single two-second probe, and a one-shot
    # failure must not permanently degrade every Home Assistant capability.
    deadline = time.monotonic() + 10
    detail = {"configured": True, "reachable": False, "reason": "startup probe not completed"}
    while time.monotonic() < deadline:
        detail = _probe_backend()
        if detail["reachable"]:
            break
        time.sleep(1)
''',
    '''    # One bounded probe establishes the initial capability profile without
    # delaying MCP/REST transport startup for an unreachable backend. The
    # background reconciler performs subsequent retries and promotes tools when
    # Home Assistant becomes reachable.
    detail = _probe_backend()
''',
)

# Avoid reflecting filesystem details and stream the normal markdown artifact.
replace_once(
    "server.py",
    "    from starlette.responses import JSONResponse, PlainTextResponse\n",
    "    from starlette.responses import FileResponse, JSONResponse\n",
)
replace_once(
    "server.py",
    '''        except (ValueError, SecurityBoundaryError) as exc:
            return JSONResponse(
                {"success": False, "error": {"code": "INVALID_PATH", "message": str(exc)}},
                status_code=400,
            )
''',
    '''        except (ValueError, SecurityBoundaryError):
            return JSONResponse(
                {
                    "success": False,
                    "error": {"code": "INVALID_PATH", "message": "Invalid or disallowed context path"},
                },
                status_code=400,
            )
''',
)
replace_once(
    "server.py",
    '''    async def context_download(request: Request) -> Any:
        try:
            output = _CONTEXT_TASKS.output_for(current_principal().subject)
            content = output.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, SecurityBoundaryError):
            return JSONResponse(
                {
                    "success": False,
                    "error": {"code": "NOT_FOUND", "message": "Context artifact not available"},
                },
                status_code=404,
            )
        if request.query_params.get("format") == "json":
            return JSONResponse(
                {"success": True, "content": content, "size_bytes": len(content.encode())}
            )
        return PlainTextResponse(
            content,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{output.name}"'},
        )
''',
    '''    async def context_download(request: Request) -> Any:
        try:
            output = _CONTEXT_TASKS.output_for(current_principal().subject)
        except (FileNotFoundError, OSError, SecurityBoundaryError):
            return JSONResponse(
                {
                    "success": False,
                    "error": {"code": "NOT_FOUND", "message": "Context artifact not available"},
                },
                status_code=404,
            )
        if request.query_params.get("format") == "json":
            try:
                content = output.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return JSONResponse(
                    {
                        "success": False,
                        "error": {"code": "NOT_FOUND", "message": "Context artifact not available"},
                    },
                    status_code=404,
                )
            return JSONResponse(
                {"success": True, "content": content, "size_bytes": len(content.encode())}
            )
        return FileResponse(output, media_type="text/markdown", filename=output.name)
''',
)
replace_once(
    "server.py",
    '''def run_startup_tests() -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/unit", "-q", "-p", "no:cacheprovider"],
''',
    '''def run_startup_tests() -> bool:
    tests_dir = Path(__file__).resolve().parent / "tests" / "unit"
    if not tests_dir.is_dir():
        _logger.warning("Startup tests requested but unit tests are not installed; skipping")
        return True
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(tests_dir), "-q", "-p", "no:cacheprovider"],
''',
)

# Official client workflow waits for the actual HTTP/auth stack, not Docker TCP.
path = ".github/workflows/official-client.yml"
text = read(path)
old_probe = '''          for _ in $(seq 1 100); do
            if python - <<'PY'
          import socket
          try:
              with socket.create_connection(('127.0.0.1', 9092), timeout=0.2):
                  pass
          except OSError:
              raise SystemExit(1)
          PY
            then
              break
            fi
            sleep 0.2
          done
          docker inspect --format '{{.State.Status}}' ha-mcp-official | grep -qx running
'''
new_probe = '''          python - <<'PY'
          import json
          import time
          import urllib.error
          import urllib.request

          payload = json.dumps({
              "jsonrpc": "2.0",
              "id": 1,
              "method": "initialize",
              "params": {
                  "protocolVersion": "2025-11-25",
                  "capabilities": {},
                  "clientInfo": {"name": "readiness-probe", "version": "1"},
              },
          }).encode()
          deadline = time.monotonic() + 30
          last = "no response"
          while time.monotonic() < deadline:
              request = urllib.request.Request(
                  "http://127.0.0.1:9092/mcp",
                  data=payload,
                  method="POST",
                  headers={
                      "Accept": "application/json, text/event-stream",
                      "Content-Type": "application/json",
                  },
              )
              try:
                  urllib.request.urlopen(request, timeout=1)
              except urllib.error.HTTPError as exc:
                  if exc.code == 401:
                      break
                  last = f"HTTP {exc.code}"
              except (OSError, urllib.error.URLError) as exc:
                  last = type(exc).__name__
              else:
                  last = "anonymous initialize unexpectedly succeeded"
              time.sleep(0.2)
          else:
              raise SystemExit(f"MCP HTTP stack did not become ready: {last}")
          PY
          docker inspect --format '{{.State.Status}}' ha-mcp-official | grep -qx running
'''
if old_probe not in text:
    raise SystemExit("official-client.yml: TCP readiness probe not found")
text = text.replace(old_probe, new_probe, 1)
text = text.replace(
    "headers={'Content-Type': 'application/json'},\n",
    "headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'},\n",
)
write(path, text)

# Migration evidence uses exact identities and truthful job/check-run ids.
replace_once(
    ".github/workflows/migration-evidence.yml",
    "          python -m pip install 'build==1.5.0' 'setuptools==83.0.0' 'wheel==0.47.0'\n",
    "          python -m pip install -c constraints-ci.txt 'build==1.5.0' 'setuptools==83.0.0' 'wheel==0.47.0'\n",
)
replace_once(
    ".github/workflows/migration-evidence.yml",
    '''          selected = [
              artifact for artifact in artifacts
              if artifact.get('name', '').startswith(('python-wheel-', 'container-image-'))
          ]
          if len(selected) < 3:
              raise SystemExit(
                  f'expected wheel plus two container artifacts, got {[a.get("name") for a in selected]}'
              )
''',
    '''          expected_artifacts = {
              f'python-wheel-{head}',
              f'container-image-{head}-amd64',
              f'container-image-{head}-arm64',
          }
          selected = [artifact for artifact in artifacts if artifact.get('name') in expected_artifacts]
          selected_names = {artifact.get('name') for artifact in selected}
          if selected_names != expected_artifacts or len(selected) != len(expected_artifacts):
              raise SystemExit(
                  f'expected exact CI artifacts {sorted(expected_artifacts)}, got {sorted(str(name) for name in selected_names)}'
              )
''',
)
replace_once(
    ".github/workflows/migration-evidence.yml",
    '''          wheel = next(Path('dist').glob('*.whl'))
          wheel_digest = 'sha256:' + hashlib.sha256(wheel.read_bytes()).hexdigest()
          report = {
''',
    '''          def check_run_id(job):
              url = job.get('check_run_url')
              if not isinstance(url, str) or '/check-runs/' not in url:
                  raise SystemExit(f"job lacks check_run_url: {job.get('name')}")
              try:
                  return int(url.rstrip('/').rsplit('/', 1)[-1])
              except ValueError as exc:
                  raise SystemExit(f"invalid check_run_url for {job.get('name')}: {url}") from exc

          wheel = next(Path('dist').glob('*.whl'))
          wheel_digest = 'sha256:' + hashlib.sha256(wheel.read_bytes()).hexdigest()
          report = {
''',
)
replace_once(
    ".github/workflows/migration-evidence.yml",
    '''                          'job_id': int(by_name[name]['id']),
                          'check_run_id': int(by_name[name]['id']),
''',
    '''                          'job_id': int(by_name[name]['id']),
                          'check_run_id': check_run_id(by_name[name]),
''',
)

# Focused regression tests for two production findings.
Path("tests/unit/test_review_regressions.py").write_text(
    '''"""Regression tests for PR review findings."""

import gc

import pytest

from tools.invocation import InvocationKernel
from tools.settings import RuntimeSettings


def test_invocation_key_cache_releases_idle_user_keys() -> None:
    kernel = InvocationKernel(max_workers=1)
    semaphore = kernel._semaphore("resource:user-controlled", 1)
    assert "resource:user-controlled" in kernel._locks
    del semaphore
    gc.collect()
    assert "resource:user-controlled" not in kernel._locks


@pytest.mark.parametrize("name", ["HEALTH_CHECK_PORT", "MCP_PORT", "REST_API_PORT"])
def test_runtime_ports_reject_out_of_range(monkeypatch, name: str) -> None:
    monkeypatch.setenv(name, "70000")
    with pytest.raises(ValueError, match=name):
        RuntimeSettings.from_env()
''',
    encoding="utf-8",
)
