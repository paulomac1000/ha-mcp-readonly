"""Shared bounded log-evidence contract for Home Assistant log tools.

Single normalization seam used by ``analyze_log_errors``, ``get_log_insights``
and ``get_log_timeline``: event parsing, trusted source-clock correction, boot
and rotation identity, coverage accounting, pattern counting and stable
evidence identity. The startup-marker scan is intentionally the only copy in
the codebase; a shared boot-prerequisite provider remains open as issue #58.

Source timestamps are naive wall-clock values. They are converted to UTC only
when the caller supplies a trusted IANA timezone or fixed offset; a naive
local timestamp is never labeled UTC. One clock value is captured per call.
"""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONTRACT_VERSION = 2

STARTUP_MARKER = "Starting Home Assistant"

_TIMESTAMP_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.\d+)?")
_COMPONENT_PATTERN = re.compile(r"\[([^\]]+)\]")
_LEVEL_PATTERN = re.compile(r"\b(ERROR|WARNING|INFO|DEBUG)\b")
_OFFSET_PATTERN = re.compile(r"([+-])(\d{2}):(\d{2})")

_SOURCE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_SEVERITY_LEVELS = frozenset({"ERROR", "WARNING"})
_NO_TRUSTED_OFFSET_REASON = "Source timestamps have no trusted UTC offset"
_NO_SIGHTINGS_REASON = "No dated error or warning sightings in the selected window"


@dataclass(frozen=True, slots=True)
class ParsedEvent:
    """One normalized log line.

    Attributes:
        source_time: Naive wall-clock timestamp string, or None when absent.
        level: Severity token (ERROR/WARNING/INFO/DEBUG), or None when absent.
        component: Bracketed component name, or empty string.
        message: Free-form message text after the component bracket.
    """

    source_time: str | None
    level: str | None
    component: str
    message: str

    @property
    def unparsed(self) -> bool:
        """True when the line carries neither a timestamp nor a severity token."""
        return self.source_time is None and self.level is None

    @property
    def parsing_class(self) -> str:
        """Return "dated", "undated" or "unparsed" for parsing-quality counts."""
        if self.source_time is not None:
            return "dated"
        return "unparsed" if self.unparsed else "undated"


def parse_event(line: str) -> ParsedEvent:
    """Parse a single log line into its normalized event identity."""
    ts_match = _TIMESTAMP_PATTERN.search(line)
    source_time = ts_match.group(1) if ts_match else None
    level_match = _LEVEL_PATTERN.search(line)
    level = level_match.group(1) if level_match else None
    comp_match = _COMPONENT_PATTERN.search(line)
    component = comp_match.group(1) if comp_match else ""
    if comp_match:
        message = line[comp_match.end() :].strip()
    else:
        message = line.strip()
    return ParsedEvent(source_time=source_time, level=level, component=component, message=message)


def normalize_pattern(message: str) -> str:
    """Normalize a message into a comparable grouped signature."""
    normalized = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?", "TIMESTAMP", message)
    normalized = re.sub(r"[a-f0-9]{8,}", "ID", normalized)
    normalized = re.sub(r"\d+\.\d+\.\d+\.\d+", "IP", normalized)
    normalized = re.sub(r"(?<![a-z_])\d+(?![a-z_])", "N", normalized)
    return normalized[:150].strip()


