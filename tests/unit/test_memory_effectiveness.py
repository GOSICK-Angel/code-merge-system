"""P0 memory-effectiveness底座 — analyzer + models unit tests."""

import pytest
from pydantic import ValidationError

from src.memory.hit_tracker import MemoryHitTracker
from src.models.memory_effectiveness import (
    EntryEffectivenessItem,
    MemoryAblationComparison,
    MemoryEffectivenessReport,
)
from src.tools.memory_eval import (
    compare_memory_effectiveness,
    compute_memory_effectiveness,
)


def _tracker(injections: dict[str, list[str]], outcomes: list[tuple[str, bool]]):
    """Build a tracker with injections then judge outcomes applied."""
    tracker = MemoryHitTracker()
    for file_path, entry_ids in injections.items():
        tracker.record_injection([file_path], entry_ids)
    for file_path, success in outcomes:
        tracker.record_outcome(file_path, success=success)
    return tracker


# --- models -----------------------------------------------------------------


def test_entry_item_is_frozen():
    item = EntryEffectivenessItem(entry_id="e", pass_count=1, fail_count=0, score=1.0)
    with pytest.raises(ValidationError):
        item.score = 0.0  # type: ignore[misc]


def test_report_rejects_out_of_range_rates():
    with pytest.raises(ValidationError):
        MemoryEffectivenessReport(
            run_id="r",
            total_judged_decisions=0,
            overall_correct_rate=1.5,
            memory_influenced_decisions=0,
            correct_after_influence=0,
            harmful_influence_count=0,
            correct_rate_after_influence=0.0,
            harmful_influence_rate=0.0,
            total_tracked_entries=0,
            effective_observations=0,
        )


# --- injected_file_paths accessor ------------------------------------------


def test_injected_file_paths_accessor():
    tracker = MemoryHitTracker()
    assert tracker.injected_file_paths() == frozenset()
    tracker.record_injection(["a.py", "b.py"], ["e1"])
    tracker.record_injection(["a.py"], ["e2"])  # union, no dup key
    assert tracker.injected_file_paths() == frozenset({"a.py", "b.py"})


def test_injected_file_paths_ignores_empty_entry_ids():
    tracker = MemoryHitTracker()
    tracker.record_injection(["a.py"], [])  # no-op per record_injection contract
    assert tracker.injected_file_paths() == frozenset()


# --- compute_memory_effectiveness ------------------------------------------


def test_empty_tracker_and_verdict_is_all_zero():
    report = compute_memory_effectiveness(MemoryHitTracker(), [], [], run_id="r0")
    assert report.run_id == "r0"
    assert report.total_judged_decisions == 0
    assert report.overall_correct_rate == 0.0
    assert report.memory_influenced_decisions == 0
    assert report.correct_rate_after_influence == 0.0
    assert report.harmful_influence_rate == 0.0
    assert report.top_helpful == []
    assert report.top_harmful == []


def test_influenced_counts_intersection_of_injected_and_judged():
    # a.py injected+passed, b.py injected+failed, c.py judged-passed but NOT injected
    tracker = _tracker(
        {"a.py": ["good"], "b.py": ["bad"]},
        [("a.py", True), ("b.py", False)],
    )
    report = compute_memory_effectiveness(
        tracker, passed_files=["a.py", "c.py"], failed_files=["b.py"], run_id="r1"
    )
    # overall: 2 passed of 3 judged
    assert report.total_judged_decisions == 3
    assert report.overall_correct_rate == round(2 / 3, 4)
    # influenced: a.py (pass) + b.py (fail); c.py excluded (not injected)
    assert report.memory_influenced_decisions == 2
    assert report.correct_after_influence == 1
    assert report.harmful_influence_count == 1
    assert report.correct_rate_after_influence == 0.5
    assert report.harmful_influence_rate == 0.5
    # PR-0d: per-file lists persisted (sorted) for offline causal attribution
    assert report.passed_files == ["a.py", "c.py"]
    assert report.failed_files == ["b.py"]


def test_injected_file_not_judged_is_excluded_from_influence():
    tracker = _tracker({"x.py": ["e1"]}, [("x.py", True)])
    report = compute_memory_effectiveness(
        tracker, passed_files=["other.py"], failed_files=[], run_id="r2"
    )
    assert report.memory_influenced_decisions == 0
    assert report.correct_rate_after_influence == 0.0
    # but the per-entry outcome still tracked cross-run
    assert report.total_tracked_entries == 1


def test_top_helpful_and_harmful_propagated_from_summary():
    tracker = _tracker(
        {"a.py": ["good"], "b.py": ["bad"]},
        [("a.py", True), ("a.py", True), ("b.py", False)],
    )
    report = compute_memory_effectiveness(
        tracker, passed_files=["a.py"], failed_files=["b.py"], run_id="r3"
    )
    assert [i.entry_id for i in report.top_helpful] == ["good"]
    assert report.top_helpful[0].pass_count == 2
    assert report.top_helpful[0].score == 1.0
    assert [i.entry_id for i in report.top_harmful] == ["bad"]
    assert report.top_harmful[0].score == -1.0
    assert report.effective_observations == 3


def test_all_failed_judged_overall_rate_zero():
    tracker = _tracker({"a.py": ["e1"]}, [("a.py", False)])
    report = compute_memory_effectiveness(
        tracker, passed_files=[], failed_files=["a.py"], run_id="r4"
    )
    assert report.overall_correct_rate == 0.0
    assert report.harmful_influence_rate == 1.0
    assert report.correct_rate_after_influence == 0.0


# --- compare_memory_effectiveness (ablation) -------------------------------


def _report(run_id: str, overall: float, harmful_rate: float = 0.0):
    return MemoryEffectivenessReport(
        run_id=run_id,
        total_judged_decisions=10,
        overall_correct_rate=overall,
        memory_influenced_decisions=0,
        correct_after_influence=0,
        harmful_influence_count=0,
        correct_rate_after_influence=0.0,
        harmful_influence_rate=harmful_rate,
        total_tracked_entries=0,
        effective_observations=0,
    )


def test_compare_positive_lift_is_beneficial():
    cmp = compare_memory_effectiveness(_report("on", 0.90), _report("off", 0.82))
    assert isinstance(cmp, MemoryAblationComparison)
    assert cmp.memory_decision_lift == round(0.90 - 0.82, 4)
    assert cmp.memory_beneficial is True
    assert cmp.on_run_id == "on" and cmp.off_run_id == "off"


def test_compare_negative_lift_not_beneficial():
    cmp = compare_memory_effectiveness(_report("on", 0.80), _report("off", 0.88))
    assert cmp.memory_decision_lift == round(0.80 - 0.88, 4)
    assert cmp.memory_beneficial is False


def test_compare_zero_lift_not_beneficial():
    cmp = compare_memory_effectiveness(_report("on", 0.85), _report("off", 0.85))
    assert cmp.memory_decision_lift == 0.0
    assert cmp.memory_beneficial is False


def test_report_json_round_trip():
    tracker = _tracker({"a.py": ["e1"]}, [("a.py", True)])
    report = compute_memory_effectiveness(
        tracker, passed_files=["a.py"], failed_files=[], run_id="r5"
    )
    restored = MemoryEffectivenessReport.model_validate_json(report.model_dump_json())
    assert restored == report
