"""P1-2 tests: SentinelScanner fork-delta trigger.

Text-marker sentinels miss fork customizations that don't carry annotation
comments. Add a complementary signal: when fork-side ``lines_added +
lines_deleted`` (already populated in P0-1) crosses a threshold, treat the
file as customized regardless of marker presence.

Contract:
- ``check_fork_delta(file_diff, *, min_lines=50)`` returns one synthetic
  ``SentinelHit`` when fork delta >= ``min_lines``, else an empty list.
- The synthetic hit uses pattern ``__fork_delta_threshold__`` so downstream
  code can distinguish it from real text-marker hits.
- ``matched_text`` includes the actual fork-line count and the threshold,
  so plan-dispute messages remain self-explanatory.
- A ``None`` file_diff yields an empty list (executor calls this branch
  only when fd is present, but the helper must be safe).
"""

from __future__ import annotations

from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.tools.sentinel_scanner import SentinelHit, SentinelScanner


def _fd(added: int, deleted: int, path: str = "src/x.py") -> FileDiff:
    return FileDiff(
        file_path=path,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.1,
        lines_added=added,
        lines_deleted=deleted,
    )


class TestForkDeltaTriggers:
    def test_above_threshold_emits_synthetic_hit(self) -> None:
        scanner = SentinelScanner()
        hits = scanner.check_fork_delta(_fd(80, 20))
        assert len(hits) == 1
        hit = hits[0]
        assert isinstance(hit, SentinelHit)
        assert hit.pattern == "__fork_delta_threshold__"
        assert "100" in hit.matched_text
        assert "50" in hit.matched_text
        assert hit.file_path == "src/x.py"

    def test_at_threshold_emits_hit(self) -> None:
        scanner = SentinelScanner()
        hits = scanner.check_fork_delta(_fd(30, 20))
        assert len(hits) == 1

    def test_just_below_threshold_no_hit(self) -> None:
        scanner = SentinelScanner()
        assert scanner.check_fork_delta(_fd(30, 19)) == []

    def test_zero_delta_no_hit(self) -> None:
        scanner = SentinelScanner()
        assert scanner.check_fork_delta(_fd(0, 0)) == []

    def test_threshold_override(self) -> None:
        scanner = SentinelScanner()
        assert scanner.check_fork_delta(_fd(8, 4)) == []
        hits = scanner.check_fork_delta(_fd(8, 4), min_lines=10)
        assert len(hits) == 1
        assert "12" in hits[0].matched_text

    def test_none_file_diff_returns_empty(self) -> None:
        scanner = SentinelScanner()
        assert scanner.check_fork_delta(None) == []