def parse_trusted_timezone(value: str | None) -> tzinfo | None:
    """Parse a caller-supplied IANA timezone or fixed UTC offset."""
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.upper() in {"Z", "UTC"}:
        return UTC
    offset_match = _OFFSET_PATTERN.fullmatch(candidate)
    if offset_match is not None:
        sign = 1 if offset_match.group(1) == "+" else -1
        delta = timedelta(hours=int(offset_match.group(2)), minutes=int(offset_match.group(3)))
        return timezone(sign * delta)
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class TrustedClock:
    """One clock capture per call, with optional trusted source timezone."""

    zone: tzinfo | None
    source_timezone: str | None
    now_utc: datetime

    @classmethod
    def capture(cls, source_timezone: str | None, zone: tzinfo | None) -> TrustedClock:
        """Capture a single trusted clock value for one tool call."""
        return cls(zone=zone, source_timezone=source_timezone, now_utc=datetime.now(UTC))

    @property
    def clock_confidence(self) -> str:
        """Return CALLER_TRUSTED_OFFSET when a trusted timezone is present."""
        return "CALLER_TRUSTED_OFFSET" if self.zone is not None else "TIME_BASIS_UNKNOWN"

    @property
    def timezone_source(self) -> str:
        """Return the provenance of the timezone used for UTC conversion."""
        return "CALLER" if self.zone is not None else "UNKNOWN"

    def to_utc(self, source_time: str) -> datetime | None:
        """Convert a naive source-local timestamp using the trusted timezone."""
        if self.zone is None:
            return None
        naive = datetime.strptime(source_time, _SOURCE_TIME_FORMAT)
        return self.to_utc_from_naive(naive)

    def to_utc_from_naive(self, naive: datetime) -> datetime | None:
        """Convert an already-parsed naive datetime using the trusted timezone."""
        if self.zone is None:
            return None
        return naive.replace(tzinfo=self.zone).astimezone(UTC)

    def source_local_now_naive(self) -> datetime:
        """Return the captured now as naive source-local wall-clock time."""
        zone = self.zone if self.zone is not None else self.now_utc.astimezone().tzinfo
        return self.now_utc.astimezone(zone).replace(tzinfo=None)

    def local_offset_label(self, source_time: str | None) -> str | None:
        """Return the source-local +HH:MM offset for a naive timestamp."""
        if self.zone is None or source_time is None:
            return None
        naive = datetime.strptime(source_time, _SOURCE_TIME_FORMAT)
        return naive.replace(tzinfo=self.zone).isoformat()[-6:]

    def basis(self, local_offset: str | None) -> dict[str, Any]:
        """Build the comparable time-basis receipt."""
        return {
            "clock_confidence": self.clock_confidence,
            "timezone_source": self.timezone_source,
            "source_timezone": self.source_timezone,
            "local_offset": local_offset,
        }


def scan_generation(log_lines: list[str]) -> dict[str, Any]:
    """Derive boot generation and file span without assuming a clean boot."""
    marker_indices = [i for i, line in enumerate(log_lines) if STARTUP_MARKER in line]
    dated = [m.group(1) for line in log_lines if (m := _TIMESTAMP_PATTERN.search(line)) is not None]
    marker_count = len(marker_indices)
    marker_source_time: str | None = None
    generation: str | None = None
    if marker_indices:
        marker_source_time = parse_event(log_lines[marker_indices[-1]]).source_time
    if marker_count == 1:
        segment = "".join(log_lines[marker_indices[0] :]).encode("utf-8")
        generation = "sha256:" + hashlib.sha256(segment).hexdigest()
        status = "DERIVED"
    elif marker_count > 1:
        status = "MIXED_GENERATIONS"
    else:
        status = "START_MARKER_MISSING"
    return {
        "status": status,
        "generation": generation,
        "startup_marker_count": marker_count,
        "startup_marker_source_time": marker_source_time,
        "marker_indices": marker_indices,
        "source_file_span": {
            "earliest": min(dated, default=None),
            "latest": max(dated, default=None),
        },
    }


