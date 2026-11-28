"""Tests for the authorized execution-evidence trace reader (tools/traces.py)."""

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from tools.traces import (
    MAX_CHANGED_VARIABLE_ENTRIES,
    MAX_ERRORS,
    register_traces_tools,
)
from tools.ws_transport import (
    WebSocketAuthError,
    WebSocketDeadlineError,
    WebSocketOversizedError,
    WebSocketRemoteError,
)

NOW = datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC)
HA_VERSION = "2026.9.1"
ITEM_ID = "evening_lights"


def _summary(run_id: str, timestamp: str, state: str = "stopped") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "item_id": ITEM_ID,
        "domain": "automation",
        "state": state,
        "script_execution": None,
        "timestamp": timestamp,
        "trigger": "time",
        "last_step": "action/0",
    }


def _detail(run_id: str, variables: int = 2) -> dict[str, Any]:
    trace_nodes: dict[str, Any] = {
        "trigger/0": [
            {"changed_variables": {"trigger": "time"}, "error": None},
        ],
        "action/0": [
            {"changed_variables": {}, "error": None},
        ],
        "action/1": [
            {
                "changed_variables": {
                    f"var_{index}": f"value-{index}" for index in range(variables)
                },
                "error": "service call failed",
            },
        ],
    }
    return {
        "blueprint_inputs": {"secret_field": "should-not-leak"},
        "config": {"alias": "Evening lights", "trigger": []},
        "context": {"id": f"ctx-{run_id}", "parent_id": None, "user_id": None},
        "domain": "automation",
        "item_id": ITEM_ID,
        "last_step": "action/1",
        "run_id": run_id,
        "script_execution": "finished",
        "state": "stopped",
        "timestamp": {
            "start": "2026-11-27T10:00:00+00:00",
            "finish": "2026-11-27T10:00:01+00:00",
        },
        "trace": trace_nodes,
        "trigger": "time",
    }


def _list_payload() -> list[dict[str, Any]]:
    return [
        _summary("run1", "2026-11-27T08:00:00+00:00"),
        _summary("run2", "2026-11-27T10:00:00+00:00"),
        _summary("run3", "2026-11-27T11:00:00+00:00"),
    ]


class FakeSession:
    """Deterministic WebSocket session fake scripted per command."""

    def __init__(self, outcomes: dict[str, Any]):
        self._outcomes = dict(outcomes)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.closed = False

    def request(self, command: str, payload: dict[str, Any] | None = None) -> Any:
        self.calls.append((command, payload))
        outcome = self._outcomes.get(command)
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome(payload)
        return outcome

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _fixed_now():
    with patch("tools.traces._now_utc", return_value=NOW):
        yield


def _register(mock_mcp, ha_url="http://localhost:8123", ha_token="token"):
    register_traces_tools(mock_mcp, ha_url, ha_token)
    return mock_mcp._tools["get_automation_execution_evidence"]


async def _call(tool, **kwargs):
    return json.loads(await tool(**kwargs))


class TestValidation:
    @pytest.mark.asyncio
    async def test_invalid_domain_is_rejected(self, mock_mcp):
        tool = _register(mock_mcp)
        data = await _call(tool, domain="scene", item_id=ITEM_ID)
        assert data["success"] is False
        assert data["error"]["code"] == "INVALID_ARGUMENTS"

    @pytest.mark.asyncio
    async def test_empty_item_id_is_rejected(self, mock_mcp):
        tool = _register(mock_mcp)
        data = await _call(tool, domain="automation", item_id="  ")
        assert data["success"] is False
        assert data["error"]["code"] == "INVALID_ARGUMENTS"

    @pytest.mark.asyncio
    async def test_non_string_item_id_is_rejected(self, mock_mcp):
        tool = _register(mock_mcp)
        data = await _call(tool, domain="automation", item_id=123)
        assert data["success"] is False
        assert data["error"]["code"] == "INVALID_ARGUMENTS"

    @pytest.mark.parametrize("hours", [0, -1, 169, "24", True, 1.5])
    @pytest.mark.asyncio
    async def test_hours_bounds_are_enforced(self, mock_mcp, hours):
        tool = _register(mock_mcp)
        data = await _call(tool, domain="automation", item_id=ITEM_ID, hours=hours)
        assert data["success"] is False
        assert data["error"]["code"] == "INVALID_ARGUMENTS"

    @pytest.mark.parametrize("limit", [0, 21, "5", True])
    @pytest.mark.asyncio
    async def test_limit_bounds_are_enforced(self, mock_mcp, limit):
        tool = _register(mock_mcp)
        data = await _call(tool, domain="automation", item_id=ITEM_ID, limit=limit)
        assert data["success"] is False
        assert data["error"]["code"] == "INVALID_ARGUMENTS"

    @pytest.mark.asyncio
    async def test_missing_token_fails_closed(self, mock_mcp):
        register_traces_tools(mock_mcp, "http://localhost:8123", "")
        tool = mock_mcp._tools["get_automation_execution_evidence"]
        data = await _call(tool, domain="automation", item_id=ITEM_ID)
        assert data["success"] is False
        assert data["error"]["code"] == "CONFIG_ERROR"

    @pytest.mark.asyncio
    async def test_empty_run_id_is_rejected(self, mock_mcp):
        tool = _register(mock_mcp)
        data = await _call(tool, domain="automation", item_id=ITEM_ID, run_id="   ")
        assert data["success"] is False
        assert data["error"]["code"] == "INVALID_ARGUMENTS"


