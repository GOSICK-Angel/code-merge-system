import pytest
from unittest.mock import AsyncMock

from src.llm.prompts.risk_scoring_prompts import (
    build_risk_scoring_prompt,
    RISK_SCORING_SYSTEM,
)
from src.models.diff import FileDiff, FileStatus, RiskLevel, DiffHunk
from src.models.config import LLMAssistConfig


def _make_file_diff(
    file_path: str = "src/utils.py",
    risk_score: float = 0.4,
    lines_added: int = 20,
    lines_deleted: int = 5,
) -> FileDiff:
    return FileDiff(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        language="python",
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        lines_changed=lines_added + lines_deleted,
        risk_score=risk_score,
        risk_level=RiskLevel.AUTO_RISKY,
        hunks=[],
        is_security_sensitive=False,
    )


class TestRiskScoringPrompt:
    def test_prompt_contains_file_info(self) -> None:
        fd = _make_file_diff()
        prompt = build_risk_scoring_prompt(fd, 0.4)
        assert "src/utils.py" in prompt
        assert "0.400" in prompt

    def test_prompt_contains_json_format(self) -> None:
        fd = _make_file_diff()
        prompt = build_risk_scoring_prompt(fd, 0.5)
        assert "llm_risk_score" in prompt
        assert "JSON" in prompt

    def test_prompt_contains_extension(self) -> None:
        fd = _make_file_diff(file_path="app/main.ts")
        prompt = build_risk_scoring_prompt(fd, 0.3)
        assert ".ts" in prompt

    def test_prompt_no_extension(self) -> None:
        fd = _make_file_diff(file_path="Makefile")
        prompt = build_risk_scoring_prompt(fd, 0.3)
        assert "unknown" in prompt

    def test_prompt_with_hunks(self) -> None:
        hunk = DiffHunk(
            hunk_id="h1",
            start_line_current=10,
            end_line_current=20,
            start_line_target=10,
            end_line_target=25,
            content_current="old",
            content_target="new",
            content_base=None,
            has_conflict=True,
            conflict_marker_lines=[],
        )
        fd = _make_file_diff()
        fd_with_hunks = fd.model_copy(update={"hunks": [hunk]})
        prompt = build_risk_scoring_prompt(fd_with_hunks, 0.4)
        assert "Lines 10-20" in prompt
        assert "conflict=yes" in prompt

    def test_system_prompt_exists(self) -> None:
        assert "risk" in RISK_SCORING_SYSTEM.lower()


class TestLLMAssistConfig:
    def test_defaults(self) -> None:
        config = LLMAssistConfig()
        assert config.mode == "auto"
        assert config.budget_max_files == 200
        assert config.uncertainty_low == 0.30
        assert config.uncertainty_high == 0.70
        assert config.rule_weight == 0.6

    def test_custom_values(self) -> None:
        config = LLMAssistConfig(
            mode="always", uncertainty_low=0.3, uncertainty_high=0.7, rule_weight=0.5
        )
        assert config.mode == "always"
        assert config.rule_weight == 0.5

    def test_validation_bounds(self) -> None:
        with pytest.raises(Exception):
            LLMAssistConfig(uncertainty_low=-0.1)
        with pytest.raises(Exception):
            LLMAssistConfig(uncertainty_high=1.5)
        with pytest.raises(Exception):
            LLMAssistConfig(rule_weight=2.0)
        with pytest.raises(Exception):
            LLMAssistConfig(mode="sometimes")


class TestBlendedScoring:
    def test_blend_formula(self) -> None:
        rule_score = 0.4
        llm_score = 0.8
        rule_weight = 0.6
        expected = 0.6 * 0.4 + 0.4 * 0.8
        result = rule_weight * rule_score + (1.0 - rule_weight) * llm_score
        assert abs(result - expected) < 0.001

    def test_blend_with_equal_weights(self) -> None:
        rule_score = 0.3
        llm_score = 0.7
        rule_weight = 0.5
        result = rule_weight * rule_score + (1.0 - rule_weight) * llm_score
        assert abs(result - 0.5) < 0.001

    def test_uncertainty_band_detection(self) -> None:
        config = LLMAssistConfig(uncertainty_low=0.25, uncertainty_high=0.65)
        assert config.uncertainty_low <= 0.4 <= config.uncertainty_high
        assert not (config.uncertainty_low <= 0.1 <= config.uncertainty_high)
        assert not (config.uncertainty_low <= 0.9 <= config.uncertainty_high)


class TestComputeLLMRiskScore:
    @pytest.mark.asyncio
    async def test_successful_llm_scoring(self) -> None:
        from src.tools.file_classifier import compute_llm_risk_score

        fd = _make_file_diff(risk_score=0.4)
        mock_client = AsyncMock()
        mock_client.complete.return_value = '{"llm_risk_score": 0.7, "reasoning": "complex changes", "risk_factors": ["size"]}'

        result = await compute_llm_risk_score(fd, mock_client, 0.4, rule_weight=0.6)
        expected = round(0.6 * 0.4 + 0.4 * 0.7, 3)
        assert abs(result - expected) < 0.001

    @pytest.mark.asyncio
    async def test_fallback_on_error(self) -> None:
        from src.tools.file_classifier import compute_llm_risk_score

        fd = _make_file_diff(risk_score=0.4)
        mock_client = AsyncMock()
        mock_client.complete.side_effect = RuntimeError("API error")

        result = await compute_llm_risk_score(fd, mock_client, 0.4)
        assert result == 0.4

    @pytest.mark.asyncio
    async def test_strips_code_fence(self) -> None:
        from src.tools.file_classifier import compute_llm_risk_score

        fd = _make_file_diff(risk_score=0.5)
        mock_client = AsyncMock()
        mock_client.complete.return_value = '```json\n{"llm_risk_score": 0.6, "reasoning": "ok", "risk_factors": []}\n```'

        result = await compute_llm_risk_score(fd, mock_client, 0.5, rule_weight=0.6)
        expected = round(0.6 * 0.5 + 0.4 * 0.6, 3)
        assert abs(result - expected) < 0.001

    @pytest.mark.asyncio
    async def test_clamps_llm_score(self) -> None:
        from src.tools.file_classifier import compute_llm_risk_score

        fd = _make_file_diff(risk_score=0.3)
        mock_client = AsyncMock()
        mock_client.complete.return_value = (
            '{"llm_risk_score": 1.5, "reasoning": "overflow", "risk_factors": []}'
        )

        result = await compute_llm_risk_score(fd, mock_client, 0.3, rule_weight=0.6)
        expected = round(0.6 * 0.3 + 0.4 * 1.0, 3)
        assert abs(result - expected) < 0.001
        assert result <= 1.0