@dataclass
class EvidenceSession:
    """Accumulates bounded evidence for one tool call over one log excerpt.

    Attributes:
        lines: The bounded log excerpt under analysis.
        log_source: Logical source label (current or previous log file).
        source_meta: Acquisition metadata (backend, truncated, max_lines).
        clock: The per-call trusted clock capture.
        rotated: True when the source is a rotated/previous log file.
        window: Requested source-local window (since, until); None sides open.
    """

    lines: list[str]
    log_source: str
    source_meta: dict[str, Any]
    clock: TrustedClock
    rotated: bool
    window: tuple[str | None, str | None] | None
    excerpt_sha256: str = field(init=False)
    generation: dict[str, Any] = field(init=False)
    observed_at_utc: str = field(init=False)
    dated: int = field(init=False, default=0)
    undated: int = field(init=False, default=0)
    unparsed: int = field(init=False, default=0)
    observed_in_window: int = field(init=False, default=0)
    observed_earliest: str | None = field(init=False, default=None)
    observed_latest: str | None = field(init=False, default=None)
    sighting_earliest: str | None = field(init=False, default=None)
    sighting_latest: str | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.truncated = bool(self.source_meta.get("truncated"))
        self.excerpt_sha256 = hashlib.sha256("".join(self.lines).encode("utf-8")).hexdigest()
        self.generation = scan_generation(self.lines)
        self.observed_at_utc = self.clock.now_utc.isoformat()
        self._raw: Counter[str] = Counter()
        self._patterns: dict[tuple[str, str, str], dict[str, Any]] = {}

    def parse(self, line: str) -> ParsedEvent:
        """Parse one line and account for its parsing-quality class."""
        event = parse_event(line)
        if event.parsing_class == "dated":
            self.dated += 1
        elif event.parsing_class == "undated":
            self.undated += 1
        else:
            self.unparsed += 1
        return event

    def in_window(self, source_time: str | None) -> bool:
        """Return True when a dated timestamp falls inside the requested window."""
        if self.window is None:
            return True
        if source_time is None:
            return False
        since, until = self.window
        if since is not None and source_time < since:
            return False
        return until is None or source_time <= until

    def observe(self, event: ParsedEvent, in_window: bool) -> None:
        """Account for a dated line's window coverage (all severities)."""
        if event.source_time is None or not in_window:
            return
        self.observed_in_window += 1
        ts = event.source_time
        if self.observed_earliest is None or ts < self.observed_earliest:
            self.observed_earliest = ts
        if self.observed_latest is None or ts > self.observed_latest:
            self.observed_latest = ts

    def consider(self, event: ParsedEvent, line_index: int) -> None:
        """Count one severity event into raw and derived pattern counts."""
        level = event.level or ""
        if level not in _SEVERITY_LEVELS:
            return
        self._raw[level] += 1
        key = (level, event.component, normalize_pattern(event.message))
        entry = self._patterns.setdefault(
            key,
            {
                "severity": level,
                "component": event.component,
                "pattern": key[2],
                "count": 0,
                "first_source_time": None,
                "last_source_time": None,
                "segments": set(),
            },
        )
        entry["count"] += 1
        entry["segments"].add(self._segment(line_index))
        if event.source_time is not None:
            ts = event.source_time
            if entry["first_source_time"] is None or ts < entry["first_source_time"]:
                entry["first_source_time"] = ts
            if entry["last_source_time"] is None or ts > entry["last_source_time"]:
                entry["last_source_time"] = ts
            if self.sighting_earliest is None or ts < self.sighting_earliest:
                self.sighting_earliest = ts
            if self.sighting_latest is None or ts > self.sighting_latest:
                self.sighting_latest = ts

    def _segment(self, line_index: int) -> int:
        marker_indices: list[int] = self.generation["marker_indices"]
        if not marker_indices:
            return -1
        return bisect_right(marker_indices, line_index) - 1

    def _generation_scope(self, segments: set[int]) -> str:
        marker_count = int(self.generation["startup_marker_count"])
        if marker_count == 0:
            return "UNKNOWN"
        if marker_count == 1 or len(segments) == 1:
            return "SINGLE"
        return "MIXED"

    def raw_level_counts(self) -> dict[str, int]:
        """Return the comparable raw severity counts observed so far."""
        return {"ERROR": self._raw["ERROR"], "WARNING": self._raw["WARNING"]}

    def pattern_entries(self, limit: int) -> tuple[list[dict[str, Any]], int]:
        """Return bounded pattern counts with per-signature boot scope."""
        entries = []
        for entry in sorted(
            self._patterns.values(),
            key=lambda e: (-e["count"], e["severity"], e["component"], e["pattern"]),
        ):
            entries.append(
                {
                    "severity": entry["severity"],
                    "component": entry["component"],
                    "pattern": entry["pattern"],
                    "count": entry["count"],
                    "first_source_time": entry["first_source_time"],
                    "last_source_time": entry["last_source_time"],
                    "generation_scope": self._generation_scope(entry["segments"]),
                }
            )
        return entries[:limit], max(0, len(entries) - limit)

    def coverage(self) -> dict[str, Any]:
        """Derive rotation coverage, cutoff reason and coverage status."""
        marker_count = int(self.generation["startup_marker_count"])
        if self.rotated:
            rotation_coverage = "LOG_ROTATED"
        elif marker_count == 0:
            rotation_coverage = "UNKNOWN"
        elif marker_count > 1:
            rotation_coverage = "MIXED_GENERATIONS"
        else:
            rotation_coverage = "SINGLE_GENERATION"
        if self.truncated:
            cutoff_reason = "TRUNCATED_MAX_LINES"
        elif self.rotated:
            cutoff_reason = "ROTATION"
        elif marker_count == 0:
            cutoff_reason = "UNKNOWN"
        else:
            cutoff_reason = "NONE"
        parsing_gap = self.undated + self.unparsed > 0
        if self.truncated or parsing_gap or marker_count != 1 or self.rotated:
            coverage_status = "PARTIAL"
        elif self.window is not None and not self._window_fully_covered():
            coverage_status = "WINDOW_UNVERIFIED"
        else:
            coverage_status = "SOURCE_SNAPSHOT_ONLY"
        return {
            "rotation_coverage": rotation_coverage,
            "cutoff_reason": cutoff_reason,
            "coverage_status": coverage_status,
        }

    def _window_fully_covered(self) -> bool:
        if self.window is None or self.observed_in_window == 0:
            return False
        since, until = self.window
        if until is None:
            return True
        if (
            since is not None
            and self.observed_earliest is not None
            and self.observed_earliest > since
        ):
            return False
        return not (
            until is not None and self.observed_latest is not None and self.observed_latest < until
        )

    def covered_utc_window(self) -> dict[str, Any]:
        """Return the UTC interval covered by severity sightings when trusted."""
        if self.clock.zone is not None and self.sighting_earliest is not None:
            first_utc = self.clock.to_utc(self.sighting_earliest)
            last_utc = self.clock.to_utc(self.sighting_latest or self.sighting_earliest)
            return {
                "status": "VERIFIED",
                "first_seen_utc": first_utc.isoformat() if first_utc else None,
                "last_seen_utc": last_utc.isoformat() if last_utc else None,
            }
        reason = _NO_TRUSTED_OFFSET_REASON if self.clock.zone is None else _NO_SIGHTINGS_REASON
        return {
            "status": "UNVERIFIED",
            "first_seen_utc": None,
            "last_seen_utc": None,
            "reason": reason,
        }

    def build(
        self,
        requested_utc_window: dict[str, Any],
        digest_extra: dict[str, Any],
        pattern_limit: int,
    ) -> dict[str, Any]:
        """Build the comparable evidence block with its stable identity."""
        cov = self.coverage()
        full_patterns, _ = self.pattern_entries(limit=len(self._patterns))
        parsing = {
            "dated_lines": self.dated,
            "undated_lines": self.undated,
            "unparsed_lines": self.unparsed,
        }
        generation = self.generation["generation"]
        block: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "log_source": self.log_source,
            "backend": self.source_meta.get("source", "unknown"),
            "observed_lines": len(self.lines),
            "source_limits": {
                "max_lines": self.source_meta.get("max_lines"),
                "truncated": self.truncated,
            },
            "excerpt_sha256": self.excerpt_sha256,
            "boot_generation": generation if isinstance(generation, str) else "UNKNOWN",
            "generation_status": self.generation["status"],
            "startup_marker_count": self.generation["startup_marker_count"],
            "startup_marker_source_time": self.generation["startup_marker_source_time"],
            "source_file_span": self.generation["source_file_span"],
            "rotation_coverage": cov["rotation_coverage"],
            "cutoff_reason": cov["cutoff_reason"],
            "coverage_status": cov["coverage_status"],
            "parsing": parsing,
            "time_basis": self.clock.basis(None),
            "requested_utc_window": requested_utc_window,
            "covered_utc_window": self.covered_utc_window(),
            "observed_source_window": {
                "earliest": self.observed_earliest,
                "latest": self.observed_latest,
            },
            "raw_level_counts": self.raw_level_counts(),
            "pattern_counts": full_patterns[:pattern_limit],
            "pattern_counts_omitted": max(0, len(full_patterns) - pattern_limit),
            "observed_at_utc": self.observed_at_utc,
        }
        digest_payload: dict[str, Any] = {
            key: value for key, value in block.items() if key != "observed_at_utc"
        }
        digest_payload["pattern_counts"] = full_patterns
        digest_payload.update(digest_extra)
        block["evidence_digest"] = hashlib.sha256(
            json.dumps(digest_payload, sort_keys=True, separators=(",", ":"), default=str).encode(
                "utf-8"
            )
        ).hexdigest()
        return block


