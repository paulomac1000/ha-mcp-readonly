"""
Tests for tools/log_evidence.py primitives.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import tools.log_evidence as log_evidence_module


class TestEvidenceSessionParsing:
    """EvidenceSession.parse accounts for every parsing-quality class."""

    def _session(self) -> log_evidence_module.EvidenceSession:
        clock = log_evidence_module.TrustedClock.capture(None, None)
        return log_evidence_module.EvidenceSession(
            lines=[],
            log_source="home-assistant.log",
            source_meta={"source": "log_file", "truncated": False, "max_lines": 10},
            clock=clock,
            rotated=False,
            window=None,
        )

    def test_parse_counters_increment_per_quality_class(self):
        session = self._session()
        session.parse("2099-01-01 12:00:00 ERROR [c] dated line\n")
        session.parse("WARNING [c] undated severity-only line\n")
        session.parse("pure garbage continuation line\n")

        assert session.dated == 1
        assert session.undated == 1
        assert session.unparsed == 1

    def test_untrusted_clock_uses_host_local_wall_clock(self):
        clock = log_evidence_module.TrustedClock.capture(None, None)
        naive_now = clock.source_local_now_naive()
        local_now = datetime.now().replace(microsecond=0)

        assert naive_now.tzinfo is None
        assert abs((naive_now - local_now).total_seconds()) <= 5

    def test_open_ended_window_requires_source_to_reach_requested_start(self):
        clock = log_evidence_module.TrustedClock.capture("UTC", UTC)
        session = log_evidence_module.EvidenceSession(
            lines=[
                "2026-01-01 12:00:00 INFO [homeassistant.bootstrap] Starting Home Assistant\n",
                "2026-01-01 12:01:00 INFO [homeassistant.components.climate] ready\n",
            ],
            log_source="home-assistant.log",
            source_meta={"source": "log_file", "truncated": False, "max_lines": 10},
            clock=clock,
            rotated=False,
            window=("2026-01-01 10:00:00", None),
        )
        for line in session.lines:
            event = session.parse(line)
            session.observe(event, session.in_window(event.source_time))

        assert session.coverage()["coverage_status"] == "WINDOW_UNVERIFIED"

    def test_rolling_hour_window_uses_elapsed_utc_across_dst(self):
        zone = ZoneInfo("America/New_York")
        clock = log_evidence_module.TrustedClock(
            zone=zone,
            source_timezone="America/New_York",
            now_utc=datetime(2026, 3, 8, 7, 30, tzinfo=UTC),
        )

        receipt = log_evidence_module.hours_requested_utc_window(1, 1, clock)

        assert receipt["since_utc"] == "2026-03-08T06:30:00+00:00"
        assert receipt["until_utc"] == "2026-03-08T07:30:00+00:00"
        assert clock.rolling_cutoff_naive(1).strftime("%Y-%m-%d %H:%M:%S") == "2026-03-08 01:30:00"
