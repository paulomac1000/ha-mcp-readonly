"""
Tests for tools/log_evidence.py primitives.
"""

from datetime import datetime

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