def bounds_requested_utc_window(
    since: str | None, until: str | None, clock: TrustedClock
) -> dict[str, Any]:
    """Build the explicit-bounds requested UTC window receipt."""
    if clock.zone is not None and (since is not None or until is not None):
        since_utc = clock.to_utc(since) if since else None
        until_utc = clock.to_utc(until) if until else None
        return {
            "status": "VERIFIED",
            "since_utc": since_utc.isoformat() if since_utc else None,
            "until_utc": until_utc.isoformat() if until_utc else None,
        }
    if clock.zone is not None:
        return {"status": "NOT_REQUESTED", "since_utc": None, "until_utc": None}
    return {
        "status": "UNVERIFIED",
        "since_utc": None,
        "until_utc": None,
        "reason": _NO_TRUSTED_OFFSET_REASON,
    }


def hours_requested_utc_window(
    requested_hours: int, effective_hours: int, clock: TrustedClock
) -> dict[str, Any]:
    """Build the hours-based requested UTC window receipt with clamp provenance."""
    now_naive = clock.source_local_now_naive()
    since_naive = now_naive - timedelta(hours=effective_hours)
    receipt: dict[str, Any] = {
        "requested_hours": requested_hours,
        "effective_hours": effective_hours,
        "clamped": requested_hours != effective_hours,
    }
    if clock.zone is not None:
        since_utc = clock.to_utc_from_naive(since_naive)
        until_utc = clock.to_utc_from_naive(now_naive)
        receipt.update(
            {
                "status": "VERIFIED",
                "since_utc": since_utc.isoformat() if since_utc else None,
                "until_utc": until_utc.isoformat() if until_utc else None,
            }
        )
    else:
        receipt.update(
            {
                "status": "UNVERIFIED",
                "since_utc": None,
                "until_utc": None,
                "reason": _NO_TRUSTED_OFFSET_REASON,
            }
        )
    return receipt


