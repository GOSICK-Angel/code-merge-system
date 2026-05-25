"""Complexity-driven LLM-assist tier selection in PlannerAgent.

Covers the off/auto/always regimes, the uncertainty-band → tier-2
(single-file rescore) vs above-band → tier-3 (batch reclassify) split,
budget truncation by descending complexity, and the tier-3 categorical
override. Selection is isolated from the complexity formula (unit-tested
separately) by stubbing ``compute_complexity`` and the tier executors.
"""

from __future__ import annotations

from typing import Any, Callable
from unittest.mock import AsyncMock, patch

from src.agents import planner_agent as planner_module
from src.agents.planner_agent import PlannerAgent
from src.models.config import AgentLLMConfig, MergeConfig
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel


def _make_planner() -> PlannerAgent:
    cfg = AgentLLMConfig(
        provider="anthropic", model="test-model", api_key_env="TEST_KEY"
    )
    with (
        patch("src.llm.client.LLMClientFactory.create"),
        patch.dict("os.environ", {"TEST_KEY": "sk-test-dummy"}),
    ):
        return PlannerAgent(llm_config=cfg)


def _fd(path: str) -> FileDiff:
    return FileDiff(
        file_path=path,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.0,
        lines_added=5,
        lines_deleted=2,
        lines_changed=7,
        change_category=FileChangeCategory.C,
    )


def _patch_tiers(agent: PlannerAgent) -> tuple[AsyncMock, AsyncMock]:
    """Replace tier executors with pass-through spies that record the
    index list they were handed."""

    async def passthrough(
        enhanced_diffs: list[FileDiff],
        indices: list[int],
        config: MergeConfig,
        *rest: Any,
    ) -> list[FileDiff]:
        return enhanced_diffs

    t2 = AsyncMock(side_effect=passthrough)
    t3 = AsyncMock(side_effect=passthrough)
    agent._rescore_files = t2  # type: ignore[method-assign]
    agent._reclassify_files = t3  # type: ignore[method-assign]
    return t2, t3


def _complexity(mapping: dict[str, float]) -> Callable[..., float]:
    def _fn(fd: FileDiff, _config: Any, **_kw: Any) -> float:
        return mapping[fd.file_path]

    return _fn


def _cfg(mode: str = "auto", budget: int = 200) -> MergeConfig:
    cfg = MergeConfig(upstream_ref="u", fork_ref="f")
    cfg.llm_assist.mode = mode  # type: ignore[assignment]
    cfg.llm_assist.budget_max_files = budget
    return cfg


async def test_mode_off_returns_unchanged_without_llm() -> None:
    agent = _make_planner()
    t2, t3 = _patch_tiers(agent)
    diffs = [_fd("a.py")]
    out = await agent._enhance_risk_scores(diffs, _cfg(mode="off"))
    assert out == diffs
    t2.assert_not_called()
    t3.assert_not_called()


async def test_auto_splits_band_to_tier2_and_above_to_tier3() -> None:
    agent = _make_planner()
    t2, t3 = _patch_tiers(agent)
    diffs = [_fd("low.py"), _fd("mid.py"), _fd("high.py")]
    cmap = {"low.py": 0.1, "mid.py": 0.5, "high.py": 0.9}
    with patch.object(planner_module, "compute_complexity", _complexity(cmap)):
        await agent._enhance_risk_scores(diffs, _cfg())
    assert t2.call_args.args[1] == [1]  # mid only — low is below the band
    assert t3.call_args.args[1] == [2]  # high routes to strong-judgment tier


async def test_always_rescores_every_nonhigh_file() -> None:
    agent = _make_planner()
    t2, t3 = _patch_tiers(agent)
    diffs = [_fd("low.py"), _fd("mid.py"), _fd("high.py")]
    cmap = {"low.py": 0.1, "mid.py": 0.5, "high.py": 0.9}
    with patch.object(planner_module, "compute_complexity", _complexity(cmap)):
        await agent._enhance_risk_scores(diffs, _cfg(mode="always"))
    assert t2.call_args.args[1] == [0, 1]  # everyone not above the band
    assert t3.call_args.args[1] == [2]


async def test_budget_keeps_most_complex_files() -> None:
    agent = _make_planner()
    t2, t3 = _patch_tiers(agent)
    diffs = [_fd("a.py"), _fd("b.py"), _fd("c.py"), _fd("d.py")]
    # all inside the band → all eligible for tier 2; budget keeps the two
    # most complex (c=0.6, d=0.65).
    cmap = {"a.py": 0.4, "b.py": 0.5, "c.py": 0.6, "d.py": 0.65}
    with patch.object(planner_module, "compute_complexity", _complexity(cmap)):
        await agent._enhance_risk_scores(diffs, _cfg(budget=2))
    assert t2.call_args.args[1] == [2, 3]
    t3.assert_not_called()


async def test_tier3_override_wins_over_rule_classification() -> None:
    agent = _make_planner()
    classification = (
        '{"phases": [{"batch_id": "x", "phase": "human_review", '
        '"file_paths": ["danger.py"], "risk_level": "human_required"}]}'
    )
    agent._call_llm_with_retry = AsyncMock(return_value=classification)  # type: ignore[method-assign]
    diffs = [_fd("danger.py")]
    assert diffs[0].risk_level == RiskLevel.AUTO_SAFE
    with patch.object(
        planner_module, "compute_complexity", _complexity({"danger.py": 0.95})
    ):
        out = await agent._enhance_risk_scores(diffs, _cfg())
    assert out[0].risk_level == RiskLevel.HUMAN_REQUIRED
