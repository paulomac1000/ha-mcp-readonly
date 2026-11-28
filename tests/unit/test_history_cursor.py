"""
Tests for tools/history_cursor.py — tamper-evident history scan cursors.
"""

import base64
import json
from datetime import UTC, datetime

import pytest

from tools.history_cursor import (
    INVALID,
    MISMATCH,
    STALE,
    CursorError,
    HistoryCursorRequest,
    decode_cursor,
    history_sort_key,
    issue_cursor,
)

SECRET = "unit-test-secret"
FINGERPRINT = "sha256:abc123"
GENERATION = 3
BASE_REQUEST = HistoryCursorRequest(
    entity_ids=("light.living_room", "light.office"),
    domains=("light",),
    start_utc=datetime(2026, 1, 1, tzinfo=UTC),
    end_utc=datetime(2026, 1, 2, tzinfo=UTC),
)


class TestIssueDecodeRoundtrip:
    """Tests for cursor issue/decode roundtrip."""

    def test_decode_returns_issued_offset(self):
        cursor = issue_cursor(BASE_REQUEST, FINGERPRINT, GENERATION, 42, SECRET)

        assert decode_cursor(cursor, BASE_REQUEST, FINGERPRINT, GENERATION, SECRET) == 42

    def test_cursor_has_versioned_three_part_shape(self):
        cursor = issue_cursor(BASE_REQUEST, FINGERPRINT, GENERATION, 7, SECRET)

        prefix, payload, signature = cursor.split(".")
        assert prefix == "v1"
        assert payload != ""
        assert len(signature) == 64


class TestDeterministicCursor:
    """Cursor must depend on the canonical set, not the request ordering."""

    def test_reordered_entity_and_domain_sets_produce_identical_cursor(self):
        reordered = HistoryCursorRequest(
            entity_ids=("light.office", "light.living_room", "light.office"),
            domains=("light", "light"),
            start_utc=datetime(2026, 1, 1, tzinfo=UTC),
            end_utc=datetime(2026, 1, 2, tzinfo=UTC),
        )
        original = issue_cursor(BASE_REQUEST, FINGERPRINT, GENERATION, 9, SECRET)
        other = issue_cursor(reordered, FINGERPRINT, GENERATION, 9, SECRET)

        assert other == original

    def test_cursor_issued_from_reordered_request_decodes_for_base_request(self):
        reordered = HistoryCursorRequest(
            entity_ids=("light.office", "light.living_room"),
            domains=("light",),
            start_utc=datetime(2026, 1, 1, tzinfo=UTC),
            end_utc=datetime(2026, 1, 2, tzinfo=UTC),
        )
        cursor = issue_cursor(reordered, FINGERPRINT, GENERATION, 5, SECRET)

        assert decode_cursor(cursor, BASE_REQUEST, FINGERPRINT, GENERATION, SECRET) == 5


class TestTamperDetection:
    """Signature validation fails closed on tampering."""

    def test_tampered_payload_raises_invalid(self):
        cursor = issue_cursor(BASE_REQUEST, FINGERPRINT, GENERATION, 12, SECRET)
        prefix, payload, signature = cursor.split(".")
        tampered_data = json.loads(base64.urlsafe_b64decode(payload))
        tampered_data["offset"] = 13
        tampered_raw = json.dumps(tampered_data, sort_keys=True, separators=(",", ":")).encode()
        tampered = f"{prefix}.{base64.urlsafe_b64encode(tampered_raw).decode()}.{signature}"

        with pytest.raises(CursorError) as excinfo:
            decode_cursor(tampered, BASE_REQUEST, FINGERPRINT, GENERATION, SECRET)

        assert excinfo.value.code == INVALID


class TestRequestMismatch:
    """Decoding against a changed request must fail with MISMATCH."""

    def test_different_entity_request_raises_mismatch(self):
        other = HistoryCursorRequest(
            entity_ids=("light.office",),
            domains=("light",),
            start_utc=datetime(2026, 1, 1, tzinfo=UTC),
            end_utc=datetime(2026, 1, 2, tzinfo=UTC),
        )
        cursor = issue_cursor(BASE_REQUEST, FINGERPRINT, GENERATION, 1, SECRET)

        with pytest.raises(CursorError) as excinfo:
            decode_cursor(cursor, other, FINGERPRINT, GENERATION, SECRET)

        assert excinfo.value.code == MISMATCH

    def test_different_window_raises_mismatch(self):
        other = HistoryCursorRequest(
            entity_ids=BASE_REQUEST.entity_ids,
            domains=BASE_REQUEST.domains,
            start_utc=datetime(2026, 1, 1, tzinfo=UTC),
            end_utc=datetime(2026, 1, 3, tzinfo=UTC),
        )
        cursor = issue_cursor(BASE_REQUEST, FINGERPRINT, GENERATION, 1, SECRET)

        with pytest.raises(CursorError) as excinfo:
            decode_cursor(cursor, other, FINGERPRINT, GENERATION, SECRET)

        assert excinfo.value.code == MISMATCH


class TestGenerationStaleness:
    """Cursors issued against older evidence must fail with STALE."""

    def test_different_generation_raises_stale(self):
        cursor = issue_cursor(BASE_REQUEST, FINGERPRINT, GENERATION, 1, SECRET)

        with pytest.raises(CursorError) as excinfo:
            decode_cursor(cursor, BASE_REQUEST, FINGERPRINT, GENERATION + 1, SECRET)

        assert excinfo.value.code == STALE


class TestHistorySortKey:
    """Deterministic descending-time sort key ordering."""

    def test_equal_timestamps_order_by_entity_then_state_then_index(self):
        ts = "2026-01-01T00:00:00+00:00"
        rows = [
            {"entity_id": "light.office", "state": "off", "original_index": 9, "last_changed": ts},
            {
                "entity_id": "light.living_room",
                "state": "on",
                "original_index": 5,
                "last_changed": ts,
            },
            {
                "entity_id": "light.living_room",
                "state": "off",
                "original_index": 1,
                "last_changed": ts,
            },
        ]

        ordered = sorted(rows, key=history_sort_key)

        assert [(row["entity_id"], row["state"], row["original_index"]) for row in ordered] == [
            ("light.living_room", "off", 1),
            ("light.living_room", "on", 5),
            ("light.office", "off", 9),
        ]

    def test_naive_row_timestamp_is_treated_as_utc(self):
        naive_row = {
            "entity_id": "light.living_room",
            "state": "on",
            "original_index": 1,
            "last_changed": "2026-01-01T00:00:00",
        }
        aware_row = {
            "entity_id": "light.living_room",
            "state": "on",
            "original_index": 1,
            "last_changed": "2026-01-01T00:00:00+00:00",
        }

        assert history_sort_key(naive_row) == history_sort_key(aware_row)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