class TestSelectionAndProjection:
    @pytest.mark.asyncio
    async def test_happy_path_projects_bounded_evidence(self, mock_mcp):
        session = FakeSession(
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": _list_payload(),
                "trace/get": lambda payload: _detail(str(payload["run_id"])),
            }
        )
        with patch("tools.traces._open_session", return_value=session):
            tool = _register(mock_mcp)
            data = await _call(tool, domain="automation", item_id=ITEM_ID, limit=2)

        assert data["success"] is True
        assert data["domain"] == "automation"
        assert data["item_id"] == ITEM_ID
        assert data["ha_version"] == HA_VERSION
        assert data["source"]["kind"] == "HA_WEBSOCKET_TRACE_API"
        assert data["authority"]["required_scope"] == "administrator"
        assert data["authority"]["llat_authenticated"] is True
        assert data["requested_utc_window"]["end_utc"] == NOW.isoformat()
        assert data["requested_utc_window"]["hours"] == 24

        # limit=2 with the run_id omitted selects the two most recent runs.
        assert len(data["evidence"]) == 2
        assert data["selected_run_ids"] == ["run3", "run2"]
        assert data["retention"]["completeness"] == "RETAINED_TRACES_ONLY"
        assert data["covered_utc_window"]["observed_runs"] == 2

        newest = data["evidence"][0]
        assert newest["run_id"] == "run3"
        assert newest["state"] == "stopped"
        assert newest["script_execution"] == "finished"
        assert newest["last_step"] == "action/1"
        assert newest["timestamp"]["start"] == "2026-11-27T10:00:00+00:00"
        assert newest["timestamp"]["finish"] == "2026-11-27T10:00:01+00:00"
        assert newest["context"]["id"] == "ctx-run3"
        assert newest["trigger"] == "time"

        session.close()
        assert session.closed

    @pytest.mark.asyncio
    async def test_window_filters_out_older_runs(self, mock_mcp):
        session = FakeSession(
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": [
                    _summary("old", "2026-11-01T08:00:00+00:00"),
                    _summary("new", "2026-11-27T11:00:00+00:00"),
                ],
                "trace/get": _detail("new"),
            }
        )
        with patch("tools.traces._open_session", return_value=session):
            tool = _register(mock_mcp)
            data = await _call(tool, domain="automation", item_id=ITEM_ID)

        assert data["success"] is True
        assert data["selected_run_ids"] == ["new"]

    @pytest.mark.asyncio
    async def test_explicit_run_id_returns_single_evidence(self, mock_mcp):
        session = FakeSession(
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": _list_payload(),
                "trace/get": _detail("run2"),
            }
        )
        with patch("tools.traces._open_session", return_value=session):
            tool = _register(mock_mcp)
            data = await _call(tool, domain="automation", item_id=ITEM_ID, run_id="run2")

        assert data["success"] is True
        assert data["evidence"] and data["evidence"][0]["run_id"] == "run2"
        assert data["run_id"] == "run2"
        trace_call = [call for call in session.calls if call[0] == "trace/get"]
        assert trace_call[0][1] == {
            "domain": "automation",
            "item_id": ITEM_ID,
            "run_id": "run2",
        }

    @pytest.mark.asyncio
    async def test_unknown_explicit_run_id_is_not_retained(self, mock_mcp):
        session = FakeSession(
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": _list_payload(),
                "trace/get": _detail("run3"),
            }
        )
        with patch("tools.traces._open_session", return_value=session):
            tool = _register(mock_mcp)
            data = await _call(tool, domain="automation", item_id=ITEM_ID, run_id="missing")

        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_NOT_RETAINED"

    @pytest.mark.asyncio
    async def test_no_matching_runs_is_not_retained(self, mock_mcp):
        session = FakeSession(
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": [],
                "trace/get": _detail("run3"),
            }
        )
        with patch("tools.traces._open_session", return_value=session):
            tool = _register(mock_mcp)
            data = await _call(tool, domain="automation", item_id=ITEM_ID)

        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_NOT_RETAINED"

    @pytest.mark.asyncio
    async def test_other_item_ids_are_filtered_out(self, mock_mcp):
        summary = _summary("run9", "2026-11-27T11:00:00+00:00")
        summary["item_id"] = "other_automation"
        session = FakeSession(
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": [summary],
                "trace/get": _detail("run9"),
            }
        )
        with patch("tools.traces._open_session", return_value=session):
            tool = _register(mock_mcp)
            data = await _call(tool, domain="automation", item_id=ITEM_ID)

        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_NOT_RETAINED"

    @pytest.mark.asyncio
    async def test_non_matching_domain_entries_are_filtered_out(self, mock_mcp):
        summary = _summary("run9", "2026-11-27T11:00:00+00:00")
        summary["domain"] = "script"
        session = FakeSession(
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": [summary],
                "trace/get": _detail("run9"),
            }
        )
        with patch("tools.traces._open_session", return_value=session):
            tool = _register(mock_mcp)
            data = await _call(tool, domain="automation", item_id=ITEM_ID)

        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_NOT_RETAINED"


