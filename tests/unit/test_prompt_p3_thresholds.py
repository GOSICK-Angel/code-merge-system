"""P3-3 config-sourced prompt thresholds (anti-drift).

- **risk_scoring** — the system prompt anchors what the score means and the
  user prompt states the numeric bands, sourced from ThresholdConfig
  (risk_score_low / risk_score_high) instead of leaving the model unanchored.
- **planner classification** — the "large diff" line threshold is interpolated
  from ThresholdConfig.classification_large_diff_lines (default 200) rather than
  hardcoded in the prompt text, so it cannot drift from config.
"""

from __future__ import annotations

from src.llm.prompts.planner_prompts import build_classification_prompt
from src.llm.prompts.risk_scoring_prompts import (
    RISK_SCORING_SYSTEM,
    build_risk_scoring_prompt,
)
from src.models.config import ThresholdConfig
from src.models.diff import FileDiff, FileStatus, RiskLevel


def _fd(path: str = "pkg/iso.ts") -> FileDiff:
    return FileDiff(
        file_path=path,
        file_status=FileStatus.MODIFIED,
        language="typescript",
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.4,
        lines_added=2,
        lines_deleted=1,
    )


class TestRiskScoringBands:
    def test_system_anchors_score_semantics(self) -> None:
        assert "[0.0, 1.0]" in RISK_SCORING_SYSTEM
        assert "auto-merge" in RISK_SCORING_SYSTEM
        assert "review" in RISK_SCORING_SYSTEM

    def test_default_bands_match_config(self) -> None:
        prompt = build_risk_scoring_prompt(_fd(), 0.4)
        cfg = ThresholdConfig()
        assert f"< {cfg.risk_score_low:.2f}" in prompt
        assert f">= {cfg.risk_score_high:.2f}" in prompt

    def test_custom_bands_are_threaded(self) -> None:
        prompt = build_risk_scoring_prompt(
            _fd(), 0.4, risk_score_low=0.2, risk_score_high=0.7
        )
        assert "< 0.20" in prompt
        assert ">= 0.70" in prompt
        assert "0.20–0.70" in prompt


class TestClassificationLargeDiffThreshold:
    def test_config_field_default_is_200(self) -> None:
        assert ThresholdConfig().classification_large_diff_lines == 200

    def test_default_prompt_uses_200(self) -> None:
        prompt = build_classification_prompt([_fd()], "ctx")
        assert "fork_lines_added + fork_lines_deleted < 200" in prompt
        assert (
            "upstream_lines_added + upstream_lines_deleted >= 200 AND "
            "category=both_changed" in prompt
        )

    def test_custom_threshold_is_interpolated(self) -> None:
        prompt = build_classification_prompt([_fd()], "ctx", large_diff_lines=120)
        assert "< 120" in prompt
        assert ">= 120" in prompt
        # the old hardcoded value must not survive in the authoritative rules
        assert "fork_lines_added + fork_lines_deleted < 200" not in prompt
        assert "fork_lines_deleted >= 200) touching" not in prompt
