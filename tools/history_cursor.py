"""Tamper-evident pagination cursors for history snapshot scans.

Cursors bind a canonical JSON payload (entity filter, time window, source
fingerprint, evidence generation, and offset) to an HMAC-SHA256 signature so
clients cannot replay a cursor against a different request or against stale
evidence. Decoding validates signature integrity first, then request
consistency, then evidence freshness.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

INVALID = "INVALID"
MISMATCH = "MISMATCH"
STALE = "STALE"


class CursorError(Exception):
    """Raised when a history cursor fails signature, match, or freshness validation.

    Args:
        code: Machine-readable failure code: INVALID for malformed cursors,
            MISMATCH for cursors bound to a different request, STALE for cursors
            issued against older evidence.
        message: Human-readable failure description.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class HistoryCursorRequest:
    """Immutable filter describing the history scan a cursor is bound to.

    Args:
        entity_ids: Entity IDs included in the scan.
        domains: Domains included in the scan.
        start_utc: Inclusive start of the scan window.
        end_utc: Exclusive end of the scan window.
    """

    entity_ids: tuple[str, ...]
    domains: tuple[str, ...]
    start_utc: datetime
    end_utc: datetime


def _utc(dt: datetime) -> datetime:
    """Normalize a datetime to UTC, assuming naive values are already UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _payload(
    request: HistoryCursorRequest,
    source_fingerprint: str,
    evidence_generation: int,
    offset: int,
) -> dict[str, Any]:
    """Build the canonical cursor payload signed and encoded into the cursor."""
    return {
        "v": 1,
        "entity_ids": sorted(set(request.entity_ids)),
        "domains": sorted(set(request.domains)),
        "start_utc": _utc(request.start_utc).isoformat(),
        "end_utc": _utc(request.end_utc).isoformat(),
        "source_fingerprint": source_fingerprint,
        "evidence_generation": evidence_generation,
        "offset": offset,
    }


def issue_cursor(
    request: HistoryCursorRequest,
    source_fingerprint: str,
    evidence_generation: int,
    offset: int,
    secret: str,
) -> str:
    """Sign and encode a history scan position into an opaque cursor token.

    Args:
        request: History scan filter the cursor is bound to.
        source_fingerprint: Fingerprint of the snapshot the scan reads.
        evidence_generation: Evidence generation the scan was issued against.
        offset: Row offset the cursor resumes from.
        secret: HMAC signing secret.

    Returns:
        The versioned cursor token "v1.<payload>.<signature>".
    """
    raw = json.dumps(
        _payload(request, source_fingerprint, evidence_generation, offset),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return "v1." + base64.urlsafe_b64encode(raw).decode() + "." + signature


def _decode_signed_payload(cursor: str, secret: str) -> dict[str, Any]:
    """Verify a cursor signature and return its decoded JSON payload."""
    try:
        prefix, encoded, signature = cursor.split(".")
        raw = base64.urlsafe_b64decode(encoded)
        data = json.loads(raw)
        if (
            prefix != "v1"
            or not isinstance(data, dict)
            or not hmac.compare_digest(
                signature, hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            )
        ):
            raise CursorError(INVALID, "invalid cursor")
    except CursorError:
        raise
    except (
        ValueError,
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise CursorError(INVALID, "invalid cursor") from exc
    return data


def cursor_request(cursor: str, secret: str) -> tuple[HistoryCursorRequest, int]:
    """Read the frozen request and offset from a valid cursor.

    Args:
        cursor: Cursor token produced by issue_cursor.
        secret: HMAC signing secret used when the cursor was issued.

    Returns:
        The frozen request and row offset encoded in the cursor.

    Raises:
        CursorError: When the cursor is malformed or its signature is invalid.
    """
    data = _decode_signed_payload(cursor, secret)
    entity_ids = data.get("entity_ids")
    domains = data.get("domains")
    offset = data.get("offset")
    if (
        not isinstance(entity_ids, list)
        or not all(isinstance(value, str) for value in entity_ids)
        or not isinstance(domains, list)
        or not all(isinstance(value, str) for value in domains)
        or not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
    ):
        raise CursorError(INVALID, "invalid cursor payload")
    try:
        start_utc = datetime.fromisoformat(str(data["start_utc"]))
        end_utc = datetime.fromisoformat(str(data["end_utc"]))
    except (KeyError, ValueError) as exc:
        raise CursorError(INVALID, "invalid cursor window") from exc
    if start_utc.tzinfo is None or end_utc.tzinfo is None:
        raise CursorError(INVALID, "invalid cursor window")
    request = HistoryCursorRequest(
        entity_ids=tuple(entity_ids),
        domains=tuple(domains),
        start_utc=_utc(start_utc),
        end_utc=_utc(end_utc),
    )
    return request, offset


def decode_cursor(
    cursor: str,
    request: HistoryCursorRequest,
    source_fingerprint: str,
    evidence_generation: int,
    secret: str,
) -> int:
    """Decode and validate a cursor token against the current scan context.

    Args:
        cursor: Cursor token produced by issue_cursor.
        request: History scan filter the cursor must match.
        source_fingerprint: Fingerprint of the snapshot the scan reads.
        evidence_generation: Current evidence generation; stale cursors fail.
        secret: HMAC signing secret used when the cursor was issued.

    Returns:
        The row offset the cursor resumes from.

    Raises:
        CursorError: When the cursor is malformed (INVALID), bound to a
            different request (MISMATCH), or issued against older evidence
            (STALE).
    """
    data = _decode_signed_payload(cursor, secret)
    frozen_request, offset = cursor_request(cursor, secret)
    cursor_fingerprint = data.get("source_fingerprint")
    if not isinstance(cursor_fingerprint, str):
        raise CursorError(INVALID, "invalid cursor source fingerprint")
    expected = _payload(request, source_fingerprint, evidence_generation, 0)
    actual = _payload(frozen_request, cursor_fingerprint, 0, offset)
    for key in ("entity_ids", "domains", "start_utc", "end_utc", "source_fingerprint"):
        if actual.get(key) != expected.get(key):
            raise CursorError(MISMATCH, "cursor request mismatch")
    if data.get("evidence_generation") != evidence_generation:
        raise CursorError(STALE, "cursor evidence is stale")
    return offset


def history_sort_key(row: Mapping[str, Any]) -> tuple[float, str, str, int]:
    """Compute a deterministic descending-time sort key for a history row.

    Args:
        row: History row with entity_id, state, original_index, and either
            last_changed or last_updated.

    Returns:
        Tuple sorting rows newest-first, then by entity_id, state, and
        original_index for stable ordering.
    """
    raw = row.get("last_changed") or row.get("last_updated") or row.get("timestamp")
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    state = row.get("state", row.get("to_state"))
    return (
        -_utc(parsed).timestamp(),
        str(row["entity_id"]),
        str(state),
        int(row["original_index"]),
    )
