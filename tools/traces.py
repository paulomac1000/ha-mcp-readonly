"""Authorized execution-evidence reader for automation and script traces.

Exposes ``get_automation_execution_evidence``, a read-only capability that
talks exclusively to the official authenticated Home Assistant WebSocket
trace API (``trace/list`` and ``trace/get``) plus ``config/get`` for
instance identity. An administrator long-lived access token is required;
non-administrative authority fails closed with ``TRACE_UNAUTHORIZED``.
The response is a small redacted projection: the full raw trace,
automation/script configuration, and private variables are never returned.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from tools.utils import _error_response, _success_response
from tools.ws_transport import (
    HaWebSocketSession,
    TransportLimits,
    WebSocketAuthError,
    WebSocketOversizedError,
    WebSocketRemoteError,
    WebSocketTransportError,
)

_logger = logging.getLogger(__name__)

TRACE_DOMAINS = frozenset({"automation", "script"})
MIN_HOURS = 1
MAX_HOURS = 168
DEFAULT_HOURS = 24
MIN_LIMIT = 1
MAX_LIMIT = 20
DEFAULT_LIMIT = 5
MAX_TRACE_LIST_ENTRIES = 500
MAX_CHANGED_VARIABLE_ENTRIES = 10
MAX_ACTIONS = 20
MAX_ERRORS = 10
MAX_TRIGGER_CHARS = 1024
SESSION_DEADLINE_S = 20.0
COMMAND_TIMEOUT_S = 10.0


def _now_utc() -> datetime:
    """Return the current UTC instant; isolated for deterministic tests."""
    return datetime.now(UTC)


def _open_session(ha_url: str, ha_token: str) -> HaWebSocketSession:
    """Open one bounded authenticated WebSocket session.

    Args:
        ha_url: Home Assistant base URL.
        ha_token: Administrator long-lived access token.

    Returns:
        The authenticated session.
    """
    return HaWebSocketSession.connect(
        ha_url,
        ha_token,
        limits=TransportLimits(
            command_timeout_s=COMMAND_TIMEOUT_S,
            session_deadline_s=SESSION_DEADLINE_S,
        ),
    )


def _error(code: str, message: str, retryable: bool = False, suggestion: str | None = None) -> str:
    """Serialize a structured, machine-classifiable failure."""
    payload: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    if suggestion:
        payload["suggestion"] = suggestion
    return _error_response(payload)


def _classify_transport_error(exc: WebSocketTransportError) -> str:
    """Map a typed transport failure onto the trace error taxonomy."""
    if isinstance(exc, WebSocketAuthError):
        return "TRACE_UNAUTHORIZED"
    if isinstance(exc, WebSocketRemoteError):
        if exc.remote_code == "unauthorized":
            return "TRACE_UNAUTHORIZED"
        return "TRACE_UNAVAILABLE"
    if isinstance(exc, WebSocketOversizedError):
        return "TRACE_OVERSIZED"
    return "TRACE_UNAVAILABLE"


def _parse_ts(value: Any) -> datetime | None:
    """Parse a timezone-aware ISO timestamp, or return None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _validate(
    domain: Any, item_id: Any, run_id: Any, hours: Any, limit: Any
) -> tuple[str, str, str | None, int, int] | str:
    """Validate inputs at the boundary and return canonical values or an error.

    Args:
        domain: Requested trace domain.
        item_id: Requested exact configuration identifier.
        run_id: Optional exact run identifier.
        hours: Requested lookback window in hours.
        limit: Requested maximum number of runs.

    Returns:
        Tuple of canonical (domain, item_id, run_id, hours, limit) on success,
        or a serialized error string.
    """
    if domain not in TRACE_DOMAINS:
        return _error(
            "INVALID_ARGUMENTS",
            "domain must be 'automation' or 'script'",
            suggestion="use domain='automation' or domain='script'",
        )
    if not isinstance(item_id, str) or not item_id.strip():
        return _error("INVALID_ARGUMENTS", "item_id must be a non-empty string")
    if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
        return _error("INVALID_ARGUMENTS", "run_id must be a non-empty string when provided")
    if isinstance(hours, bool) or not isinstance(hours, int) or not MIN_HOURS <= hours <= MAX_HOURS:
        return _error(
            "INVALID_ARGUMENTS",
            f"hours must be an integer between {MIN_HOURS} and {MAX_HOURS}",
        )
    if isinstance(limit, bool) or not isinstance(limit, int) or not MIN_LIMIT <= limit <= MAX_LIMIT:
        return _error(
            "INVALID_ARGUMENTS",
            f"limit must be an integer between {MIN_LIMIT} and {MAX_LIMIT}",
        )
    return (
        str(domain),
        item_id.strip(),
        run_id.strip() if isinstance(run_id, str) else None,
        int(hours),
        int(limit),
    )


