"""Small reusable authenticated Home Assistant WebSocket command transport.

This module owns the official ``auth_required``/``auth``/``auth_ok`` handshake,
request-id correlation, deadline/timeout enforcement, and a per-frame byte
bound. It never falls back to frontend cookies or REST trace endpoints and
never caches credentials. Every failure is classified into a typed exception
so callers can map transport outcomes onto their own error contracts.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

_LOGGER = logging.getLogger(__name__)

WS_PATH = "/api/websocket"
DEFAULT_CONNECT_TIMEOUT_S = 10.0
DEFAULT_COMMAND_TIMEOUT_S = 10.0
DEFAULT_SESSION_DEADLINE_S = 30.0
DEFAULT_MAX_FRAME_BYTES = 8 * 1024 * 1024
CLOSE_TIMEOUT_S = 3.0
_MAX_UNMATCHED_FRAMES = 32


class WebSocketTransportError(Exception):
    """Base class for classified WebSocket transport failures."""

    code = "TRANSPORT_ERROR"


class WebSocketAuthError(WebSocketTransportError):
    """Home Assistant rejected the long-lived access token."""

    code = "AUTH_FAILED"


class WebSocketProtocolError(WebSocketTransportError):
    """Frames deviated from the Home Assistant WebSocket protocol contract."""

    code = "PROTOCOL_ERROR"


class WebSocketDeadlineError(WebSocketTransportError):
    """The handshake or a command exceeded its wait budget."""

    code = "DEADLINE_EXCEEDED"


class WebSocketOversizedError(WebSocketTransportError):
    """A frame exceeded the configured byte bound."""

    code = "RESPONSE_TOO_LARGE"


class WebSocketRemoteError(WebSocketTransportError):
    """Home Assistant answered a command with success=false."""

    code = "REMOTE_ERROR"

    def __init__(self, remote_code: str, message: str) -> None:
        super().__init__(f"{remote_code}: {message}")
        self.remote_code = remote_code
        self.message = message


def websocket_url(ha_url: str) -> str:
    """Derive the Home Assistant WebSocket endpoint from the configured base URL.

    Args:
        ha_url: Configured Home Assistant base URL (http or https).

    Returns:
        The ``/api/websocket`` endpoint using the ws or wss scheme.

    Raises:
        ValueError: When the base URL does not use http or https.
    """
    base = ha_url.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :] + WS_PATH
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :] + WS_PATH
    raise ValueError("HA_URL must use http or https")


def _assert_http_https(ha_url: str) -> None:
    """Reject non-HTTP(S) targets before any token leaves the process."""
    if urlparse(ha_url).scheme not in {"http", "https"}:
        raise ValueError("HA_URL must use http or https")


@dataclass(frozen=True)
class TransportLimits:
    """Bounded wait and size budgets for one WebSocket session."""

    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S
    command_timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S
    session_deadline_s: float = DEFAULT_SESSION_DEADLINE_S
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES


class HaWebSocketSession:
    """One authenticated Home Assistant WebSocket session.

    The session performs the official token handshake on construction,
    correlates every request with a monotonically increasing id, bounds each
    command by both the per-command timeout and the remaining session
    deadline, and rejects frames larger than the configured byte bound.
    """

    def __init__(
        self, connection: Any, *, access_token: str, limits: TransportLimits | None = None
    ) -> None:
        """Wrap an already-open WebSocket connection and authenticate it.

        Args:
            connection: Object providing ``send(str)``, ``recv(timeout)`` and
                ``close()`` with the ``websockets.sync.client`` semantics.
            access_token: Long-lived access token for the ``auth`` frame.
            limits: Wait and size budgets.

        Raises:
            WebSocketAuthError: The token was rejected.
            WebSocketProtocolError: The handshake deviated from the protocol.
            WebSocketDeadlineError: The handshake exceeded its wait budget.
            WebSocketTransportError: Any other handshake failure.
        """
        self._connection = connection
        self._access_token = access_token
        self._limits = limits if limits is not None else TransportLimits()
        self._deadline = time.monotonic() + self._limits.session_deadline_s
        self._closed = False
        self._next_id = 1
        self._authenticate()

    @classmethod
    def connect(
        cls,
        ha_url: str,
        ha_token: str,
        *,
        limits: TransportLimits | None = None,
    ) -> HaWebSocketSession:
        """Open and authenticate a real WebSocket session.

        Args:
            ha_url: Configured Home Assistant base URL.
            ha_token: Long-lived access token used for the ``auth`` frame.
            limits: Wait and size budgets.

        Returns:
            An authenticated session ready for bounded commands.

        Raises:
            WebSocketAuthError: The token was rejected.
            WebSocketProtocolError: The handshake deviated from the protocol.
            WebSocketDeadlineError: The handshake exceeded its wait budget.
            WebSocketOversizedError: The server sent an oversized frame.
            WebSocketTransportError: Connection or transport failure.
            ValueError: The base URL is not http or https.
        """
        _assert_http_https(ha_url)
        active_limits = limits if limits is not None else TransportLimits()
        from websockets.sync.client import connect as ws_connect

        try:
            connection = ws_connect(
                websocket_url(ha_url),
                open_timeout=active_limits.connect_timeout_s,
                close_timeout=CLOSE_TIMEOUT_S,
                max_size=active_limits.max_frame_bytes,
            )
        except TimeoutError as exc:
            raise WebSocketDeadlineError(f"connect exceeded its budget: {exc}") from exc
        except Exception as exc:
            raise WebSocketTransportError(f"connect failed: {type(exc).__name__}") from exc
        return cls(connection, access_token=ha_token, limits=active_limits)

    def _remaining_time(self) -> float:
        return self._deadline - time.monotonic()

    def _recv_json(self) -> dict[str, Any]:
        wait = max(0.001, min(self._limits.command_timeout_s, self._remaining_time()))
        try:
            raw = self._connection.recv(timeout=wait)
        except TimeoutError as exc:
            raise WebSocketDeadlineError(f"recv exceeded its budget: {exc}") from exc
        except Exception as exc:
            if isinstance(exc, (WebSocketTransportError,)):
                raise
            raise WebSocketTransportError(f"recv failed: {type(exc).__name__}") from exc
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if len(raw) > self._limits.max_frame_bytes:
            raise WebSocketOversizedError(f"frame exceeds {self._limits.max_frame_bytes} bytes")
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise WebSocketProtocolError("frame is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise WebSocketProtocolError("frame is not a JSON object")
        return payload

    def _authenticate(self) -> None:
        try:
            first = self._recv_json()
        except WebSocketTransportError:
            raise
        if first.get("type") != "auth_required":
            raise WebSocketProtocolError("Home Assistant did not request authentication")
        self._connection.send(json.dumps({"type": "auth", "access_token": self._access_token}))
        reply = self._recv_json()
        reply_type = reply.get("type")
        if reply_type == "auth_ok":
            return
        if reply_type == "auth_invalid":
            raise WebSocketAuthError("Home Assistant rejected the access token")
        raise WebSocketProtocolError("unexpected authentication reply")

    def request(self, command: str, payload: dict[str, Any] | None = None) -> Any:
        """Send one command and return the successful result payload.

        Args:
            command: WebSocket command type (e.g. ``trace/list``).
            payload: Additional command fields.

        Returns:
            The ``result`` value of the matching response frame.

        Raises:
            WebSocketRemoteError: Home Assistant answered success=false.
            WebSocketProtocolError: The response stream deviated.
            WebSocketDeadlineError: No matching response within the budget.
            WebSocketOversizedError: A frame exceeded the byte bound.
        """
        if self._closed:
            raise WebSocketProtocolError("session is closed")
        request_id = self._next_id
        self._next_id += 1
        frame: dict[str, Any] = {"id": request_id, "type": command}
        if payload:
            frame.update(payload)
        self._connection.send(json.dumps(frame))
        unmatched = 0
        while True:
            response = self._recv_json()
            if response.get("id") != request_id:
                unmatched += 1
                if unmatched > _MAX_UNMATCHED_FRAMES:
                    raise WebSocketProtocolError(
                        "could not correlate the request with a response frame"
                    )
                continue
            if response.get("type") != "result":
                raise WebSocketProtocolError("response frame is not a result")
            if response.get("success") is True:
                return response.get("result")
            error = response.get("error")
            if not isinstance(error, dict):
                error = {"code": "unknown_error", "message": "command failed"}
            raise WebSocketRemoteError(
                str(error.get("code", "unknown_error")),
                str(error.get("message", "command failed")),
            )

    def close(self) -> None:
        """Close the underlying connection; safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        try:
            self._connection.close()
        except Exception as exc:
            _LOGGER.debug("WebSocket close failed: %s", type(exc).__name__)
