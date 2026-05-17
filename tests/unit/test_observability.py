"""Unit tests for request-scoped observability.

References: mcp-server-standards.md Observability rules 9/10 (request_id in
contextvars, shared id in logs and _meta) and Canonical Template 4c
(per-tool invocation counter). [RULE: TEST-HIERARCHY-2] zero I/O.
"""

import contextvars
import logging
import uuid

from tools.observability import (
    RequestIdFilter,
    get_invocation_counts,
    get_request_id,
    increment_invocation,
    start_tool_context,
)


class TestRequestId:
    """Tests for start_tool_context / get_request_id."""

    def test_start_tool_context_returns_uuid(self):
        rid = start_tool_context()
        assert str(uuid.UUID(rid)) == rid

    def test_get_request_id_matches_started_context(self):
        rid = start_tool_context()
        assert get_request_id() == rid

    def test_fresh_context_returns_default(self):
        # A context that never called start_tool_context returns the default.
        ctx = contextvars.Context()
        assert ctx.run(get_request_id) == "-"

    def test_contexts_are_isolated(self):
        # [RULE: Observability-9] — concurrent contexts must not share request_id.
        results: dict[str, tuple[str, str]] = {}

        def _worker(key: str) -> None:
            rid = start_tool_context()
            results[key] = (rid, get_request_id())

        contextvars.copy_context().run(_worker, "a")
        contextvars.copy_context().run(_worker, "b")

        assert results["a"][0] == results["a"][1]
        assert results["b"][0] == results["b"][1]
        assert results["a"][0] != results["b"][0]


class TestRequestIdFilter:
    """Tests for the logging filter that injects request_id into records."""

    def test_filter_injects_current_request_id(self):
        start_tool_context()
        expected = get_request_id()
        record = logging.LogRecord("n", logging.INFO, "p", 1, "msg", None, None)
        flt = RequestIdFilter()
        assert flt.filter(record) is True
        assert record.request_id == expected


class TestInvocationCounter:
    """Tests for the thread-safe per-tool invocation counter."""

    def test_increment_and_read(self):
        before = get_invocation_counts().get("obs_test_tool", 0)
        increment_invocation("obs_test_tool")
        increment_invocation("obs_test_tool")
        assert get_invocation_counts()["obs_test_tool"] == before + 2

    def test_get_invocation_counts_returns_copy(self):
        snapshot = get_invocation_counts()
        snapshot["mutated_key"] = 999
        assert "mutated_key" not in get_invocation_counts()