def _bounded_trigger(trigger: Any) -> Any:
    """Bound the trigger summary to a small serialized projection."""
    if isinstance(trigger, str):
        return trigger
    try:
        serialized = json.dumps(trigger, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return {"summary": "<unserializable>", "truncated": True}
    if len(serialized) <= MAX_TRIGGER_CHARS:
        return trigger
    return {"summary": serialized[:MAX_TRIGGER_CHARS], "truncated": True}


def _summarize_trace_nodes(detail: dict[str, Any], evidence: dict[str, Any]) -> None:
    """Extract bounded changed-variable, action, and error summaries.

    Args:
        detail: The ``trace/get`` result payload.
        evidence: The projection dict mutated in place with bounded summaries.
    """
    trace_nodes = detail.get("trace")
    nodes: dict[str, Any] = trace_nodes if isinstance(trace_nodes, dict) else {}

    changed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    actions: list[str] = []
    truncated_variables = False
    for node in sorted(nodes):
        entries = nodes[node]
        if isinstance(node, str) and node.startswith("action/"):
            actions.append(node)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            error = entry.get("error")
            if error is not None:
                errors.append({"node": node, "error": str(error)})
            variables = entry.get("changed_variables")
            if isinstance(variables, dict):
                for name in sorted(variables):
                    if len(changed) >= MAX_CHANGED_VARIABLE_ENTRIES:
                        truncated_variables = True
                        break
                    changed.append({"node": node, "variable": name, "value": variables[name]})
                if truncated_variables:
                    break
        if truncated_variables:
            break
    if truncated_variables and changed:
        changed[-1]["truncated"] = True

    evidence["changed_variables"] = changed
    evidence["actions"] = actions[:MAX_ACTIONS]
    evidence["actions_truncated"] = len(actions) > MAX_ACTIONS
    evidence["errors"] = errors[:MAX_ERRORS]
    evidence["errors_truncated"] = len(errors) > MAX_ERRORS


def _project_evidence(domain: str, item_id: str, detail: dict[str, Any]) -> dict[str, Any]:
    """Build the redacted per-run projection from one ``trace/get`` result.

    Args:
        domain: Canonical trace domain.
        item_id: Exact configuration identifier.
        detail: The ``trace/get`` result payload.

    Returns:
        A bounded projection without raw trace, config, or variable dumps.
    """
    timestamp = detail.get("timestamp")
    if isinstance(timestamp, dict):
        start = timestamp.get("start")
        finish = timestamp.get("finish")
    else:
        start = timestamp if isinstance(timestamp, str) else None
        finish = start
    context = detail.get("context")
    context_ids = (
        {
            "id": context.get("id"),
            "parent_id": context.get("parent_id"),
            "user_id": context.get("user_id"),
        }
        if isinstance(context, dict)
        else {"id": None, "parent_id": None, "user_id": None}
    )
    evidence: dict[str, Any] = {
        "domain": domain,
        "item_id": item_id,
        "run_id": detail.get("run_id"),
        "state": detail.get("state"),
        "script_execution": detail.get("script_execution"),
        "last_step": detail.get("last_step"),
        "timestamp": {"start": start, "finish": finish},
        "context": context_ids,
        "trigger": _bounded_trigger(detail.get("trigger")),
    }
    _summarize_trace_nodes(detail, evidence)
    return evidence


def _do_get_automation_execution_evidence(
    domain: str,
    item_id: str,
    run_id: str | None,
    hours: int,
    limit: int,
    ha_url: str,
    ha_token: str,
) -> str:
    """Collect bounded execution evidence for one automation or script item.

    Args:
        domain: Trace domain, 'automation' or 'script'.
        item_id: Exact configuration identifier.
        run_id: Optional exact run identifier; omitted selects recent runs.
        hours: Lookback window in hours (1-168).
        limit: Maximum number of selected runs (1-20).
        ha_url: Home Assistant base URL.
        ha_token: Administrator long-lived access token.

    Returns:
        JSON string with a success flag, the redacted projection, and a
        stable evidence digest, or a structured classified error.
    """
    if not ha_url or not ha_token:
        return _error(
            "CONFIG_ERROR",
            "HA_URL and HA_TOKEN must be configured",
            suggestion="configure an administrator long-lived access token",
        )
    validated = _validate(domain, item_id, run_id, hours, limit)
    if isinstance(validated, str):
        return validated
    domain, item_id, run_id, hours, limit = validated

    now = _now_utc()
    window_start = now - timedelta(hours=hours)
    session: HaWebSocketSession | None = None
    try:
        try:
            session = _open_session(ha_url, ha_token)
            config_result = session.request("config/get")
            trace_list = session.request("trace/list", {"domain": domain})
        except WebSocketTransportError as exc:
            code = _classify_transport_error(exc)
            return _error(
                code,
                "Home Assistant rejected the trace request",
                suggestion=(
                    "verify the token belongs to an administrator user"
                    if code == "TRACE_UNAUTHORIZED"
                    else "verify Home Assistant connectivity and retry"
                ),
            )
        finally:
            if session is not None:
                session.close()

        if (
            not isinstance(config_result, dict)
            or not isinstance(config_result.get("version"), str)
            or not config_result.get("version")
        ):
            return _error("TRACE_MALFORMED", "config/get returned an unexpected shape")
        ha_version = str(config_result["version"])

        if not isinstance(trace_list, list):
            return _error("TRACE_MALFORMED", "trace/list returned an unexpected shape")
        if len(trace_list) > MAX_TRACE_LIST_ENTRIES:
            return _error(
                "TRACE_OVERSIZED",
                f"trace/list exceeds the {MAX_TRACE_LIST_ENTRIES}-entry bound",
            )
        matching: list[dict[str, Any]] = []
        for entry in trace_list:
            if not isinstance(entry, dict):
                return _error("TRACE_MALFORMED", "trace/list entry is not an object")
            entry_run_id = entry.get("run_id")
            entry_item_id = entry.get("item_id")
            entry_domain = entry.get("domain")
            if not isinstance(entry_run_id, str) or not entry_run_id:
                return _error("TRACE_MALFORMED", "trace/list entry lacks a run_id")
            if not isinstance(entry_item_id, str) or not isinstance(entry_domain, str):
                return _error("TRACE_MALFORMED", "trace/list entry lacks identity fields")
            if entry_item_id != item_id or entry_domain != domain:
                continue
            matching.append(entry)

        selected: list[dict[str, Any]]
        if run_id is not None:
            selected = [entry for entry in matching if entry["run_id"] == run_id]
            if not selected:
                return _error(
                    "TRACE_NOT_RETAINED",
                    f"run {run_id!r} is not retained for {domain}.{item_id}",
                    suggestion="omit run_id to list recently retained runs",
                )
        else:
            in_window: list[tuple[datetime, str, dict[str, Any]]] = []
            for entry in matching:
                parsed = _parse_ts(entry.get("timestamp"))
                if parsed is None or parsed < window_start:
                    continue
                in_window.append((parsed, entry["run_id"], entry))
            in_window.sort(key=lambda item: (item[0], item[1]), reverse=True)
            selected = [entry for _ts, _rid, entry in in_window[:limit]]
            if not selected:
                return _error(
                    "TRACE_NOT_RETAINED",
                    f"no {domain} runs for {item_id!r} are retained in the last {hours} hours",
                    suggestion=(
                        "traces are retained only for recent executions; widen "
                        "the window or trigger the automation once"
                    ),
                )

        evidence_items: list[dict[str, Any]] = []
        skipped = 0
        first_failure: str | None = None
        if session is None:
            return _error("TRACE_UNAVAILABLE", "Home Assistant session was closed")
        for entry in selected:
            run_id_value = entry["run_id"]
            try:
                detail_raw = session.request(
                    "trace/get", {"domain": domain, "item_id": item_id, "run_id": run_id_value}
                )
            except WebSocketTransportError as exc:
                code = _classify_transport_error(exc)
                if run_id is not None:
                    return _error(
                        code,
                        f"trace/get for run {run_id_value!r} failed",
                        suggestion=(
                            "verify the token belongs to an administrator user"
                            if code == "TRACE_UNAUTHORIZED"
                            else "verify Home Assistant connectivity and retry"
                        ),
                    )
                skipped += 1
                if first_failure is None:
                    first_failure = code
                continue
            if not isinstance(detail_raw, dict) or detail_raw.get("run_id") != run_id_value:
                code = "TRACE_MALFORMED"
                if run_id is not None:
                    return _error(
                        code,
                        f"trace/get for run {run_id_value!r} returned an unexpected shape",
                    )
                skipped += 1
                if first_failure is None:
                    first_failure = code
                continue
            evidence_items.append(_project_evidence(domain, item_id, detail_raw))

        if not evidence_items:
            code = first_failure or "TRACE_UNAVAILABLE"
            return _error(
                code,
                "no selected trace could be retrieved",
                suggestion=(
                    "verify the token belongs to an administrator user"
                    if code == "TRACE_UNAUTHORIZED"
                    else "verify Home Assistant connectivity and retry"
                ),
            )

        observed_windows = [
            evidence["timestamp"]["start"]
            for evidence in evidence_items
            if evidence["timestamp"]["start"]
        ]
        response: dict[str, Any] = {
            "source": {
                "kind": "HA_WEBSOCKET_TRACE_API",
                "endpoints": ["config/get", "trace/list", "trace/get"],
            },
            "authority": {
                "required_scope": "administrator",
                "llat_authenticated": True,
                "note": (
                    "requires an administrator long-lived access token; "
                    "non-administrative authority fails closed"
                ),
            },
            "domain": domain,
            "item_id": item_id,
            "selected_run_ids": [evidence["run_id"] for evidence in evidence_items],
            "requested_utc_window": {
                "start_utc": window_start.isoformat(),
                "end_utc": now.isoformat(),
                "hours": hours,
            },
            "covered_utc_window": {
                "start_utc": min(observed_windows) if observed_windows else None,
                "end_utc": max(observed_windows) if observed_windows else None,
                "observed_runs": len(evidence_items),
                "completeness": "RETAINED_TRACES_ONLY",
            },
            "ha_version": ha_version,
            "retention": {
                "source": "HA_TRACE_STORAGE",
                "completeness": "RETAINED_TRACES_ONLY",
                "matching_runs_in_window": len(matching)
                if run_id is not None
                else sum(
                    1
                    for entry in matching
                    if (_ts := _parse_ts(entry.get("timestamp"))) is not None
                    and _ts >= window_start
                ),
                "omitted_runs": max(
                    0,
                    (
                        len(matching)
                        if run_id is not None
                        else sum(
                            1
                            for entry in matching
                            if (_ts := _parse_ts(entry.get("timestamp"))) is not None
                            and _ts >= window_start
                        )
                    )
                    - len(evidence_items),
                ),
                "skipped_unavailable_runs": skipped,
            },
            "evidence": evidence_items,
        }
        if run_id is not None:
            response["run_id"] = run_id
        response["evidence_digest"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    response, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
            ).hexdigest()
        )
        return _success_response(response)
    except Exception as exc:
        _logger.warning("trace evidence collection failed: %s", type(exc).__name__)
        return _error("TRACE_UNAVAILABLE", f"trace collection failed: {type(exc).__name__}")


