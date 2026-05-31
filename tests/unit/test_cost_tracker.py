"""Cost tracker pricing/observability fixes.

Covers the three guarantees added for unknown-model handling:
  1. record() warns once (deduped) when a model has no pricing entry.
  2. mimo-v2.5-pro and the claude-4-7 family are priced (cost > 0).
  3. summary() surfaces zero-priced models via ``untracked_models``.
"""

from __future__ import annotations

import logging

from src.tools.cost_tracker import CostTracker, TokenUsage


def _usage() -> TokenUsage:
    return TokenUsage(input_tokens=10_000, output_tokens=2_000)


def test_known_model_is_priced() -> None:
    tracker = CostTracker()
    entry = tracker.record(
        "executor", "auto_merge", "claude-opus-4-6", "anthropic", _usage()
    )
    assert entry.cost_usd > 0
    assert tracker.summary()["untracked_models"] == []


def test_mimo_and_claude_4_7_are_priced() -> None:
    tracker = CostTracker()
    for model in ("mimo-v2.5-pro", "claude-opus-4-7", "claude-sonnet-4-7"):
        entry = tracker.record("judge", "judge_review", model, "openai", _usage())
        assert entry.cost_usd > 0, f"{model} should be priced"
    assert tracker.summary()["untracked_models"] == []


def test_unknown_model_records_zero_and_is_surfaced() -> None:
    tracker = CostTracker()
    entry = tracker.record("judge", "judge_review", "totally-unknown-v9", "x", _usage())
    assert entry.cost_usd == 0.0
    assert tracker.summary()["untracked_models"] == ["totally-unknown-v9"]


def test_unknown_model_warns_once(caplog) -> None:
    tracker = CostTracker()
    with caplog.at_level(logging.WARNING, logger="src.tools.cost_tracker"):
        tracker.record("judge", "judge_review", "unpriced-model", "x", _usage())
        tracker.record("judge", "judge_review", "unpriced-model", "x", _usage())
    warnings = [r for r in caplog.records if "unpriced-model" in r.getMessage()]
    assert len(warnings) == 1