def clean_claim(
    evidence: dict[str, Any], owner_outcome: str | None = None
) -> tuple[str, list[str]]:
    """Gate a clean claim on complete, trusted evidence.

    Args:
        evidence: The comparable evidence block produced by ``EvidenceSession.build``.
        owner_outcome: The adapter's owner outcome label; observed owner errors
            force the claim to UNVERIFIED.

    Returns:
        Tuple of ("VERIFIED" | "UNVERIFIED", missing-evidence reason list).
    """
    reasons: list[str] = []
    if owner_outcome == "OWNER_ERRORS_OBSERVED":
        reasons.append("OWNER_ERRORS_OBSERVED")
    if evidence["time_basis"]["clock_confidence"] != "CALLER_TRUSTED_OFFSET":
        reasons.append("TIME_BASIS_UNKNOWN")
    coverage_status = evidence["coverage_status"]
    if coverage_status == "PARTIAL":
        reasons.append("COVERAGE_PARTIAL")
    elif coverage_status == "WINDOW_UNVERIFIED":
        reasons.append("WINDOW_UNVERIFIED")
    generation_status = evidence["generation_status"]
    if generation_status == "START_MARKER_MISSING":
        reasons.append("START_MARKER_MISSING")
    elif generation_status == "MIXED_GENERATIONS":
        reasons.append("MIXED_GENERATIONS")
    if evidence["rotation_coverage"] == "LOG_ROTATED":
        reasons.append("LOG_ROTATED")
    parsing = evidence["parsing"]
    if parsing["undated_lines"] or parsing["unparsed_lines"]:
        reasons.append("INCOMPLETE_PARSING")
    return ("UNVERIFIED" if reasons else "VERIFIED"), reasons