class TestScriptDomain:
    @pytest.mark.asyncio
    async def test_script_domain_lists_script_traces(self, mock_mcp):
        script_summary = _summary("srun1", "2026-11-27T11:00:00+00:00")
        script_summary["domain"] = "script"
        script_summary["item_id"] = "cleanup_job"
        session = FakeSession(
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": [script_summary],
                "trace/get": _detail("srun1"),
            }
        )
        with patch("tools.traces._open_session", return_value=session):
            tool = _register(mock_mcp)
            data = await _call(tool, domain="script", item_id="cleanup_job")

        assert data["success"] is True
        list_call = [call for call in session.calls if call[0] == "trace/list"]
        assert list_call[0][1] == {"domain": "script"}


class TestErrorClassification:
    async def _call_with_session(self, mock_mcp, outcomes, **kwargs):
        session = FakeSession(outcomes)
        with patch("tools.traces._open_session", return_value=session):
            tool = _register(mock_mcp)
            return await _call(tool, domain="automation", item_id=ITEM_ID, **kwargs)

    @pytest.mark.asyncio
    async def test_auth_rejection_is_unauthorized(self, mock_mcp):
        data = await self._call_with_session(
            mock_mcp,
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": WebSocketAuthError("rejected"),
            },
        )
        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_UNAUTHORIZED"

    @pytest.mark.asyncio
    async def test_remote_unauthorized_is_unauthorized(self, mock_mcp):
        data = await self._call_with_session(
            mock_mcp,
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": WebSocketRemoteError("unauthorized", "user not admin"),
            },
        )
        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_UNAUTHORIZED"

    @pytest.mark.asyncio
    async def test_deadline_is_unavailable(self, mock_mcp):
        data = await self._call_with_session(
            mock_mcp,
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": WebSocketDeadlineError("slow"),
            },
        )
        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_oversized_frame_is_oversized(self, mock_mcp):
        data = await self._call_with_session(
            mock_mcp,
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": WebSocketOversizedError("huge"),
            },
        )
        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_OVERSIZED"

    @pytest.mark.asyncio
    async def test_malformed_list_is_malformed(self, mock_mcp):
        data = await self._call_with_session(
            mock_mcp,
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": {"unexpected": "shape"},
            },
        )
        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_MALFORMED"

    @pytest.mark.asyncio
    async def test_malformed_summary_entries_are_malformed(self, mock_mcp):
        data = await self._call_with_session(
            mock_mcp,
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": [{"run_id": 7, "item_id": None}],
            },
        )
        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_MALFORMED"

    @pytest.mark.asyncio
    async def test_missing_ha_version_is_malformed(self, mock_mcp):
        data = await self._call_with_session(
            mock_mcp,
            {
                "config/get": {"unexpected": True},
                "trace/list": _list_payload(),
            },
        )
        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_MALFORMED"

    @pytest.mark.asyncio
    async def test_explicit_run_get_failure_is_unavailable(self, mock_mcp):
        data = await self._call_with_session(
            mock_mcp,
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": _list_payload(),
                "trace/get": WebSocketDeadlineError("slow"),
            },
            run_id="run2",
        )
        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_malformed_detail_for_explicit_run_is_malformed(self, mock_mcp):
        data = await self._call_with_session(
            mock_mcp,
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": _list_payload(),
                "trace/get": ["not", "a", "dict"],
            },
            run_id="run2",
        )
        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_MALFORMED"

    @pytest.mark.asyncio
    async def test_all_multi_run_get_failures_are_unavailable(self, mock_mcp):
        outcomes: dict[str, Any] = {
            "config/get": {"version": HA_VERSION},
            "trace/list": _list_payload(),
            "trace/get": WebSocketDeadlineError("slow"),
        }
        data = await self._call_with_session(mock_mcp, outcomes)
        assert data["success"] is False
        assert data["error"]["code"] == "TRACE_UNAVAILABLE"


