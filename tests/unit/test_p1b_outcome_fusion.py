"""P1-B: fuse deterministic signals (judge + compile) into the per-file memory
outcome that drives OPP-5 write-back / P1-A suppression.

Recording moved out of judge_review into the orchestrator's post-phase memory
hook so the verdict reflects the post-judge build check. With the default
``["judge"]`` the split is byte-identical to the old behaviour; adding
``"compile"`` demotes a judge-passed compiled-language file when the build
check failed this run.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from src.core.orchestrator import Orchestrator
from src.memory.hit_tracker import MemoryHitTracker
from src.models.config import MemoryExtractionConfig
from src.models.judge import IssueSeverity, JudgeIssue, JudgeVerdict, VerdictType


def _verdict(passed, failed, *, build_failed: bool = False) -> JudgeVerdict:
    issues = []
    if build_failed:
        issues.append(
            JudgeIssue(
                file_path="(build)",
                issue_level=IssueSeverity.CRITICAL,
                issue_type="build_check_failed",
                description="compile broke",
                veto_condition="Build check failed",
            )
        )
    return JudgeVerdict(
        verdict=VerdictType.FAIL if build_failed else VerdictType.PASS,
        reviewed_files_count=len(passed) + len(failed),
        passed_files=list(passed),
        failed_files=list(failed),
        conditional_files=[],
        issues=issues,
        critical_issues_count=1 if build_failed else 0,
        high_issues_count=0,
        overall_confidence=0.9,
        summary="x",
        blocking_issues=[],
        timestamp=datetime(2026, 1, 1),
        judge_model="m",
    )


def _orch(sources: list[str], verdict: JudgeVerdict) -> Orchestrator:
    orch = Orchestrator.__new__(Orchestrator)
    orch._memory_hit_tracker = MemoryHitTracker()
    orch.config = SimpleNamespace(
        memory=MemoryExtractionConfig(writeback_signal_sources=sources)
    )
    orch._verdict_for_test = verdict  # convenience handle in assertions
    return orch


def _state(verdict: JudgeVerdict) -> SimpleNamespace:
    return SimpleNamespace(judge_verdict=verdict)


def _inject(tracker: MemoryHitTracker, file_path: str, entry_id: str) -> None:
    tracker.record_injection([file_path], [entry_id])


# --- config -----------------------------------------------------------------


def test_default_sources_is_judge_only():
    assert MemoryExtractionConfig().writeback_signal_sources == ["judge"]


# --- judge-only equivalence -------------------------------------------------


def test_judge_only_records_pass_and_fail_split():
    v = _verdict(["a.py"], ["b.py"])
    orch = _orch(["judge"], v)
    _inject(orch._memory_hit_tracker, "a.py", "e_pass")
    _inject(orch._memory_hit_tracker, "b.py", "e_fail")

    orch._record_memory_outcomes(_state(v))

    scores = orch._memory_hit_tracker.outcome_scores(min_observations=1)
    assert scores["e_pass"] == 1.0
    assert scores["e_fail"] == -1.0


def test_compile_source_but_build_passed_is_judge_equivalent():
    v = _verdict(["a.go"], [])  # no build_check_failed issue
    orch = _orch(["judge", "compile"], v)
    _inject(orch._memory_hit_tracker, "a.go", "e")
    orch._record_memory_outcomes(_state(v))
    assert orch._memory_hit_tracker.outcome_scores(min_observations=1)["e"] == 1.0


# --- compile fusion ---------------------------------------------------------


def test_compile_failure_demotes_compiled_passed_file():
    # judge passed a.go, but the build broke → a.go's memory is blamed, not
    # credited, even though judge said pass.
    v = _verdict(["a.go"], [], build_failed=True)
    orch = _orch(["judge", "compile"], v)
    _inject(orch._memory_hit_tracker, "a.go", "e_go")
    orch._record_memory_outcomes(_state(v))
    assert orch._memory_hit_tracker.outcome_scores(min_observations=1)["e_go"] == -1.0


def test_compile_failure_does_not_demote_non_compiled_passed_file():
    # a build break must not blame a Markdown file the compiler never touches.
    v = _verdict(["a.go", "README.md"], [], build_failed=True)
    orch = _orch(["judge", "compile"], v)
    _inject(orch._memory_hit_tracker, "a.go", "e_go")
    _inject(orch._memory_hit_tracker, "README.md", "e_md")
    orch._record_memory_outcomes(_state(v))
    scores = orch._memory_hit_tracker.outcome_scores(min_observations=1)
    assert scores["e_go"] == -1.0
    assert scores["e_md"] == 1.0  # non-compiled → still credited


def test_compile_not_in_sources_keeps_judge_credit_on_build_fail():
    # build failed, but operator opted out of compile fusion → judge split wins.
    v = _verdict(["a.go"], [], build_failed=True)
    orch = _orch(["judge"], v)
    _inject(orch._memory_hit_tracker, "a.go", "e_go")
    orch._record_memory_outcomes(_state(v))
    assert orch._memory_hit_tracker.outcome_scores(min_observations=1)["e_go"] == 1.0


# --- guards -----------------------------------------------------------------


def test_no_verdict_is_noop():
    orch = _orch(["judge", "compile"], _verdict([], []))
    orch._record_memory_outcomes(SimpleNamespace(judge_verdict=None))  # must not raise
    assert orch._memory_hit_tracker.outcome_scores(min_observations=1) == {}
