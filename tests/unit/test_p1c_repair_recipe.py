"""P1-C: verified-repair recipe library.

A deterministic repair operator (duplicate-symbol dedup) that fires AND whose
file the Judge ultimately passes mints a REPAIR_RECIPE memory entry, keyed by an
error_signature so a later run that opens a sibling file retrieves
"this error class was resolved here by operator X, verified by judge PASS".
Pure execution-grounding — no LLM decides success.
"""

from __future__ import annotations

from datetime import datetime

from src.agents.executor_agent import _record_applied_repair
from src.memory.models import MemoryEntryType
from src.memory.store import MemoryStore
from src.memory.summarizer import PhaseSummarizer
from src.models.judge import JudgeVerdict, VerdictType


def _verdict(passed: list[str], failed: list[str]) -> JudgeVerdict:
    return JudgeVerdict(
        verdict=VerdictType.PASS if not failed else VerdictType.FAIL,
        reviewed_files_count=len(passed) + len(failed),
        passed_files=list(passed),
        failed_files=list(failed),
        conditional_files=[],
        issues=[],
        critical_issues_count=0,
        high_issues_count=0,
        overall_confidence=0.9,
        summary="x",
        blocking_issues=[],
        timestamp=datetime(2026, 1, 1),
        judge_model="m",
    )


class _State:
    """Minimal stand-in carrying the fields the summarizer reads."""

    def __init__(self, verdict, applied_repairs):
        self.judge_verdict = verdict
        self.applied_repairs = applied_repairs
        self.judge_verdicts_log = []
        self.judge_repair_rounds = 0


# --- executor recording -----------------------------------------------------


def test_record_applied_repair_dedups_per_file_operator():
    state = _State(None, [])
    _record_applied_repair(state, "a.go", "dedup_top_level_symbols", "dup_symbol")
    _record_applied_repair(state, "a.go", "dedup_top_level_symbols", "dup_symbol")
    assert state.applied_repairs == [
        {
            "file_path": "a.go",
            "operator": "dedup_top_level_symbols",
            "error_class": "dup_symbol",
        }
    ]


# --- summarizer minting -----------------------------------------------------


def _repairs(*files: str) -> list[dict[str, str]]:
    return [
        {
            "file_path": f,
            "operator": "dedup_top_level_symbols",
            "error_class": "duplicate_top_level_symbol",
        }
        for f in files
    ]


def test_recipe_minted_only_for_judge_passed_file():
    state = _State(
        _verdict(passed=["pkg/x/a.go"], failed=["pkg/y/b.go"]),
        _repairs("pkg/x/a.go", "pkg/y/b.go"),
    )
    _, entries = PhaseSummarizer().summarize_judge_review(state)  # type: ignore[arg-type]
    recipes = [e for e in entries if e.entry_type == MemoryEntryType.REPAIR_RECIPE]
    assert len(recipes) == 1
    r = recipes[0]
    assert "pkg/x/a.go" in r.file_paths
    assert "duplicate_top_level_symbol" in r.tags
    assert "dedup_top_level_symbols" in r.tags
    # the failed file earns no recipe
    assert all("pkg/y/b.go" not in e.file_paths for e in recipes)


def test_recipe_signature_deduped_across_same_dir_layer():
    # two passed files in the same dir-layer with the same operator/error →
    # one recipe (the error_signature collapses them).
    state = _State(
        _verdict(passed=["pkg/x/a.go", "pkg/x/b.go"], failed=[]),
        _repairs("pkg/x/a.go", "pkg/x/b.go"),
    )
    _, entries = PhaseSummarizer().summarize_judge_review(state)  # type: ignore[arg-type]
    recipes = [e for e in entries if e.entry_type == MemoryEntryType.REPAIR_RECIPE]
    assert len(recipes) == 1


def test_no_recipe_when_disabled():
    state = _State(_verdict(passed=["pkg/x/a.go"], failed=[]), _repairs("pkg/x/a.go"))
    _, entries = PhaseSummarizer(repair_recipe_enabled=False).summarize_judge_review(
        state
    )  # type: ignore[arg-type]
    assert not [e for e in entries if e.entry_type == MemoryEntryType.REPAIR_RECIPE]


def test_no_recipe_without_applied_repairs():
    state = _State(_verdict(passed=["pkg/x/a.go"], failed=[]), [])
    _, entries = PhaseSummarizer().summarize_judge_review(state)  # type: ignore[arg-type]
    assert not [e for e in entries if e.entry_type == MemoryEntryType.REPAIR_RECIPE]


def test_no_recipe_without_verdict():
    state = _State(None, _repairs("pkg/x/a.go"))
    _, entries = PhaseSummarizer().summarize_judge_review(state)  # type: ignore[arg-type]
    assert not [e for e in entries if e.entry_type == MemoryEntryType.REPAIR_RECIPE]


# --- retrieval (existing memory channel) ------------------------------------


def test_recipe_is_retrievable_for_matching_file():
    state = _State(_verdict(passed=["pkg/x/a.go"], failed=[]), _repairs("pkg/x/a.go"))
    _, entries = PhaseSummarizer().summarize_judge_review(state)  # type: ignore[arg-type]
    store = MemoryStore()
    for e in entries:
        store = store.add_entry(e)
    hits = store.get_relevant_context(["pkg/x/a.go"])
    assert any(h.entry_type == MemoryEntryType.REPAIR_RECIPE for h in hits)