class TestRedactionBounds:
    async def _response(self, mock_mcp, detail, **kwargs):
        session = FakeSession(
            {
                "config/get": {"version": HA_VERSION},
                "trace/list": [_summary("run9", "2026-11-27T11:00:00+00:00")],
                "trace/get": detail,
            }
        )
        with patch("tools.traces._open_session", return_value=session):
            tool = _register(mock_mcp)
            return await _call(tool, domain="automation", item_id=ITEM_ID, **kwargs)

    @pytest.mark.asyncio
    async def test_raw_trace_and_config_are_never_returned(self, mock_mcp):
        data = await self._response(mock_mcp, _detail("run9"))
        assert data["success"] is True
        for forbidden in ("config", "trace", "blueprint_inputs"):
            assert forbidden not in data
            assert all(forbidden not in evidence for evidence in data["evidence"])

    @pytest.mark.asyncio
    async def test_changed_variables_are_bounded(self, mock_mcp):
        detail = _detail("run9", variables=0)
        detail["trace"]["action/1"] = [
            {
                "changed_variables": {
                    f"var_{index}": f"value-{index}"
                    for index in range(MAX_CHANGED_VARIABLE_ENTRIES + 20)
                },
                "error": None,
            }
        ]
        data = await self._response(mock_mcp, detail)
        evidence = data["evidence"][0]
        variables = evidence["changed_variables"]
        assert len(variables) == MAX_CHANGED_VARIABLE_ENTRIES
        assert variables[-1]["truncated"] is True

    @pytest.mark.asyncio
    async def test_error_summaries_are_bounded(self, mock_mcp):
        detail = _detail("run9", variables=0)
        detail["trace"] = {
            f"action/{index}": [{"error": f"boom-{index}", "changed_variables": {}}]
            for index in range(MAX_ERRORS + 10)
        }
        data = await self._response(mock_mcp, detail)
        evidence = data["evidence"][0]
        assert len(evidence["errors"]) == MAX_ERRORS
        assert evidence["errors_truncated"] is True

    @pytest.mark.asyncio
    async def test_trigger_summary_is_bounded(self, mock_mcp):
        detail = _detail("run9")
        detail["trigger"] = {"blob": "x" * 5000}
        data = await self._response(mock_mcp, detail)
        evidence = data["evidence"][0]
        assert evidence["trigger"]["truncated"] is True


class TestEvidenceDigest:
    async def _digest(self, mock_mcp, detail_state=None):
        outcomes: dict[str, Any] = {
            "config/get": {"version": HA_VERSION},
            "trace/list": _list_payload(),
            "trace/get": lambda payload: _detail(str(payload["run_id"])),
        }
        if detail_state is not None:
            outcomes["trace/get"] = lambda payload: {
                **_detail(str(payload["run_id"])),
                "state": detail_state,
            }
        session = FakeSession(outcomes)
        with patch("tools.traces._open_session", return_value=session):
            tool = _register(mock_mcp)
            data = await _call(tool, domain="automation", item_id=ITEM_ID, limit=2)
        return data["evidence_digest"], data

    @pytest.mark.asyncio
    async def test_digest_is_stable_for_identical_data(self, mock_mcp):
        first_digest, _ = await self._digest(mock_mcp)
        second_digest, _ = await self._digest(mock_mcp)
        assert first_digest == second_digest
        assert first_digest.startswith("sha256:")

    @pytest.mark.asyncio
    async def test_digest_changes_with_data(self, mock_mcp):
        first_digest, _ = await self._digest(mock_mcp)
        second_digest, _ = await self._digest(mock_mcp, detail_state="running")
        assert first_digest != second_digest


class TestToolWrapper:
    @pytest.mark.asyncio
    async def test_unexpected_exception_fails_closed(self, mock_mcp):
        with patch(
            "tools.traces._do_get_automation_execution_evidence",
            side_effect=RuntimeError("boom"),
        ):
            tool = _register(mock_mcp)
            result = await tool(domain="automation", item_id=ITEM_ID)
        data = json.loads(result)
        assert data["success"] is False
        assert "boom" in data["error"]

    @pytest.mark.asyncio
    async def test_registration_exposes_the_tool(self, mock_mcp):
        register_traces_tools(mock_mcp, "http://localhost:8123", "token")
        assert "get_automation_execution_evidence" in mock_mcp._tools
