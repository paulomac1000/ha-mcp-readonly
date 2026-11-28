"""Tests for the bounded authenticated Home Assistant WebSocket transport."""

import json
from typing import Any

import pytest

from tools.ws_transport import (
    HaWebSocketSession,
    TransportLimits,
    WebSocketAuthError,
    WebSocketDeadlineError,
    WebSocketOversizedError,
    WebSocketProtocolError,
    WebSocketRemoteError,
    WebSocketTransportError,
    websocket_url,
)

AUTH_OK_SESSION = [
    json.dumps({"type": "auth_required", "ha_version": "2026.9.1"}),
    json.dumps({"type": "auth_ok", "ha_version": "2026.9.1"}),
]


class FakeConnection:
    """Deterministic in-memory WebSocket connection matching the sync client surface."""

    def __init__(self, frames: list[str], recv_error: Exception | None = None):
        self.sent: list[dict[str, Any]] = []
        self._frames = list(frames)
        self._recv_error = recv_error
        self.closed = False

    def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def recv(self, timeout: float | None = None) -> str:
        assert timeout is not None
        if self._frames:
            return self._frames.pop(0)
        raise self._recv_error if self._recv_error is not None else TimeoutError("timed out")

    def close(self) -> None:
        self.closed = True


def _session(connection: FakeConnection) -> HaWebSocketSession:
    return HaWebSocketSession(connection, access_token="token-value")


class TestWebsocketUrl:
    def test_http_maps_to_ws(self) -> None:
        assert websocket_url("http://ha.local:8123") == "ws://ha.local:8123/api/websocket"

    def test_https_maps_to_wss(self) -> None:
        assert websocket_url("https://ha.local") == "wss://ha.local/api/websocket"

    def test_trailing_slash_is_removed(self) -> None:
        assert websocket_url("http://ha.local:8123/") == "ws://ha.local:8123/api/websocket"

    def test_unsupported_scheme_is_refused(self) -> None:
        with pytest.raises(ValueError, match="http or https"):
            websocket_url("ftp://ha.local")


class TestHandshake:
    def test_auth_handshake_completes_and_sends_token(self) -> None:
        connection = FakeConnection(list(AUTH_OK_SESSION))
        _session(connection).close()
        assert connection.sent == [{"type": "auth", "access_token": "token-value"}]

    def test_missing_first_frame_is_protocol_error(self) -> None:
        connection = FakeConnection([json.dumps({"type": "result"})])
        with pytest.raises(WebSocketProtocolError, match="authentication"):
            _session(connection)

    def test_auth_invalid_is_auth_error(self) -> None:
        connection = FakeConnection(
            [
                json.dumps({"type": "auth_required", "ha_version": "2026.9.1"}),
                json.dumps({"type": "auth_invalid", "message": "Invalid access token"}),
            ]
        )
        with pytest.raises(WebSocketAuthError):
            _session(connection)

    def test_unexpected_auth_reply_is_protocol_error(self) -> None:
        connection = FakeConnection(
            [
                json.dumps({"type": "auth_required"}),
                json.dumps({"type": "pong"}),
            ]
        )
        with pytest.raises(WebSocketProtocolError):
            _session(connection)

    def test_handshake_timeout_is_deadline_error(self) -> None:
        connection = FakeConnection([], recv_error=TimeoutError("handshake"))
        with pytest.raises(WebSocketDeadlineError):
            _session(connection)

    def test_handshake_transport_failure_is_transport_error(self) -> None:
        connection = FakeConnection([], recv_error=ConnectionError("reset"))
        with pytest.raises(WebSocketTransportError) as caught:
            _session(connection)
        assert not isinstance(caught.value, WebSocketDeadlineError)