def register_traces_tools(mcp: Any, ha_url: str, ha_token: str) -> None:
    """Register the trace evidence tool on the MCP server.

    Args:
        mcp: MCP operation adapter exposing the ``tool`` decorator.
        ha_url: Home Assistant base URL.
        ha_token: Home Assistant access token.
    """

    @mcp.tool()
    async def get_automation_execution_evidence(
        domain: str,
        item_id: str,
        run_id: str | None = None,
        hours: int = DEFAULT_HOURS,
        limit: int = DEFAULT_LIMIT,
    ) -> str:
        """[READ] Retrieve bounded execution evidence for an automation or script.

        Uses only the official authenticated Home Assistant WebSocket trace
        API. Requires an administrator long-lived access token; lower
        authority fails closed. Returns a small redacted projection per
        retained run (state, timestamps, context IDs, trigger, bounded
        changed-variable/action/error summaries) plus a stable evidence
        digest. The raw trace, automation/script configuration, and private
        variables are never returned.

        Args:
            domain: Trace domain, 'automation' or 'script'.
            item_id: Exact configuration identifier of the automation or script.
            run_id: Optional exact run identifier; omit to select the most
                recent retained runs inside the requested window.
            hours: Lookback window in hours (1-168, default 24).
            limit: Maximum number of runs returned (1-20, default 5).

        Returns:
            JSON string with a success flag, requested and covered UTC
            windows, HA version, retention and completeness, per-run
            evidence, and a stable evidence digest. Missing traces fail
            with TRACE_NOT_RETAINED or TRACE_UNAVAILABLE, never as proof
            that no event happened.
        """
        try:
            return _do_get_automation_execution_evidence(
                domain, item_id, run_id, hours, limit, ha_url, ha_token
            )
        except Exception as e:
            return _error_response(str(e))
