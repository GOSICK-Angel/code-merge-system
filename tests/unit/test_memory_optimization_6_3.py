"""6.3 优化项单测：

- O1: PlannerAgent 在 _classify_batch / _enhance_risk_scores 注入 memory_text
- O3: Orchestrator._should_llm_extract 周期性触发 (periodic_extraction_every_n_phases)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.planner_agent import PlannerAgent
from src.core.orchestrator import Orchestrator
from src.memory.models import MergeMemory, PhaseSummary
from src.memory.store import MemoryStore
from src.models.config import (
    AgentLLMConfig,
    MemoryExtractionConfig,
    MergeConfig,
)
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.state import MergeState
from src.tools.cost_tracker import CostTracker


def _make_planner() -> PlannerAgent:
    cfg = AgentLLMConfig(
        provider="anthropic",
        model="test-model",
        api_key_env="TEST_KEY",
    )
    with (
        patch("src.llm.client.LLMClientFactory.create"),
        patch.dict("os.environ", {"TEST_KEY": "sk-test-dummy"}),
    ):
        return PlannerAgent(llm_config=cfg)


def _attach_memory(agent: PlannerAgent, pattern_text: str, phase: str) -> None:
    memory = MergeMemory(
        codebase_profile={"language": "python"},
        phase_summaries={
            phase: PhaseSummary(
                phase=phase,
                files_processed=10,
                key_decisions=["seed"],
                patterns_discovered=[pattern_text],
            )
        },
    )
    agent.set_memory_store(MemoryStore(memory))
    agent.set_cost_tracker(CostTracker(), phase=phase)


_EMPTY_PLAN_JSON = (
    '{"phases": [], "risk_summary": {"total_files": 0, '
    '"auto_safe_count": 0, "auto_risky_count": 0, "human_required_count": 0, '
    '"deleted_only_count": 0, "binary_count": 0, "excluded_count": 0, '
    '"estimated_auto_merge_rate": 0.0, "top_risk_files": []}, '
    '"project_context_summary": "", "special_instructions": []}'
)


@pytest.mark.asyncio
async def test_classify_batch_injects_memory_when_store_present():
    agent = _make_planner()
    captured: list[str] = []

    async def fake_call(messages, system=None, **_):
        captured.append(messages[0]["content"])
        return _EMPTY_PLAN_JSON

    agent._call_llm_with_retry = AsyncMock(side_effect=fake_call)
    _attach_memory(agent, "KNOWN_PATTERN_FOR_TEST_PLANNER", "planning")

    fd = FileDiff(
        file_path="x.py",
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.1,
        change_category=FileChangeCategory.B,
    )
    await agent._classify_batch([fd], "ctx", "sys", 0, 1)

    assert captured, "LLM was not invoked"
    prompt = captured[0]
    assert "# Prior Knowledge" in prompt
    assert "KNOWN_PATTERN_FOR_TEST_PLANNER" in prompt


@pytest.mark.asyncio
async def test_classify_batch_skips_memory_when_store_absent():
    agent = _make_planner()
    captured: list[str] = []

    async def fake_call(messages, system=None, **_):
        captured.append(messages[0]["content"])
        return _EMPTY_PLAN_JSON

    agent._call_llm_with_retry = AsyncMock(side_effect=fake_call)
    agent.set_cost_tracker(CostTracker(), phase="planning")

    fd = FileDiff(
        file_path="x.py",
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.1,
        change_category=FileChangeCategory.B,
    )
    await agent._classify_batch([fd], "ctx", "sys", 0, 1)

    assert captured
    assert "# Prior Knowledge" not in captured[0]


@pytest.mark.asyncio
async def test_enhance_risk_scores_injects_memory_for_gray_zone():
    agent = _make_planner()
    captured: list[str] = []

    async def fake_call(messages, system=None, **_):
        captured.append(messages[0]["content"])
        return '{"llm_risk_score": 0.5, "rationale": "ok"}'

    agent._call_llm_with_retry = AsyncMock(side_effect=fake_call)
    _attach_memory(agent, "GRAY_ZONE_HINT_TEST", "planning")

    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="origin/feat")
    cfg.llm_assist.mode = "auto"
    fd = FileDiff(
        file_path="middle.py",
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.5,
        change_category=FileChangeCategory.C,
    )
    await agent._enhance_risk_scores([fd], cfg)

    assert captured, "LLM should be invoked for gray-zone file"
    assert "# Prior Knowledge" in captured[0]
    assert "GRAY_ZONE_HINT_TEST" in captured[0]


@pytest.mark.asyncio
async def test_enhance_risk_scores_fans_out_in_parallel():
    """Fix 5: 121-file gray-zone rescoring must not be sequential.

    Asserts the LLM-fanned-out path goes through ParallelFileRunner so a
    slow/wedged single call cannot freeze the whole planning phase the
    way it did during the forgejo run that idled for 21min with 0
    cost_summary entries.
    """
    from src.agents import planner_agent as planner_module

    agent = _make_planner()
    captured: list[str] = []

    async def fake_call(messages, system=None, **_):
        captured.append(messages[0]["content"])
        return '{"llm_risk_score": 0.7}'

    agent._call_llm_with_retry = AsyncMock(side_effect=fake_call)

    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="origin/feat")
    cfg.llm_assist.mode = "auto"
    diffs = [
        FileDiff(
            file_path=f"src/f{i}.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_RISKY,
            risk_score=0.5,
            change_category=FileChangeCategory.C,
        )
        for i in range(6)
    ]

    from unittest.mock import MagicMock

    spy = MagicMock(wraps=planner_module.ParallelFileRunner.from_api_key_env_list)
    with patch.object(planner_module.ParallelFileRunner, "from_api_key_env_list", spy):
        result = await agent._enhance_risk_scores(diffs, cfg)

    # ParallelFileRunner must be invoked (with > 1 gray-zone file so the
    # fan-out branch genuinely fires).
    assert spy.call_count >= 1
    # Every gray-zone file gets rescored (LLM called once per file).
    assert len(captured) == 6
    # Output preserves order — index ↔ file_path mapping survives the
    # parallel runner's result merging.
    for i, fd in enumerate(result):
        assert fd.file_path == f"src/f{i}.py"


@pytest.mark.asyncio
async def test_enhance_risk_scores_emits_progress_events():
    """Each gray-zone rescore completion fires a progress ActivityEvent
    so the Web UI can render planner activity instead of an opaque
    'PLANNING…' spinner during the (potentially-long) fan-out."""
    agent = _make_planner()

    async def fake_call(messages, system=None, **_):
        return '{"llm_risk_score": 0.6}'

    agent._call_llm_with_retry = AsyncMock(side_effect=fake_call)

    events: list[object] = []

    def collect(ev: object) -> None:
        events.append(ev)

    agent.set_activity_callback(collect)

    cfg = MergeConfig(upstream_ref="upstream/main", fork_ref="origin/feat")
    cfg.llm_assist.mode = "auto"
    diffs = [
        FileDiff(
            file_path=f"src/f{i}.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_RISKY,
            risk_score=0.5,
            change_category=FileChangeCategory.C,
        )
        for i in range(3)
    ]
    await agent._enhance_risk_scores(diffs, cfg)

    progress = [
        e
        for e in events
        if getattr(e, "action", None) == "llm_risk_rescore"
        and getattr(e, "event_type", None) == "progress"
    ]
    assert len(progress) >= 3, (
        f"expected >=3 rescore progress events, got {len(progress)}: {events}"
    )
    # Final event must report completed == total so the UI can clear
    # the in-flight indicator.
    final = progress[-1]
    extra = getattr(final, "extra", {}) or {}
    assert extra.get("total") == 3
    assert extra.get("completed") == 3


def _build_state(cfg: MergeConfig) -> MergeState:
    state = MergeState(config=cfg)
    state.errors = []
    state.plan_disputes = []
    state.judge_repair_rounds = 0
    state.coordinator_directives = []
    return state


def test_should_llm_extract_periodic_triggers_after_n_phases():
    cfg = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="origin/feat",
        memory=MemoryExtractionConfig(periodic_extraction_every_n_phases=3),
    )
    state = _build_state(cfg)

    self_obj = SimpleNamespace(config=cfg, _phases_since_last_extract=2)
    assert Orchestrator._should_llm_extract(self_obj, "auto_merge", state) is False

    self_obj._phases_since_last_extract = 3
    assert Orchestrator._should_llm_extract(self_obj, "auto_merge", state) is True


def test_should_llm_extract_periodic_disabled_by_default():
    cfg = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="origin/feat",
        memory=MemoryExtractionConfig(),
    )
    state = _build_state(cfg)

    assert cfg.memory.periodic_extraction_every_n_phases == 0
    self_obj = SimpleNamespace(config=cfg, _phases_since_last_extract=999)
    assert Orchestrator._should_llm_extract(self_obj, "auto_merge", state) is False


def test_should_llm_extract_respects_llm_extraction_off():
    cfg = MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="origin/feat",
        memory=MemoryExtractionConfig(
            llm_extraction=False, periodic_extraction_every_n_phases=1
        ),
    )
    state = _build_state(cfg)

    self_obj = SimpleNamespace(config=cfg, _phases_since_last_extract=10)
    assert Orchestrator._should_llm_extract(self_obj, "auto_merge", state) is False
