"""Phase 0 — U-P0.1 RunBudgetExceeded exception class.

Lock anchors:
- approved-facts.md #8 BaseAgent `_current_phase: str` exists.
- plan/FINAL.md §2 Phase 0 delivery #1.
- test/FINAL.md U-P0.1.
"""

from __future__ import annotations

import pytest

from src.models.state import RunBudgetExceeded


def test_run_budget_exceeded_initializes_with_spent_limit_phase() -> None:
    exc = RunBudgetExceeded(spent=6.0, limit=5.0, phase="planning")
    assert exc.spent == 6.0
    assert exc.limit == 5.0
    assert exc.phase == "planning"


def test_run_budget_exceeded_str_contains_all_tokens() -> None:
    exc = RunBudgetExceeded(spent=6.0, limit=5.0, phase="planning")
    rendered = str(exc)
    assert "6.0" in rendered
    assert "5.0" in rendered
    assert "planning" in rendered


def test_run_budget_exceeded_is_subclass_of_exception() -> None:
    assert issubclass(RunBudgetExceeded, Exception)


def test_run_budget_exceeded_is_not_subclass_of_system_exit() -> None:
    assert not issubclass(RunBudgetExceeded, SystemExit)


def test_run_budget_exceeded_raise_and_catch_roundtrip() -> None:
    with pytest.raises(RunBudgetExceeded) as excinfo:
        raise RunBudgetExceeded(spent=10.5, limit=5.0, phase="conflict_analysis")
    assert excinfo.value.spent == 10.5
    assert excinfo.value.limit == 5.0
    assert excinfo.value.phase == "conflict_analysis"
