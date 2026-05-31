"""P0: memory-effectiveness analyzer (read-only).

Pure functions that quantify whether injected memory improved merge
decisions, derived from the ``MemoryHitTracker`` per-file injection map and
the Judge's final pass/fail verdict. No LLM calls, no writes to any decision
path — purely diagnostic. Surfaced in the run report and persisted as a JSON
artifact so a memory on/off ablation can be compared after the fact.

``passed_files`` / ``failed_files`` are the same signal that feeds
``MemoryHitTracker.record_outcome`` (the Judge verdict), keeping "correct" /
"harmful" execution-grounded rather than self-reported.
"""

from __future__ import annotations

from collections.abc import Sequence

from src.memory.hit_tracker import MemoryHitTracker
from src.models.memory_effectiveness import (
    EntryEffectivenessItem,
    MemoryAblationComparison,
    MemoryEffectivenessReport,
)


def _as_int(value: object) -> int:
    """Coerce a loosely-typed ``summary()`` value into a non-negative int."""
    return int(value) if isinstance(value, (int, float)) else 0


def _items_from_outcomes(raw: object) -> list[EntryEffectivenessItem]:
    """Convert ``summary()['outcomes']['top_*']`` dicts into typed items."""
    items: list[EntryEffectivenessItem] = []
    if not isinstance(raw, list):
        return items
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        items.append(
            EntryEffectivenessItem(
                entry_id=str(entry.get("entry_id", "")),
                pass_count=int(entry.get("pass", 0)),
                fail_count=int(entry.get("fail", 0)),
                score=float(entry.get("score", 0.0)),
            )
        )
    return items


def compute_memory_effectiveness(
    tracker: MemoryHitTracker,
    passed_files: Sequence[str],
    failed_files: Sequence[str],
    run_id: str,
) -> MemoryEffectivenessReport:
    """Quantify memory's effect on this run's judged decisions.

    Influenced decisions are the judged files that also received a memory
    injection this run (``injected ∩ (passed ∪ failed)``). Rates are guarded
    against a zero denominator and return ``0.0`` when undefined.
    """
    injected = tracker.injected_file_paths()
    passed = frozenset(passed_files)
    failed = frozenset(failed_files)

    influenced_passed = injected & passed
    influenced_failed = injected & failed
    influenced = len(influenced_passed) + len(influenced_failed)
    correct = len(influenced_passed)
    harmful = len(influenced_failed)

    total_judged = len(passed) + len(failed)
    overall_correct_rate = (
        round(len(passed) / total_judged, 4) if total_judged > 0 else 0.0
    )
    correct_rate = round(correct / influenced, 4) if influenced > 0 else 0.0
    harmful_rate = round(harmful / influenced, 4) if influenced > 0 else 0.0

    summary = tracker.summary()
    outcomes = summary.get("outcomes")
    outcomes_dict = outcomes if isinstance(outcomes, dict) else {}

    return MemoryEffectivenessReport(
        run_id=run_id,
        total_judged_decisions=total_judged,
        overall_correct_rate=overall_correct_rate,
        memory_influenced_decisions=influenced,
        correct_after_influence=correct,
        harmful_influence_count=harmful,
        correct_rate_after_influence=correct_rate,
        harmful_influence_rate=harmful_rate,
        top_helpful=_items_from_outcomes(outcomes_dict.get("top_helpful")),
        top_harmful=_items_from_outcomes(outcomes_dict.get("top_harmful")),
        total_tracked_entries=_as_int(outcomes_dict.get("tracked_entries", 0)),
        effective_observations=_as_int(summary.get("effective_observations", 0)),
    )


def compare_memory_effectiveness(
    memory_on: MemoryEffectivenessReport,
    memory_off: MemoryEffectivenessReport,
) -> MemoryAblationComparison:
    """Diff two runs (memory on vs off) on the same dataset.

    ``memory_beneficial`` is the simple ``lift > 0`` convenience flag; the
    full acceptance gate (lift positive AND harmful rate not rising) lives in
    ``doc/evaluation/acceptance.md``.
    """
    lift = round(memory_on.overall_correct_rate - memory_off.overall_correct_rate, 4)
    return MemoryAblationComparison(
        on_run_id=memory_on.run_id,
        off_run_id=memory_off.run_id,
        overall_correct_rate_on=memory_on.overall_correct_rate,
        overall_correct_rate_off=memory_off.overall_correct_rate,
        memory_decision_lift=lift,
        harmful_influence_rate_on=memory_on.harmful_influence_rate,
        memory_beneficial=lift > 0.0,
    )
