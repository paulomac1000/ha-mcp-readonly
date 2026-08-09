from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.0.0"
AI_SKILLS_REVISION = "b54fc6b27ea80b36a70d5de73445970e17f55789"
OLD_AI_SKILLS_REVISION = "c5ba4091cd8a3043fe4ba9715a3bda96d62a05e4"


def path(name: str) -> Path:
    return ROOT / name


def replace_once(name: str, old: str, new: str) -> None:
    target = path(name)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected text not found in {name}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def regex_once(name: str, pattern: str, replacement: str) -> None:
    target = path(name)
    text = target.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL | re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"expected one regex match in {name}, got {count}: {pattern}")
    target.write_text(updated, encoding="utf-8")


def write(name: str, content: str) -> None:
    target = path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


# ---------------------------------------------------------------------------
# Versioning and pinned upstream contract
# ---------------------------------------------------------------------------
write(
    "version.py",
    '''
    """Project version. Single source of truth for the whole project."""

    __version__ = "2.0.0"
    ''',
)
replace_once("pyproject.toml", 'version = "1.7.0"', 'version = "2.0.0"')

changelog = path("CHANGELOG.md").read_text(encoding="utf-8")
changelog = changelog.replace("## [1.7.0] - 2026-08-09", "## [2.0.0] - 2026-08-09", 1)
needle = "## [2.0.0] - 2026-08-09\n"
if needle not in changelog:
    raise RuntimeError("2.0.0 changelog heading missing")
breaking = '''

### Breaking Changes
- Removed the legacy two-endpoint HTTP+SSE transport. The supported MCP transports are now stdio and Streamable HTTP only.
- The network MCP deployment now uses an explicit hardened ASGI application with bounded request/header sizes, trusted Host policy, exact-origin CORS policy, connection limits, and explicit stateless/stateful mode.
- Public capability discovery now separates supported and active transports/components and reports server, SDK, protocol, and deployment-profile identity.
- The project major version is now 2.0.0 because transport and public health/discovery semantics changed incompatibly from the 1.x line.

### Security Hardening
- Final tool responses are recursively redacted at the application-owned operation boundary using key-aware credential filtering plus token/JWT/IP pattern sanitization.
- Blocking coroutine adapters in the storage tool family run through the bounded invocation executor instead of blocking the MCP event loop.
- Async admission cancellation no longer leaks semaphore permits while a background semaphore acquire is still running.
- REST and MCP HTTP request bodies and aggregate headers are bounded before application parsing.

### Verification Added
- Added official `mcp` Python SDK interoperability smoke tests for the exact installed wheel over stdio and the exact container over authenticated Streamable HTTP.
- Added regression tests for credential redaction, async admission cancellation, blocking-coroutine isolation, JSON-schema generation for unions/generics, HTTP request limits, and manifest/server version consistency.
'''
if "### Breaking Changes" not in changelog.split("## [1.6.0]", 1)[0]:
    changelog = changelog.replace(needle, needle + breaking, 1)
path("CHANGELOG.md").write_text(changelog, encoding="utf-8")

lock = path("ai-skills.lock.yaml").read_text(encoding="utf-8")
lock = lock.replace(OLD_AI_SKILLS_REVISION, AI_SKILLS_REVISION)
path("ai-skills.lock.yaml").write_text(lock, encoding="utf-8")

ci = path(".github/workflows/ci.yml").read_text(encoding="utf-8")
ci = ci.replace(OLD_AI_SKILLS_REVISION, AI_SKILLS_REVISION)
version_gate = '''
      - name: Verify package version
        run: |
          python - <<'PY'
          import tomllib
          from pathlib import Path
          from version import __version__
          project = tomllib.loads(Path('pyproject.toml').read_text())['project']
          assert project['version'] == __version__
          PY
'''
manifest_gate = version_gate + '''      - name: Verify capability manifest versions
        run: |
          python - <<'PY'
          import json
          from pathlib import Path
          from version import __version__
          manifests = json.loads(Path('tools/tool_manifests.json').read_text())
          stale = sorted(
              name for name, manifest in manifests.items()
              if manifest.get('extensions', {}).get('tool_version') != __version__
          )
          assert not stale, f'stale capability versions: {stale}'
          PY
'''
if version_gate not in ci:
    raise RuntimeError("CI version gate marker not found")
ci = ci.replace(version_gate, manifest_gate, 1)
path(".github/workflows/ci.yml").write_text(ci, encoding="utf-8")

write(
    "docs/ai-skills-adoption.md",
    f'''
    ---
    description: Status and evidence policy for adoption of the pinned ai-skills contracts.
    doc_id: reference.ai-skills-adoption
    type: reference
    status: active
    rigor: normative
    owners: [repository-maintainers]
    verification: Compare the pinned revision with CI configuration and generate provider-backed adoption evidence only after the assessed GitHub revision and its checks exist.
    ---

    # ai-skills adoption status

    The repository targets the immutable `paulomac1000/ai-skills` revision
    `{AI_SKILLS_REVISION}` from the `fix/unified-contract-release-hardening` line.
    The revision immediately follows the previous `c5ba4091` pin and changes only the
    .NET generator lock validation lane; the Python/FastMCP normative entrypoints and
    their recorded content digests are unchanged. The consumer lock, CI checkout, and
    migration evidence must all use the same immutable revision.

    This repository implements the reviewed migration code, but implementation is not
    the same thing as provider-backed acceptance. A canonical adoption assessment is
    evidence about an already-existing immutable GitHub revision, so final assessment
    data is generated as a CI/review artifact after the exact commit exists rather than
    attempting to predict the SHA of the commit containing the assessment itself.

    ## Evidence policy

    Final L3 acceptance must use the canonical adoption schema and validator from the
    pinned ai-skills revision. Evidence must bind the assessed revision, workflow run,
    job/check IDs, exact wheel/container artifacts and digests, official-client transport
    results, live Home Assistant integration results, residual risks, rollback procedure,
    and an independent GitHub review to the same SHA.

    The public CI lanes provide deterministic source, wheel, container, security, and
    official-client evidence without requiring a private Home Assistant instance. Live
    Home Assistant smoke, E2E, and integration evidence remains a separate required lane
    and must be attached before the final decision can become `approve`.

    Until that provider-backed assessment and independent review exist, the adoption
    decision remains **request changes / not certified**. This is an evidence-state
    statement, not a claim that the implementation should be rolled back.

    ## Upstream normative vocabulary conflict

    The pinned capability-manifest JSON schema and the narrative capability-manifest
    reference currently use partially different field vocabularies. This consumer treats
    the canonical JSON schema as the executable serialization contract and records the
    narrative-only concepts through reviewed extension fields where possible. The
    discrepancy is an upstream residual risk and must be resolved by ai-skills before a
    final certification claims that the two sources are literally identical.

    ## Known residual risk

    The new runtime policy surface and context runtime modules are strict-mypy gates.
    Older monolithic context analyzers/formatters/utilities and `ha_graph` still contain
    pre-existing typing debt under a narrowly scoped override. That debt is not represented
    as complete repository-wide strict typing and should be retired incrementally.
    ''',
)