class TestRequestCorrelation:
    def test_result_for_matching_id_is_returned(self) -> None:
        connection = FakeConnection(
            [
                *AUTH_OK_SESSION,
                json.dumps({"id": 1, "type": "result", "success": True, "result": [1, 2]}),
            ]
        )
        session = _session(connection)
        assert session.request("trace/list", {"domain": "automation"}) == [1, 2]
        assert connection.sent[-1] == {
            "id": 1,
            "type": "trace/list",
            "domain": "automation",
        }

    def test_request_ids_correlate_monotonically(self) -> None:
        connection = FakeConnection(
            [
                *AUTH_OK_SESSION,
                json.dumps({"id": 1, "type": "result", "success": True, "result": "a"}),
                json.dumps({"id": 2, "type": "result", "success": True, "result": "b"}),
            ]
        )
        session = _session(connection)
        session.request("config/get")
        session.request("trace/list")
        assert [frame["id"] for frame in connection.sent[1:]] == [1, 2]

    def test_unmatched_frames_are_skipped_up_to_a_bound(self) -> None:
        stray = {"type": "event", "event": "noise"}
        connection = FakeConnection(
            [
                *AUTH_OK_SESSION,
                *[json.dumps(stray)] * 32,
                json.dumps({"id": 1, "type": "result", "success": True, "result": "ok"}),
            ]
        )
        session = _session(connection)
        assert session.request("trace/list") == "ok"

    def test_excessive_unmatched_frames_are_protocol_errors(self) -> None:
        stray = {"type": "event", "event": "noise"}
        connection = FakeConnection(
            [
                *AUTH_OK_SESSION,
                *[json.dumps(stray)] * 33,
            ]
        )
        session = _session(connection)
        with pytest.raises(WebSocketProtocolError, match="correlat"):
            session.request("trace/list")

    def test_non_result_frame_for_matching_id_is_protocol_error(self) -> None:
        connection = FakeConnection([*AUTH_OK_SESSION, json.dumps({"id": 1, "type": "pong"})])
        session = _session(connection)
        with pytest.raises(WebSocketProtocolError):
            session.request("trace/list")

    def test_non_dict_frame_is_protocol_error(self) -> None:
        connection = FakeConnection([*AUTH_OK_SESSION, json.dumps(["not", "a", "dict"])])
        session = _session(connection)
        with pytest.raises(WebSocketProtocolError):
            session.request("trace/list")

    def test_invalid_json_frame_is_protocol_error(self) -> None:
        connection = FakeConnection([*AUTH_OK_SESSION, "{not json"])
        session = _session(connection)
        with pytest.raises(WebSocketProtocolError):
            session.request("trace/list")


class TestRemoteErrors:
    def test_unauthorized_remote_error_is_classified(self) -> None:
        connection = FakeConnection(
            [
                *AUTH_OK_SESSION,
                json.dumps(
                    {
                        "id": 1,
                        "type": "result",
                        "success": False,
                        "error": {"code": "unauthorized", "message": "user not admin"},
                    }
                ),
            ]
        )
        session = _session(connection)
        with pytest.raises(WebSocketRemoteError) as caught:
            session.request("trace/list")
        assert caught.value.remote_code == "unauthorized"
        assert caught.value.message == "user not admin"

    def test_other_remote_error_is_classified(self) -> None:
        connection = FakeConnection(
            [
                *AUTH_OK_SESSION,
                json.dumps(
                    {
                        "id": 1,
                        "type": "result",
                        "success": False,
                        "error": {"code": "unknown_command", "message": "nope"},
                    }
                ),
            ]
        )
        session = _session(connection)
        with pytest.raises(WebSocketRemoteError) as caught:
            session.request("bogus/command")
        assert caught.value.remote_code == "unknown_command"


class TestDeadlineAndSize:
    def test_recv_timeout_is_deadline_error(self) -> None:
        connection = FakeConnection([*AUTH_OK_SESSION])
        session = _session(connection)
        with pytest.raises(WebSocketDeadlineError):
            session.request("trace/list")

    def test_session_deadline_bounds_command_wait(self) -> None:
        connection = FakeConnection(
            [
                *AUTH_OK_SESSION,
            ]
        )
        limits = TransportLimits(session_deadline_s=0.5)
        session = HaWebSocketSession(connection, access_token="token-value", limits=limits)
        with pytest.raises(WebSocketDeadlineError):
            session.request("trace/list")
        # The wait used the remaining session deadline, not the longer default.
        assert session._remaining_time() <= 0.5

    def test_oversized_frame_is_oversized_error(self) -> None:
        connection = FakeConnection(
            [
                *AUTH_OK_SESSION,
                json.dumps({"garbage": "x" * 4096}),
            ]
        )
        limits = TransportLimits(max_frame_bytes=256)
        session = HaWebSocketSession(connection, access_token="token-value", limits=limits)
        with pytest.raises(WebSocketOversizedError):
            session.request("trace/list")


class TestLifecycle:
    def test_close_is_idempotent(self) -> None:
        connection = FakeConnection(list(AUTH_OK_SESSION))
        session = _session(connection)
        session.close()
        session.close()
        assert connection.closed

    def test_request_after_close_is_refused(self) -> None:
        connection = FakeConnection(list(AUTH_OK_SESSION))
        session = _session(connection)
        session.close()
        with pytest.raises(WebSocketProtocolError, match="closed"):
            session.request("trace/list")
