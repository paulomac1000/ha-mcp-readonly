"""Completeness and provenance tracking for generated HA context artifacts."""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

SourceStatus = Literal["complete", "partial", "unavailable", "skipped"]

_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|credential|private[_-]?key|webhook|refresh[_-]?token|access[_-]?token|client[_-]?secret|(?:^|_)(?:psk|pin|passphrase)(?:$|_))",
    re.IGNORECASE,
)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+")
_BEARER = re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~-]+")
_QUERY_SECRET = re.compile(r"(?i)([?&](?:token|api_key|access_token|key)=)[^&#\s]+")


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source: str
    method: str
    status: SourceStatus
    records: int
    bytes: int
    redacted_fields: int
    collected_at: str
    reason: str | None = None
    requested: str | None = None


class ProvenanceTracker:
    """Thread-safe source matrix included verbatim in the final report."""

    def __init__(self) -> None:
        self._records: dict[str, SourceRecord] = {}
        self._lock = threading.Lock()

    def record(
        self,
        source: str,
        *,
        method: str,
        status: SourceStatus,
        records: int = 0,
        size_bytes: int = 0,
        redacted_fields: int = 0,
        reason: str | None = None,
        requested: str | None = None,
    ) -> None:
        item = SourceRecord(
            source=source,
            method=method,
            status=status,
            records=max(0, records),
            bytes=max(0, size_bytes),
            redacted_fields=max(0, redacted_fields),
            collected_at=datetime.now(UTC).isoformat(),
            reason=reason,
            requested=requested,
        )
        with self._lock:
            self._records[source] = item

    def as_dict(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {name: asdict(item) for name, item in sorted(self._records.items())}

    def summary(self) -> dict[str, Any]:
        records = self.as_dict()
        counts = {status: 0 for status in ("complete", "partial", "unavailable", "skipped")}
        for item in records.values():
            counts[str(item["status"])] += 1
        policy_excluded = sum(
            1
            for item in records.values()
            if item["status"] == "skipped" and str(item.get("reason") or "").startswith("policy:")
        )
        missing = counts["partial"] + counts["unavailable"] + counts["skipped"] - policy_excluded
        completeness = "complete" if missing == 0 else "partial"
        return {
            "completeness": completeness,
            "counts": counts,
            "policy_excluded": policy_excluded,
            "sources": records,
        }


def record_count(value: Any) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 1 if value is not None else 0


def redact_sensitive(value: Any) -> tuple[Any, int]:
    """Recursively redact credential-bearing fields while retaining structure."""

    if isinstance(value, dict):
        output: dict[str, Any] = {}
        redactions = 0
        for key, child in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY.search(key_text):
                output[key_text] = "[REDACTED]"
                redactions += 1
                continue
            safe_child, count = redact_sensitive(child)
            output[key_text] = safe_child
            redactions += count
        return output, redactions
    if isinstance(value, list):
        output_list: list[Any] = []
        redactions = 0
        for child in value:
            safe_child, count = redact_sensitive(child)
            output_list.append(safe_child)
            redactions += count
        return output_list, redactions
    if isinstance(value, tuple):
        safe, count = redact_sensitive(list(value))
        return safe, count
    if isinstance(value, str):
        redacted = _JWT.sub("[JWT_REDACTED]", value)
        redacted = _BEARER.sub("Bearer [REDACTED]", redacted)
        redacted = _QUERY_SECRET.sub(r"\1[REDACTED]", redacted)
        return redacted, int(redacted != value)
    return value, 0