# Keep the static manifest catalog aligned with the public major version.
manifest_path = path("tools/tool_manifests.json")
manifests = json.loads(manifest_path.read_text(encoding="utf-8"))
for manifest in manifests.values():
    manifest.setdefault("extensions", {})["tool_version"] = VERSION
manifest_path.write_text(json.dumps(manifests, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# ---------------------------------------------------------------------------
# Central redaction and schema utilities
# ---------------------------------------------------------------------------
write(
    "tools/redaction.py",
    r'''
    """Final response redaction for all model-visible operation results."""

    from __future__ import annotations

    import re
    from typing import Any

    from tools.utils import sanitize_log_line

    REDACTED = "[REDACTED]"
    _NORMALIZE_KEY = re.compile(r"[^a-z0-9]+")
    _SENSITIVE_KEYS = frozenset(
        {
            "password",
            "passwd",
            "pwd",
            "token",
            "access_token",
            "refresh_token",
            "id_token",
            "api_key",
            "apikey",
            "api_token",
            "secret",
            "client_secret",
            "private_key",
            "authorization",
            "cookie",
            "set_cookie",
            "credential",
            "credentials",
            "ssid",
        }
    )
    _SENSITIVE_SUFFIXES = (
        "_password",
        "_passwd",
        "_token",
        "_secret",
        "_api_key",
        "_private_key",
        "_credential",
        "_credentials",
        "_cookie",
    )


    def _normalized_key(value: object) -> str:
        return _NORMALIZE_KEY.sub("_", str(value).strip().casefold()).strip("_")


    def _is_sensitive_key(value: object) -> bool:
        key = _normalized_key(value)
        return key in _SENSITIVE_KEYS or any(key.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


    def sanitize_response_data(value: Any) -> Any:
        """Recursively remove credential-bearing fields and sanitize free-form strings.

        Key-aware redaction runs before string-pattern sanitization. This prevents a
        secret stored as a plain value under a field such as ``password`` or
        ``refresh_token`` from bypassing regexes that only see the value itself.
        """
        if isinstance(value, str):
            return sanitize_log_line(value)
        if isinstance(value, dict):
            return {
                key: REDACTED if _is_sensitive_key(key) else sanitize_response_data(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [sanitize_response_data(item) for item in value]
        if isinstance(value, tuple):
            return tuple(sanitize_response_data(item) for item in value)
        return value
    ''',
)

write(
    "tools/schema_utils.py",
    '''
    """Generate JSON Schema from application operation signatures."""

    from __future__ import annotations

    import inspect
    from typing import Any, get_type_hints

    from pydantic import TypeAdapter


    def signature_to_json_schema(function: Any) -> dict[str, Any]:
        """Return a bounded object schema preserving unions, generics, and nullability."""
        signature = inspect.signature(function)
        try:
            type_hints = get_type_hints(function)
        except (NameError, TypeError):
            type_hints = {}

        properties: dict[str, Any] = {}
        required: list[str] = []
        for name, parameter in signature.parameters.items():
            if name == "self":
                continue
            annotation = type_hints.get(name, parameter.annotation)
            if annotation is inspect.Parameter.empty:
                property_schema: dict[str, Any] = {}
            else:
                try:
                    property_schema = TypeAdapter(annotation).json_schema()
                except Exception:
                    property_schema = {"type": "string"}
            if parameter.default is inspect.Parameter.empty:
                required.append(name)
            else:
                property_schema = dict(property_schema)
                property_schema["default"] = parameter.default
            properties[name] = property_schema

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = required
        return schema
    ''',
)

write(
    "tools/http_security.py",
    '''
    """ASGI request-size enforcement shared by MCP HTTP and REST adapters."""

    from __future__ import annotations

    import json
    from typing import Any

    from starlette.types import ASGIApp, Message, Receive, Scope, Send


    class RequestLimitExceeded(RuntimeError):
        """Raised internally when a streamed request exceeds the configured bound."""


    class RequestLimitsMiddleware:
        """Reject oversized aggregate headers and request bodies before parsing."""

        def __init__(self, app: ASGIApp, *, max_body_bytes: int, max_header_bytes: int) -> None:
            if max_body_bytes < 1 or max_header_bytes < 1:
                raise ValueError("HTTP request limits must be positive")
            self.app = app
            self.max_body_bytes = max_body_bytes
            self.max_header_bytes = max_header_bytes

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope.get("type") != "http":
                await self.app(scope, receive, send)
                return

            headers = scope.get("headers", [])
            header_bytes = sum(len(name) + len(value) for name, value in headers)
            if header_bytes > self.max_header_bytes:
                await self._reject(send, 431, "Request headers exceed configured limit")
                return

            consumed = 0
            response_started = False

            async def limited_receive() -> Message:
                nonlocal consumed
                message = await receive()
                if message.get("type") == "http.request":
                    body = message.get("body", b"")
                    if isinstance(body, bytes):
                        consumed += len(body)
                    if consumed > self.max_body_bytes:
                        raise RequestLimitExceeded("Request body exceeds configured limit")
                return message

            async def tracking_send(message: Message) -> None:
                nonlocal response_started
                if message.get("type") == "http.response.start":
                    response_started = True
                await send(message)

            try:
                await self.app(scope, limited_receive, tracking_send)
            except RequestLimitExceeded:
                if not response_started:
                    await self._reject(send, 413, "Request body exceeds configured limit")
                    return
                raise

        @staticmethod
        async def _reject(send: Send, status: int, message: str) -> None:
            body = json.dumps(
                {"success": False, "error": {"code": "REQUEST_TOO_LARGE", "message": message}}
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body, "more_body": False})
    ''',
)

# ---------------------------------------------------------------------------
# Runtime settings and constants
# ---------------------------------------------------------------------------
write(
    "tools/settings.py",
    '''
    """Immutable runtime settings loaded once before application composition."""

    from __future__ import annotations

    import os
    from dataclasses import dataclass
    from pathlib import Path
    from typing import Literal


    def _env_bool(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().casefold() in {"1", "true", "yes", "on"}


    def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
        raw = os.getenv(name)
        value = default if raw is None else int(raw)
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return value


    @dataclass(frozen=True, slots=True)
    class RuntimeSettings:
        ha_url: str
        ha_token: str
        ha_config_path: str
        health_check_port: int
        mcp_port: int
        rest_api_port: int
        mcp_transport: Literal["stdio", "http"]
        rest_api_enabled: bool
        dev_tools_enabled: bool
        run_tests_on_startup: bool
        health_server_enabled: bool
        backend_required_for_ready: bool
        mcp_bind_host: str
        mcp_auth_token: str
        rest_api_token: str
        cors_allowed_origins: tuple[str, ...]
        mcp_allowed_hosts: tuple[str, ...]
        mcp_http_max_body_bytes: int
        mcp_http_max_header_bytes: int
        mcp_http_connection_limit: int
        mcp_http_keepalive_seconds: int
        mcp_http_stateless: bool
        output_path: str
        context_output_root: str
        log_level: str

        @classmethod
        def from_env(cls) -> "RuntimeSettings":
            transport = os.getenv("MCP_TRANSPORT", "stdio").strip().casefold()
            if transport == "streamable-http":
                transport = "http"
            if transport == "sse":
                raise ValueError("Legacy SSE transport has been removed; use stdio or Streamable HTTP")
            if transport not in {"stdio", "http"}:
                raise ValueError("MCP_TRANSPORT must be one of: stdio, http")

            bind_host = os.getenv("MCP_BIND_HOST", "127.0.0.1").strip()
            if not bind_host:
                raise ValueError("MCP_BIND_HOST cannot be empty")

            origins = tuple(
                origin.strip()
                for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost").split(",")
                if origin.strip()
            )
            if "*" in origins:
                raise ValueError("Wildcard CORS origins are not allowed")

            allowed_hosts = tuple(
                host.strip()
                for host in os.getenv(
                    "MCP_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]"
                ).split(",")
                if host.strip()
            )
            if not allowed_hosts or "*" in allowed_hosts:
                raise ValueError("MCP_ALLOWED_HOSTS must contain explicit hosts and cannot use '*'")
            if bind_host not in {"0.0.0.0", "::"} and bind_host not in allowed_hosts:
                allowed_hosts = (*allowed_hosts, bind_host)

            output_path = os.getenv("OUTPUT_PATH", "/app/output/ha-ai-context.md")
            return cls(
                ha_url=os.getenv("HA_URL", "http://homeassistant:8123"),
                ha_token=os.getenv("HA_TOKEN", ""),
                ha_config_path=os.getenv("HA_CONFIG_PATH", "/config"),
                health_check_port=int(os.getenv("HEALTH_CHECK_PORT", "9091")),
                mcp_port=int(os.getenv("MCP_PORT", "9092")),
                rest_api_port=int(os.getenv("REST_API_PORT", "9093")),
                mcp_transport=transport,  # type: ignore[arg-type]
                rest_api_enabled=_env_bool("REST_API_ENABLED", False),
                dev_tools_enabled=_env_bool("MCP_DEV_TOOLS_ENABLED", False),
                run_tests_on_startup=_env_bool("RUN_TESTS_ON_STARTUP", False),
                health_server_enabled=_env_bool("HEALTH_SERVER_ENABLED", transport != "stdio"),
                backend_required_for_ready=_env_bool("HA_BACKEND_REQUIRED_FOR_READY", False),
                mcp_bind_host=bind_host,
                mcp_auth_token=os.getenv("MCP_AUTH_TOKEN", ""),
                rest_api_token=os.getenv("REST_API_TOKEN", os.getenv("MCP_AUTH_TOKEN", "")),
                cors_allowed_origins=origins,
                mcp_allowed_hosts=allowed_hosts,
                mcp_http_max_body_bytes=_env_int(
                    "MCP_HTTP_MAX_BODY_BYTES", 1024 * 1024, minimum=1024, maximum=16 * 1024 * 1024
                ),
                mcp_http_max_header_bytes=_env_int(
                    "MCP_HTTP_MAX_HEADER_BYTES", 64 * 1024, minimum=4096, maximum=1024 * 1024
                ),
                mcp_http_connection_limit=_env_int(
                    "MCP_HTTP_CONNECTION_LIMIT", 64, minimum=1, maximum=10_000
                ),
                mcp_http_keepalive_seconds=_env_int(
                    "MCP_HTTP_KEEPALIVE_SECONDS", 5, minimum=1, maximum=300
                ),
                mcp_http_stateless=_env_bool("MCP_HTTP_STATELESS", True),
                output_path=output_path,
                context_output_root=os.getenv("CONTEXT_OUTPUT_ROOT", str(Path(output_path).parent)),
                log_level=os.getenv("LOG_LEVEL", "INFO"),
            )


    SETTINGS = RuntimeSettings.from_env()
    ''',
)

replace_once(
    "tools/constants.py",
    "CORS_ALLOWED_ORIGINS = list(SETTINGS.cors_allowed_origins)\n",
    "CORS_ALLOWED_ORIGINS = list(SETTINGS.cors_allowed_origins)\n"
    "MCP_ALLOWED_HOSTS = list(SETTINGS.mcp_allowed_hosts)\n"
    "MCP_HTTP_MAX_BODY_BYTES = SETTINGS.mcp_http_max_body_bytes\n"
    "MCP_HTTP_MAX_HEADER_BYTES = SETTINGS.mcp_http_max_header_bytes\n"
    "MCP_HTTP_CONNECTION_LIMIT = SETTINGS.mcp_http_connection_limit\n"
    "MCP_HTTP_KEEPALIVE_SECONDS = SETTINGS.mcp_http_keepalive_seconds\n"
    "MCP_HTTP_STATELESS = SETTINGS.mcp_http_stateless\n",
)

# ---------------------------------------------------------------------------
# Operation boundary: redaction + blocking coroutine routing
# ---------------------------------------------------------------------------
replace_once(
    "tools/operations.py",
    "from tools.observability import increment_invocation, start_tool_context\nfrom tools.utils import build_meta\n",
    "from tools.observability import increment_invocation, start_tool_context\n"
    "from tools.redaction import sanitize_response_data\n"
    "from tools.utils import build_meta\n",
)
replace_once(
    "tools/operations.py",
    "\n\n@dataclass(frozen=True, slots=True)\nclass Operation:",
    "\n\n_BLOCKING_COROUTINE_MODULES = frozenset({\"tools.storage\"})\n\n\n"
    "@dataclass(frozen=True, slots=True)\nclass Operation:",
)
old_async_wrap = '''        if inspect.iscoroutinefunction(raw_fn):

            @functools.wraps(raw_fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.monotonic()
                start_tool_context()
                increment_invocation(name)
                result = await KERNEL.invoke_async(name, raw_fn, *args, **kwargs)
                return _augment_result(result, name, start)

            async_wrapper.__doc__ = description
            return async_wrapper
'''
new_async_wrap = '''        if inspect.iscoroutinefunction(raw_fn):
            blocking_coroutine = raw_fn.__module__ in _BLOCKING_COROUTINE_MODULES

            @functools.wraps(raw_fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.monotonic()
                start_tool_context()
                increment_invocation(name)
                if blocking_coroutine:
                    result = await KERNEL.invoke_blocking_coroutine(
                        name, raw_fn, *args, **kwargs
                    )
                else:
                    result = await KERNEL.invoke_async(name, raw_fn, *args, **kwargs)
                return _augment_result(result, name, start)

            async_wrapper.__doc__ = description
            return async_wrapper
'''
replace_once("tools/operations.py", old_async_wrap, new_async_wrap)
regex_once(
    "tools/operations.py",
    r"def _augment_result\(result: Any, tool_name: str, start: float\) -> Any:\n.*?(?=\n\ndef _merged_meta)",
    '''def _augment_result(result: Any, tool_name: str, start: float) -> Any:
    """Sanitize, attach metadata, then enforce the final serialized size limit."""
    import json

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            sanitized_text = sanitize_response_data(result)
            KERNEL.enforce_final_result_size(tool_name, sanitized_text)
            return sanitized_text
        sanitized = sanitize_response_data(parsed)
        if isinstance(sanitized, dict):
            sanitized["_meta"] = _merged_meta(
                sanitized.get("_meta"), build_meta(tool_name, start)
            )
            encoded = json.dumps(sanitized, indent=2, ensure_ascii=False)
            KERNEL.enforce_final_result_size(tool_name, encoded)
            return encoded
        KERNEL.enforce_final_result_size(tool_name, sanitized)
        return sanitized
    if isinstance(result, dict):
        sanitized = sanitize_response_data(result)
        if not isinstance(sanitized, dict):
            raise TypeError("Sanitized dictionary result changed type")
        sanitized["_meta"] = _merged_meta(
            sanitized.get("_meta"), build_meta(tool_name, start)
        )
        KERNEL.enforce_final_result_size(tool_name, sanitized)
        return sanitized
    sanitized = sanitize_response_data(result)
    KERNEL.enforce_final_result_size(tool_name, sanitized)
    return sanitized
''',
)

# ---------------------------------------------------------------------------
# Invocation kernel cancellation and blocking-coroutine executor
# ---------------------------------------------------------------------------
invocation = path("tools/invocation.py").read_text(encoding="utf-8")
helper_marker = "    def invoke_sync(\n"
helper = '''    @staticmethod
    async def _acquire_async_semaphore(
        semaphore: threading.BoundedSemaphore, timeout: float
    ) -> bool:
        """Acquire a threading semaphore without leaking permits on cancellation.

        ``asyncio.to_thread`` cannot cancel an already-running semaphore acquire.
        Shield the inner task so outer cancellation does not mark it cancelled;
        if the abandoned acquire later succeeds, its callback returns the permit.
        """
        acquire_task = asyncio.create_task(asyncio.to_thread(semaphore.acquire, True, timeout))
        try:
            return await asyncio.shield(acquire_task)
        except asyncio.CancelledError:
            def release_abandoned(completed: asyncio.Task[bool]) -> None:
                if completed.cancelled():
                    return
                try:
                    acquired = completed.result()
                except BaseException:
                    return
                if acquired:
                    semaphore.release()

            acquire_task.add_done_callback(release_abandoned)
            raise

'''
if helper not in invocation:
    if helper_marker not in invocation:
        raise RuntimeError("invoke_sync marker missing")
    invocation = invocation.replace(helper_marker, helper + helper_marker, 1)

old_acquire = '''        acquire_task = asyncio.create_task(
            asyncio.to_thread(semaphore.acquire, True, _remaining(deadline))
        )
        try:
            acquired = await acquire_task
        except asyncio.CancelledError:
            # asyncio.to_thread cannot stop the blocking acquire. If it later
            # obtains the permit, release it from the completion callback.
            def release_cancelled_acquire(completed: asyncio.Task[bool]) -> None:
                if (
                    not completed.cancelled()
                    and completed.exception() is None
                    and completed.result()
                ):
                    semaphore.release()

            acquire_task.add_done_callback(release_cancelled_acquire)
            raise
'''
new_acquire = '''        acquired = await self._acquire_async_semaphore(semaphore, _remaining(deadline))
'''
if old_acquire not in invocation:
    raise RuntimeError("old async semaphore acquire block missing")
invocation = invocation.replace(old_acquire, new_acquire, 1)

blocking_method = '''
    async def invoke_blocking_coroutine(
        self,
        tool_name: str,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Run a coroutine containing blocking adapters in the bounded worker pool."""
        manifest = self._manifest(tool_name)
        target = self._authorize(tool_name, manifest)
        timeout = int(manifest["extensions"]["timeout_ms"]) / 1000
        deadline = time.monotonic() + timeout
        concurrency_key = self._concurrency_key(tool_name, manifest, target, args, kwargs)
        semaphore = self._semaphore(concurrency_key, int(manifest["concurrency"]["limit"]))

        acquired = await self._acquire_async_semaphore(semaphore, _remaining(deadline))
        if not acquired:
            raise InvocationError("BUSY", f"Tool '{tool_name}' concurrency limit reached")

        tool_permit_owned = True
        capacity_owned = False
        try:
            remaining = _remaining(deadline)
            if remaining <= 0:
                raise InvocationError("DEADLINE_EXCEEDED", f"Tool '{tool_name}' timed out")
            capacity_owned = await self._acquire_async_semaphore(
                self._executor_capacity, remaining
            )
            if not capacity_owned:
                raise InvocationError("BUSY", "Synchronous invocation queue is full")

            token = _current_deadline.set(deadline)
            try:
                context = contextvars.copy_context()
            finally:
                _current_deadline.reset(token)

            def run_coroutine() -> Any:
                return asyncio.run(function(*args, **kwargs))

            try:
                future = self._executor.submit(context.run, run_coroutine)
            except Exception:
                self._executor_capacity.release()
                capacity_owned = False
                raise

            future.add_done_callback(
                lambda completed: self._release_when_done(
                    completed, semaphore, self._executor_capacity
                )
            )
            tool_permit_owned = False
            capacity_owned = False
            wrapped = asyncio.wrap_future(future)
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(wrapped), timeout=_remaining(deadline)
                )
            except asyncio.TimeoutError as exc:
                future.cancel()
                raise InvocationError(
                    "DEADLINE_EXCEEDED", f"Tool '{tool_name}' timed out"
                ) from exc
            except asyncio.CancelledError:
                future.cancel()
                raise
            self._enforce_result_size(result, int(manifest["max_response_bytes"]))
            return result
        finally:
            if capacity_owned:
                self._executor_capacity.release()
            if tool_permit_owned:
                semaphore.release()

'''
if "async def invoke_blocking_coroutine" not in invocation:
    marker = "\n\nKERNEL = InvocationKernel()"
    if marker not in invocation:
        raise RuntimeError("kernel singleton marker missing")
    invocation = invocation.replace(marker, blocking_method + marker, 1)
path("tools/invocation.py").write_text(invocation, encoding="utf-8")

# ---------------------------------------------------------------------------
# Capability discovery contract
# ---------------------------------------------------------------------------
capabilities = path("tools/capabilities.py").read_text(encoding="utf-8")
capabilities = capabilities.replace(
    "import logging\nimport re\nfrom typing import Any\n",
    "import logging\nimport re\nfrom importlib.metadata import PackageNotFoundError, version as package_version\nfrom typing import Any\n",
    1,
)
capabilities = capabilities.replace(
    "from tools.constants import REST_API_ENABLED\n",
    "from tools.constants import DEV_TOOLS_ENABLED, MCP_TRANSPORT, REST_API_ENABLED\n",
    1,
)
capabilities = capabilities.replace(
    "from tools.utils import _error_response, _success_response\n",
    "from tools.utils import _error_response, _success_response\nfrom version import __version__\n",
    1,
)
capabilities = capabilities.replace('CAPABILITIES_SCHEMA_VERSION = "1.0"', 'CAPABILITIES_SCHEMA_VERSION = "1.1"')
new_capability_fn = '''def _installed_version(distribution: str) -> str:
    try:
        return package_version(distribution)
    except PackageNotFoundError:
        return "unknown"


def _do_describe_ha_capabilities() -> dict[str, Any]:
    """Build supported and active catalogs without contacting external dependencies."""
    supported_manifests = get_all_manifests(active_only=False)
    initialized = active_profile_initialized()
    active_manifests = get_all_manifests(active_only=True) if initialized else {}
    inactive_reasons = get_inactive_reasons() if initialized else {}
    active_names = set(active_manifests)
    tools = []
    for manifest in supported_manifests.values():
        item = dict(manifest)
        name = str(item.get("name", ""))
        runtime_active = name in active_names if initialized else None
        item["runtime_active"] = runtime_active
        if runtime_active is False:
            item["active_state"] = "inactive"
        if name in inactive_reasons:
            item["inactive_reason"] = inactive_reasons[name]
        tools.append(item)
    tools.sort(key=lambda manifest: str(manifest.get("name", "")))

    categories: dict[str, dict[str, Any]] = {}
    for tool in tools:
        name = str(tool.get("name", ""))
        category = _categorize_tool(name)
        bucket = categories.setdefault(category, {"tool_count": 0, "tools": []})
        bucket["tools"].append(
            {
                "name": name,
                "description": str(tool.get("description", "")),
                "active": tool.get("runtime_active"),
                "inactive_reason": tool.get("inactive_reason"),
            }
        )
        bucket["tool_count"] = len(bucket["tools"])

    supported_transports = ["stdio", "streamable-http"]
    active_transport = "stdio" if MCP_TRANSPORT == "stdio" else "streamable-http"
    compatibility_adapters = ["authenticated-rest"] if REST_API_ENABLED else []
    protocol_versions = sorted(
        {
            str(revision)
            for manifest in tools
            for revision in manifest.get("protocol_revisions", [])
            if revision
        }
    )
    active_count = len(active_names) if initialized else len(tools)
    return {
        "schema_version": CAPABILITIES_SCHEMA_VERSION,
        "server": "HA-Observer",
        "server_version": __version__,
        "tools_version": TOOLS_VERSION,
        "sdk": {
            "family": "fastmcp",
            "distribution": "fastmcp",
            "version": _installed_version("fastmcp"),
        },
        "protocol_versions": protocol_versions,
        "supported_transports": supported_transports,
        "active_transports": [active_transport],
        "compatibility_adapters": compatibility_adapters,
        "transports": supported_transports + compatibility_adapters,
        "profile": {
            "mcp_transport": active_transport,
            "dev_tools_enabled": DEV_TOOLS_ENABLED,
            "rest_api_enabled": REST_API_ENABLED,
        },
        "tool_count": active_count,
        "supported_tool_count": len(tools),
        "active_profile_initialized": initialized,
        "active_tool_count": active_count,
        "inactive_tool_count": len(tools) - active_count if initialized else None,
        "supported_component_counts": {"tools": len(tools), "resources": 0, "prompts": 0},
        "active_component_counts": {"tools": active_count, "resources": 0, "prompts": 0},
        "tools": tools,
        "categories": categories,
    }
'''
capabilities, count = re.subn(
    r"def _do_describe_ha_capabilities\(\) -> dict\[str, Any\]:\n.*?(?=\n\ndef register_capability_tools)",
    new_capability_fn,
    capabilities,
    count=1,
    flags=re.DOTALL,
)
if count != 1:
    raise RuntimeError("capability discovery function replacement failed")
path("tools/capabilities.py").write_text(capabilities, encoding="utf-8")

# ---------------------------------------------------------------------------
# REST schema and HTTP hardening in the composition root
# ---------------------------------------------------------------------------
server = path("server.py").read_text(encoding="utf-8")
server = server.replace("from typing import Any, cast, get_type_hints", "from typing import Any, cast", 1)
server = server.replace(
    "from tools.invocation import (\n",
    "from tools.http_security import RequestLimitsMiddleware\nfrom tools.invocation import (\n",
    1,
)
server = server.replace(
    "from tools.security import (\n",
    "from tools.schema_utils import signature_to_json_schema\nfrom tools.security import (\n",
    1,
)
server = server.replace(
    "MCP_BIND_HOST = SETTINGS.mcp_bind_host\n",
    "MCP_BIND_HOST = SETTINGS.mcp_bind_host\n"
    "MCP_ALLOWED_HOSTS = list(SETTINGS.mcp_allowed_hosts)\n"
    "MCP_HTTP_MAX_BODY_BYTES = SETTINGS.mcp_http_max_body_bytes\n"
    "MCP_HTTP_MAX_HEADER_BYTES = SETTINGS.mcp_http_max_header_bytes\n"
    "MCP_HTTP_CONNECTION_LIMIT = SETTINGS.mcp_http_connection_limit\n"
    "MCP_HTTP_KEEPALIVE_SECONDS = SETTINGS.mcp_http_keepalive_seconds\n"
    "MCP_HTTP_STATELESS = SETTINGS.mcp_http_stateless\n",
    1,
)
server, count = re.subn(
    r"def _signature_to_json_schema\(function: Any\) -> dict\[str, Any\]:\n.*?(?=\n\nclass HealthHandler)",
    '''def _signature_to_json_schema(function: Any) -> dict[str, Any]:
    return signature_to_json_schema(function)
''',
    server,
    count=1,
    flags=re.DOTALL,
)
if count != 1:
    raise RuntimeError("server schema helper replacement failed")
server = server.replace(
    "    from starlette.middleware.cors import CORSMiddleware\n",
    "    from starlette.middleware.cors import CORSMiddleware\n"
    "    from starlette.middleware.trustedhost import TrustedHostMiddleware\n",
    1,
)
old_rest_middleware = '''    middleware = [
        Middleware(BearerAuthMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=CORS_ALLOWED_ORIGINS,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["authorization", "content-type"],
        ),
    ]
'''
new_rest_middleware = '''    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=MCP_ALLOWED_HOSTS),
        Middleware(
            RequestLimitsMiddleware,
            max_body_bytes=MCP_HTTP_MAX_BODY_BYTES,
            max_header_bytes=MCP_HTTP_MAX_HEADER_BYTES,
        ),
        Middleware(BearerAuthMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=CORS_ALLOWED_ORIGINS,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["authorization", "content-type"],
        ),
    ]
'''
if old_rest_middleware not in server:
    raise RuntimeError("REST middleware marker missing")
server = server.replace(old_rest_middleware, new_rest_middleware, 1)

run_http = '''
def run_mcp_http(server: FastMCP) -> None:
    """Run the authenticated Streamable HTTP app with explicit transport limits."""
    import uvicorn
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=MCP_ALLOWED_HOSTS),
        Middleware(
            RequestLimitsMiddleware,
            max_body_bytes=MCP_HTTP_MAX_BODY_BYTES,
            max_header_bytes=MCP_HTTP_MAX_HEADER_BYTES,
        ),
        Middleware(
            CORSMiddleware,
            allow_origins=CORS_ALLOWED_ORIGINS,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "authorization",
                "content-type",
                "mcp-protocol-version",
                "mcp-session-id",
            ],
            expose_headers=["mcp-session-id"],
        ),
    ]
    app = server.http_app(
        path="/mcp",
        middleware=middleware,
        stateless_http=MCP_HTTP_STATELESS,
    )
    uvicorn.run(
        app,
        host=MCP_BIND_HOST,
        port=MCP_PORT,
        log_level="warning",
        access_log=False,
        limit_concurrency=MCP_HTTP_CONNECTION_LIMIT,
        timeout_keep_alive=MCP_HTTP_KEEPALIVE_SECONDS,
        h11_max_incomplete_event_size=MCP_HTTP_MAX_HEADER_BYTES,
    )


'''
marker = "def run_rest_api() -> None:\n"
if "def run_mcp_http" not in server:
    if marker not in server:
        raise RuntimeError("run_rest_api marker missing")
    server = server.replace(marker, run_http + marker, 1)
old_run = '''    network_transport = MCP_TRANSPORT
    server.run(
        transport=network_transport,
        host=MCP_BIND_HOST,
        port=MCP_PORT,
        path="/mcp",
        show_banner=False,
    )
'''
new_run = '''    run_mcp_http(server)
'''
if old_run not in server:
    raise RuntimeError("network server.run block missing")
server = server.replace(old_run, new_run, 1)
path("server.py").write_text(server, encoding="utf-8")

# ---------------------------------------------------------------------------
# Official MCP client verifier and workflow
# ---------------------------------------------------------------------------
write(
    "scripts/verify_official_mcp_client.py",
    '''
    #!/usr/bin/env python3
    """Verify HA-MCP with the official modelcontextprotocol Python client SDK."""

    from __future__ import annotations

    import argparse
    import asyncio
    import json
    import os
    from typing import Any

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client


    def _is_error(result: Any) -> bool:
        return bool(getattr(result, "is_error", getattr(result, "isError", False)))


    def _json_payload(result: Any) -> dict[str, Any]:
        content = getattr(result, "content", None)
        if not isinstance(content, list) or not content:
            raise AssertionError(f"tool result has no content: {result!r}")
        text = getattr(content[0], "text", None)
        if not isinstance(text, str):
            raise AssertionError(f"first tool result is not text: {content[0]!r}")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise AssertionError("tool payload is not an object")
        return payload


    async def _verify_session(session: ClientSession) -> None:
        await session.initialize()
        listing = await session.list_tools()
        names = {tool.name for tool in listing.tools}
        if "describe_ha_capabilities" not in names:
            raise AssertionError("capability discovery tool is missing")
        result = await session.call_tool("describe_ha_capabilities", arguments={})
        if _is_error(result):
            raise AssertionError(f"capability discovery failed: {result!r}")
        payload = _json_payload(result)
        if payload.get("success") is not True:
            raise AssertionError(payload)
        expected_version = os.getenv("MCP_EXPECTED_VERSION")
        if expected_version and payload.get("server_version") != expected_version:
            raise AssertionError(
                f"server version mismatch: {payload.get('server_version')} != {expected_version}"
            )
        if payload.get("sdk", {}).get("family") != "fastmcp":
            raise AssertionError(payload.get("sdk"))
        if not payload.get("protocol_versions"):
            raise AssertionError("protocol versions missing from capability discovery")

        failure_seen = False
        try:
            invalid = await session.call_tool(
                "get_entity_state", arguments={"wrong_parameter": "value"}
            )
            failure_seen = _is_error(invalid)
        except Exception:
            failure_seen = True
        if not failure_seen:
            raise AssertionError("invalid tool input did not fail at the protocol boundary")


    def _stdio_env(config_path: str) -> dict[str, str]:
        allowed = {
            "PATH": os.getenv("PATH", ""),
            "LANG": os.getenv("LANG", "C.UTF-8"),
            "LC_ALL": os.getenv("LC_ALL", "C.UTF-8"),
            "MCP_TRANSPORT": "stdio",
            "HEALTH_SERVER_ENABLED": "0",
            "MCP_DEV_TOOLS_ENABLED": "0",
            "HA_CONFIG_PATH": config_path,
            "PYTHONUNBUFFERED": "1",
        }
        return {key: value for key, value in allowed.items() if value}


    async def verify_stdio(command: str, command_args: list[str], config_path: str) -> None:
        params = StdioServerParameters(
            command=command,
            args=command_args,
            env=_stdio_env(config_path),
        )
        async with stdio_client(params) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await _verify_session(session)


    async def verify_http(url: str, token: str) -> None:
        headers = {"Authorization": f"Bearer {token}"}
        try:
            import httpx2 as httpx_client  # type: ignore[import-not-found]
        except ImportError:
            import httpx as httpx_client  # type: ignore[no-redef]

        async with httpx_client.AsyncClient(headers=headers, timeout=20) as http_client:
            try:
                transport = streamable_http_client(url, http_client=http_client)
            except TypeError:
                transport = streamable_http_client(url, headers=headers)  # type: ignore[call-arg]
            async with transport as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await _verify_session(session)


    def main() -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="transport", required=True)
        stdio = subparsers.add_parser("stdio")
        stdio.add_argument("--command", required=True)
        stdio.add_argument("--arg", action="append", default=[])
        stdio.add_argument("--config-path", default="/tmp")
        http = subparsers.add_parser("http")
        http.add_argument("--url", required=True)
        http.add_argument("--token", required=True)
        args = parser.parse_args()

        if args.transport == "stdio":
            asyncio.run(verify_stdio(args.command, list(args.arg), args.config_path))
        else:
            asyncio.run(verify_http(args.url, args.token))


    if __name__ == "__main__":
        main()
    ''',
)

write(
    ".github/workflows/official-client.yml",
    '''
    name: Official MCP client

    on:
      pull_request:
      workflow_dispatch:

    permissions:
      contents: read

    concurrency:
      group: official-mcp-client-${{ github.ref }}
      cancel-in-progress: true

    env:
      PIP_DISABLE_PIP_VERSION_CHECK: "1"
      MCP_EXPECTED_VERSION: "2.0.0"

    jobs:
      official-client:
        name: Official client exact artifacts
        runs-on: ubuntu-24.04
        timeout-minutes: 35
        steps:
          - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v6
            with:
              persist-credentials: false
          - uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6
            with:
              python-version: "3.13"
              cache: pip
          - name: Build exact wheel
            run: |
              python -m pip install -c constraints-ci.txt build==1.5.0 setuptools==83.0.0 wheel==0.47.0
              python -m build --wheel --no-isolation
          - name: Install exact server wheel
            run: |
              python -m venv /tmp/ha-server
              /tmp/ha-server/bin/python -m pip install dist/*.whl
              /tmp/ha-server/bin/python -m pip check
          - name: Install independent official MCP client
            run: |
              python -m venv /tmp/official-client
              /tmp/official-client/bin/python -m pip install 'mcp==2.0.0'
          - name: Official-client stdio verification
            env:
              PYTHONPATH: ${{ github.workspace }}
            run: |
              /tmp/official-client/bin/python scripts/verify_official_mcp_client.py stdio \
                --command /tmp/ha-server/bin/ha-mcp-readonly --config-path /tmp
          - name: Build exact container from the tested wheel
            run: docker build -f Dockerfile.release -t ha-mcp-readonly:official-${GITHUB_SHA} .
          - name: Start hardened Streamable HTTP container
            run: |
              mkdir -p /tmp/ha-config/.storage
              printf 'homeassistant:\n  name: CI Home\n' > /tmp/ha-config/configuration.yaml
              docker run -d --rm --name ha-mcp-official \
                --read-only --cap-drop ALL --security-opt no-new-privileges \
                --tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777 \
                -v /tmp/ha-config:/config:ro \
                -p 127.0.0.1:9092:9092 \
                -e MCP_TRANSPORT=http -e MCP_BIND_HOST=0.0.0.0 \
                -e MCP_ALLOWED_HOSTS=127.0.0.1,localhost \
                -e MCP_HTTP_STATELESS=1 \
                -e MCP_AUTH_TOKEN=official-client-ci-token \
                -e HA_URL=http://unreachable.invalid:8123 -e HA_TOKEN=ci-backend-token \
                -e HA_CONFIG_PATH=/config \
                ha-mcp-readonly:official-${GITHUB_SHA}
              for _ in $(seq 1 100); do
                if python - <<'PY'
              import socket
              try:
                  with socket.create_connection(('127.0.0.1', 9092), timeout=.2):
                      pass
              except OSError:
                  raise SystemExit(1)
              PY
                then break; fi
                sleep .2
              done
          - name: Official-client Streamable HTTP verification
            env:
              PYTHONPATH: ${{ github.workspace }}
            run: |
              /tmp/official-client/bin/python scripts/verify_official_mcp_client.py http \
                --url http://127.0.0.1:9092/mcp --token official-client-ci-token
          - name: Verify HTTP hardening boundaries
            run: |
              python - <<'PY'
              import json
              import urllib.error
              import urllib.request

              url = 'http://127.0.0.1:9092/mcp'
              request = urllib.request.Request(
                  url,
                  data=json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize'}).encode(),
                  headers={'Content-Type':'application/json'},
                  method='POST',
              )
              try:
                  urllib.request.urlopen(request, timeout=5)
              except urllib.error.HTTPError as exc:
                  assert exc.code == 401, exc.code
              else:
                  raise AssertionError('unauthenticated MCP request was accepted')

              oversized = urllib.request.Request(
                  url,
                  data=b'x' * (1024 * 1024 + 1),
                  headers={
                      'Authorization':'Bearer official-client-ci-token',
                      'Content-Type':'application/json',
                  },
                  method='POST',
              )
              try:
                  urllib.request.urlopen(oversized, timeout=5)
              except urllib.error.HTTPError as exc:
                  assert exc.code == 413, exc.code
              else:
                  raise AssertionError('oversized MCP request was accepted')
              PY
          - name: Container diagnostics
            if: always()
            run: docker logs ha-mcp-official || true
          - name: Stop container
            if: always()
            run: docker stop ha-mcp-official || true
    ''',
)

# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------
write(
    "tests/unit/test_response_redaction_boundary.py",
    '''
    """Regression tests for final model-visible response redaction."""

    import json

    from tools.manifests import get_all_manifests, make_manifest, register_manifest, set_active_tools
    from tools.operations import OperationRegistry
    from tools.redaction import REDACTED, sanitize_response_data


    def _register(name: str) -> None:
        manifest = make_manifest(name)
        manifest["extensions"]["target_binding"] = {
            "kind": "deployment-resource",
            "target": "runtime",
            "revalidation": "per-invocation",
        }
        active = set(get_all_manifests(active_only=True))
        register_manifest(name, manifest)
        set_active_tools(active | {name})


    def test_key_aware_redaction_removes_plain_secret_values() -> None:
        payload = sanitize_response_data(
            {
                "password": "plain-secret",
                "nested": {
                    "access_token": "token-value",
                    "client_secret": "client-value",
                    "safe_name": "living room",
                },
            }
        )
        assert payload["password"] == REDACTED
        assert payload["nested"]["access_token"] == REDACTED
        assert payload["nested"]["client_secret"] == REDACTED
        assert payload["nested"]["safe_name"] == "living room"


    def test_operation_boundary_redacts_config_entry_credentials() -> None:
        name = "test_final_redaction_boundary"
        _register(name)

        def raw_tool() -> str:
            return json.dumps(
                {
                    "success": True,
                    "entries": [
                        {
                            "entry_id": "safe-id",
                            "options": {
                                "password": "supersecret",
                                "refresh_token": "refresh-secret",
                                "label": "safe-value",
                            },
                        }
                    ],
                }
            )

        registry = OperationRegistry()
        operation = registry.register(name, raw_tool)
        result = json.loads(operation.fn())
        options = result["entries"][0]["options"]
        assert options["password"] == REDACTED
        assert options["refresh_token"] == REDACTED
        assert options["label"] == "safe-value"
        assert "supersecret" not in json.dumps(result)
        assert "refresh-secret" not in json.dumps(result)
    ''',
)

write(
    "tests/unit/test_schema_utils.py",
    '''
    """JSON-schema generation must preserve modern Python type semantics."""

    from tools.schema_utils import signature_to_json_schema


    def test_optional_and_generic_types_are_not_flattened_to_strings() -> None:
        def operation(name: str | None = None, values: list[int] | None = None) -> None:
            return None

        schema = signature_to_json_schema(operation)
        name_schema = schema["properties"]["name"]
        values_schema = schema["properties"]["values"]
        assert {item.get("type") for item in name_schema["anyOf"]} == {"string", "null"}
        arrays = [item for item in values_schema["anyOf"] if item.get("type") == "array"]
        assert arrays and arrays[0]["items"]["type"] == "integer"
        assert name_schema["default"] is None
        assert values_schema["default"] is None
        assert schema["additionalProperties"] is False
    ''',
)

write(
    "tests/unit/test_http_security.py",
    '''
    """ASGI HTTP request limit regressions."""

    import asyncio

    from tools.http_security import RequestLimitsMiddleware


    def _run(body_chunks: list[bytes], headers: list[tuple[bytes, bytes]], *, body_limit: int = 8, header_limit: int = 64) -> list[dict]:
        messages = [
            {
                "type": "http.request",
                "body": chunk,
                "more_body": index < len(body_chunks) - 1,
            }
            for index, chunk in enumerate(body_chunks)
        ]
        sent: list[dict] = []

        async def app(scope, receive, send):
            while True:
                message = await receive()
                if not message.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message)

        middleware = RequestLimitsMiddleware(
            app, max_body_bytes=body_limit, max_header_bytes=header_limit
        )
        asyncio.run(
            middleware(
                {"type": "http", "method": "POST", "path": "/", "headers": headers},
                receive,
                send,
            )
        )
        return sent


    def test_streamed_body_limit_returns_413() -> None:
        sent = _run([b"12345", b"6789"], [(b"host", b"localhost")])
        assert sent[0]["status"] == 413


    def test_aggregate_header_limit_returns_431() -> None:
        sent = _run([b"{}"], [(b"x-long", b"a" * 80)])
        assert sent[0]["status"] == 431
    ''',
)

write(
    "tests/unit/test_operation_async_hardening.py",
    '''
    """Operation registry hardening for blocking coroutine adapters."""

    import asyncio
    import json
    import time

    import pytest

    from tools.manifests import get_all_manifests, make_manifest, register_manifest, set_active_tools
    from tools.operations import OperationRegistry


    def _register(name: str) -> None:
        manifest = make_manifest(name, timeout_ms=1000)
        manifest["extensions"]["target_binding"] = {
            "kind": "deployment-resource",
            "target": "runtime",
            "revalidation": "per-invocation",
        }
        active = set(get_all_manifests(active_only=True))
        register_manifest(name, manifest)
        set_active_tools(active | {name})


    @pytest.mark.asyncio
    async def test_storage_coroutine_blocking_io_does_not_block_server_event_loop() -> None:
        name = "test_blocking_storage_coroutine"
        _register(name)

        async def blocking_tool() -> str:
            time.sleep(0.2)
            return json.dumps({"success": True})

        blocking_tool.__module__ = "tools.storage"
        registry = OperationRegistry()
        operation = registry.register(name, blocking_tool)
        started = time.monotonic()
        invocation = asyncio.create_task(operation.fn())
        await asyncio.sleep(0.03)
        elapsed = time.monotonic() - started
        assert elapsed < 0.12, "blocking adapter stalled the shared event loop"
        result = json.loads(await invocation)
        assert result["success"] is True
    ''',
)

# Extend the existing invocation kernel tests with the exact admission-cancellation regression.
invocation_tests = path("tests/unit/test_invocation_kernel.py").read_text(encoding="utf-8")
if "test_cancel_while_waiting_for_permit_does_not_leak_capacity" not in invocation_tests:
    invocation_tests += '''

@pytest.mark.asyncio
async def test_cancel_while_waiting_for_permit_does_not_leak_capacity() -> None:
    """Cancelling admission must release a permit acquired later by the worker thread."""
    import asyncio

    name = "test_kernel_cancelled_admission"
    _register(
        name,
        extensions={**make_manifest(name)["extensions"], "timeout_ms": 1000},
        concurrency={"scope": "capability", "limit": 1, "queue_limit": 1},
    )
    kernel = InvocationKernel(max_workers=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> str:
        started.set()
        await release.wait()
        return "held"

    first = asyncio.create_task(kernel.invoke_async(name, hold))
    await asyncio.wait_for(started.wait(), timeout=1)
    waiting = asyncio.create_task(kernel.invoke_async(name, _async_ok))
    await asyncio.sleep(0.03)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    release.set()
    assert await asyncio.wait_for(first, timeout=1) == "held"
    await asyncio.sleep(0.05)
    assert await asyncio.wait_for(kernel.invoke_async(name, _async_ok), timeout=1) == "ok"
'''
path("tests/unit/test_invocation_kernel.py").write_text(invocation_tests, encoding="utf-8")

write(
    "tests/unit/test_version_contract.py",
    '''
    """Project and capability versions must remain one coherent public contract."""

    import json
    from pathlib import Path

    from version import __version__


    def test_every_static_capability_manifest_matches_project_version() -> None:
        manifests = json.loads(Path("tools/tool_manifests.json").read_text(encoding="utf-8"))
        stale = {
            name: manifest.get("extensions", {}).get("tool_version")
            for name, manifest in manifests.items()
            if manifest.get("extensions", {}).get("tool_version") != __version__
        }
        assert stale == {}
    ''',
)

write(
    "tests/protocol/test_capability_discovery_contract.py",
    '''
    """Protocol-visible capability discovery identity and profile contract."""

    import json

    from fastmcp import Client

    import server
    from version import __version__


    async def test_capability_discovery_reports_server_sdk_protocol_and_profiles() -> None:
        async with Client(server.create_mcp_server()) as client:
            result = await client.call_tool("describe_ha_capabilities", {})
        assert not result.is_error
        payload = json.loads(result.content[0].text)
        assert payload["server_version"] == __version__
        assert payload["tools_version"] == __version__
        assert payload["sdk"]["family"] == "fastmcp"
        assert payload["protocol_versions"]
        assert payload["supported_transports"] == ["stdio", "streamable-http"]
        assert payload["active_transports"]
        assert payload["supported_component_counts"]["tools"] == payload["supported_tool_count"]
        assert payload["active_component_counts"]["tools"] == payload["active_tool_count"]
    ''',
)

# Add a migration evidence generator that can be used after the exact SHA exists.
write(
    "scripts/migration_assessment_brief.py",
    '''
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
    ''',
)

# Remove the one-shot implementation scaffolding before the workflow creates the final commit.
path(".github/apply_review_fixes.py").unlink(missing_ok=True)
path(".github/workflows/apply-review-fixes.yml").unlink(missing_ok=True)
