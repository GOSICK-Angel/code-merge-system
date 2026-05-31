"""Phase 3: offline prompt optimization harness (pure, no LLM)."""

from __future__ import annotations

from src.tools.prompt_optimizer import (
    BASELINE_ID,
    CostLedger,
    GoldenCase,
    build_report,
    propose_variants,
    render_report_markdown,
    score_candidates,
    select_winner,
)

_BASE = "You are the Judge. Decide per file."


# --- variant generation -----------------------------------------------------


def test_baseline_always_first_and_unmodified():
    cands = propose_variants("J-SYSTEM", _BASE, ["stepwise"])
    assert cands[0].candidate_id == BASELINE_ID
    assert cands[0].prompt_text == _BASE
    assert cands[0].strategy == BASELINE_ID


def test_each_strategy_appends_distinct_directive():
    cands = propose_variants("J-SYSTEM", _BASE, ["stepwise", "selfcheck"])
    ids = [c.candidate_id for c in cands]
    assert ids == [BASELINE_ID, "stepwise", "selfcheck"]
    for c in cands[1:]:
        assert c.prompt_text.startswith(_BASE)
        assert len(c.prompt_text) > len(_BASE)
    # distinct mutations
    assert cands[1].prompt_text != cands[2].prompt_text


def test_unknown_strategy_ignored():
    cands = propose_variants("J-SYSTEM", _BASE, ["nope", "stepwise"])
    assert [c.candidate_id for c in cands] == [BASELINE_ID, "stepwise"]


def test_default_strategies_is_all():
    cands = propose_variants("J-SYSTEM", _BASE)
    assert len(cands) >= 4  # baseline + 4 directives


# --- scoring ----------------------------------------------------------------


def _golden() -> list[GoldenCase]:
    return [
        GoldenCase(case_id="c1", expected_decision="take_target"),
        GoldenCase(case_id="c2", expected_decision="HUMAN_REQUIRED"),
    ]


def test_score_accuracy_per_candidate():
    cands = propose_variants("J-SYSTEM", _BASE, ["stepwise"])
    rollouts = {
        "baseline": {"c1": "take_target", "c2": "take_target"},  # 1/2
        "stepwise": {"c1": "take_target", "c2": "HUMAN_REQUIRED"},  # 2/2
    }
    scores = {s.candidate_id: s for s in score_candidates(cands, rollouts, _golden())}
    assert scores["baseline"].accuracy == 0.5
    assert scores["stepwise"].accuracy == 1.0
    assert scores["stepwise"].cases_scored == 2


def test_missing_rollout_is_unscored_not_zero():
    cands = propose_variants("J-SYSTEM", _BASE, ["stepwise"])
    rollouts = {"baseline": {"c1": "take_target", "c2": "HUMAN_REQUIRED"}}
    scores = {s.candidate_id: s for s in score_candidates(cands, rollouts, _golden())}
    assert scores["stepwise"].cases_scored == 0  # surfaced, not fabricated
    assert scores["stepwise"].accuracy == 0.0


# --- winner selection -------------------------------------------------------


def test_winner_requires_margin_over_baseline():
    cands = propose_variants("J-SYSTEM", _BASE, ["stepwise"])
    rollouts = {
        "baseline": {"c1": "take_target", "c2": "take_target"},  # 0.5
        "stepwise": {"c1": "take_target", "c2": "HUMAN_REQUIRED"},  # 1.0
    }
    scores = score_candidates(cands, rollouts, _golden())
    assert select_winner(scores, margin=0.02) == "stepwise"
    assert select_winner(scores, margin=0.9) is None  # gain below required margin


def test_no_winner_when_baseline_unscored():
    cands = propose_variants("J-SYSTEM", _BASE, ["stepwise"])
    scores = score_candidates(cands, {"stepwise": {"c1": "take_target"}}, _golden())
    assert select_winner(scores) is None


# --- report -----------------------------------------------------------------


def test_report_flags_unscored_and_never_auto_applies():
    cands = propose_variants("J-SYSTEM", _BASE, ["stepwise"])
    rollouts = {"baseline": {"c1": "take_target", "c2": "HUMAN_REQUIRED"}}
    report = build_report("J-SYSTEM", cands, _golden(), rollouts)
    md = render_report_markdown(report)
    assert "NOT auto-applied" in md
    assert any("Unscored" in n for n in report.notes)
    assert report.winner_id is None


def test_report_without_golden_notes_unscored():
    cands = propose_variants("J-SYSTEM", _BASE, ["stepwise"])
    report = build_report("J-SYSTEM", cands, [], {})
    assert any("No golden set" in n for n in report.notes)
    assert report.winner_id is None


def test_cost_ledger_accumulates_immutably():
    led = CostLedger()
    out = led.record(llm_calls=3, est_usd=0.12).record(llm_calls=2, est_usd=0.08)
    assert led.llm_calls == 0  # original untouched
    assert out.llm_calls == 5
    assert out.est_usd == 0.2
