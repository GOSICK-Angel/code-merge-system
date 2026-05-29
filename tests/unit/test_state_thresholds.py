"""U-P2.14/15/16 — lock #27 path A regression net.

Verifies that ``MergeState.thresholds`` is a stable per-run snapshot of
``config.thresholds`` copied by ``InitializePhase``, and that
``ConflictAnalystAgent.run()`` drives ``analyze_file`` from that snapshot
instead of falling back to module defaults.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import git as _git
import pytest

from src.agents.conflict_analyst_agent import ConflictAnalystAgent
from src.core.phases.base import PhaseContext
from src.core.phases.initialize import InitializePhase
from src.models.config import (
    AgentLLMConfig,
    MergeConfig,
    OutputConfig,
    ThresholdConfig,
)
from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.models.plan import MergePhase, MergePlan, PhaseFileBatch, RiskSummary
from src.models.state import MergeState
from src.tools.git_tool import GitTool


def _make_repo(tmp_path: Path) -> Path:
    if not (tmp_path / ".git").exists():
        _git.Repo.init(str(tmp_path))
    return tmp_path


def _make_config(tmp_path: Path) -> MergeConfig:
    _make_repo(tmp_path)
    return MergeConfig(
        upstream_ref="upstream/main",
        fork_ref="fork/main",
        repo_path=str(tmp_path),
        output=OutputConfig(directory=str(tmp_path / "outputs")),
        thresholds=ThresholdConfig(chunked_aggregation_min_confidence=0.91),
    )


class TestMergeStateThresholdsField:
    """U-P2.14: MergeState.thresholds field exists with default factory."""

    def test_thresholds_field_default_is_threshold_config(self, tmp_path):
        config = _make_config(tmp_path)
        state = MergeState(config=config)
        assert isinstance(state.thresholds, ThresholdConfig)

    def test_thresholds_default_factory_independent_of_config(self, tmp_path):
        """Default-constructed thresholds must not alias config.thresholds —
        InitializePhase is the one canonical copy point."""
        config = _make_config(tmp_path)
        state = MergeState(config=config)
        assert state.thresholds is not state.config.thresholds
        # Default factory produces the schema defaults, not config overrides.
        assert state.thresholds.chunked_aggregation_min_confidence == 0.85


class TestInitializePhaseCopiesThresholds:
    """U-P2.15: InitializePhase copies config.thresholds to state.thresholds."""

    def test_initialize_phase_run_sync_copies_thresholds(self, tmp_path):
        config = _make_config(tmp_path)
        state = MergeState(config=config)

        # Baseline: state.thresholds is schema default (0.85), config has 0.91.
        assert state.thresholds.chunked_aggregation_min_confidence == 0.85
        assert config.thresholds.chunked_aggregation_min_confidence == 0.91

        phase = InitializePhase()

        ctx = MagicMock(spec=PhaseContext)
        ctx.git_tool = MagicMock(spec=GitTool)
        ctx.git_tool.get_merge_base.return_value = "abc123"
        ctx.git_tool.get_changed_files.return_value = []
        ctx.notify = MagicMock()

        # Patch the heavy side-effects InitializePhase invokes after the copy
        # so the test exercises only the thresholds-snapshot statement.
        phase._resolve_project_context = MagicMock()  # type: ignore[method-assign]
        phase._check_untracked_files = MagicMock()  # type: ignore[method-assign]

        try:
            phase._run_sync(state, ctx)
        except Exception:
            # We only care that the copy happened before any downstream
            # branching; downstream failures are out of scope for this test.
            pass

        assert state.thresholds.chunked_aggregation_min_confidence == 0.91
        # Copy must be a value snapshot, not a shared reference.
        assert state.thresholds is not config.thresholds


class TestConflictAnalystDrivesThresholdsFromState:
    """U-P2.16: ConflictAnalystAgent.run reads thresholds via restricted_view
    and passes them down to analyze_file (no default fallback)."""

    async def test_run_passes_view_thresholds_to_analyze_file(self, tmp_path):
        config = _make_config(tmp_path)
        state = MergeState(config=config)
        # Mirror what InitializePhase would do: snapshot config.thresholds.
        state.thresholds = config.thresholds.model_copy()

        state.merge_plan = MergePlan(
            created_at=datetime.now(),
            upstream_ref="upstream/main",
            fork_ref="fork/main",
            merge_base_commit="abc123",
            phases=[
                PhaseFileBatch(
                    batch_id="b1",
                    phase=MergePhase.ANALYSIS,
                    risk_level=RiskLevel.AUTO_RISKY,
                    file_paths=["a.py"],
                )
            ],
            risk_summary=RiskSummary(
                total_files=1,
                auto_safe_count=0,
                auto_risky_count=1,
                human_required_count=0,
                deleted_only_count=0,
                binary_count=0,
                excluded_count=0,
                estimated_auto_merge_rate=1.0,
            ),
            project_context_summary="",
        )
        state.file_diffs = [
            FileDiff(
                file_path="a.py",
                file_status=FileStatus.MODIFIED,
                risk_level=RiskLevel.AUTO_RISKY,
                risk_score=0.5,
                hunks=[],
            )
        ]

        agent = ConflictAnalystAgent(
            llm_config=AgentLLMConfig(api_key_env="ANTHROPIC_API_KEY"),
            git_tool=None,
        )

        captured: dict[str, object] = {}

        async def fake_analyze_file(
            file_diff,
            *,
            base_content,
            current_content,
            target_content,
            project_context="",
            forks_profile=None,
            chunk_size_chars=None,
            min_chunked_confidence=None,
            lang="en",
        ):
            captured["chunk_size_chars"] = chunk_size_chars
            captured["min_chunked_confidence"] = min_chunked_confidence
            return None

        agent.analyze_file = AsyncMock(side_effect=fake_analyze_file)  # type: ignore[method-assign]

        await agent.run(state)

        assert captured["min_chunked_confidence"] == pytest.approx(0.91)
        assert captured["chunk_size_chars"] == config.chunk_size_chars

    async def test_run_reads_state_thresholds_not_config_thresholds(self, tmp_path):
        """P2-2 (Phase 2 review-v1 P2): the snapshot must be the source of
        truth — mutating ``state.config.thresholds`` after init must NOT
        leak through to ``analyze_file``. Locks ``view.thresholds`` (run-
        time snapshot) as the authoritative path, not ``view.config.thresholds``.
        """
        config = _make_config(tmp_path)
        state = MergeState(config=config)
        # Init-phase snapshot at 0.91 (config's value at init time).
        state.thresholds = config.thresholds.model_copy()
        # Then mutate config.thresholds to a wildly different value. If the
        # agent ever reaches into config.thresholds, analyze_file will see
        # 0.5; if it reads state.thresholds (correct), it sees 0.91.
        state.config = state.config.model_copy(
            update={
                "thresholds": config.thresholds.model_copy(
                    update={"chunked_aggregation_min_confidence": 0.5}
                )
            }
        )
        assert (
            state.thresholds.chunked_aggregation_min_confidence
            != state.config.thresholds.chunked_aggregation_min_confidence
        )

        state.merge_plan = MergePlan(
            created_at=datetime.now(),
            upstream_ref="upstream/main",
            fork_ref="fork/main",
            merge_base_commit="abc123",
            phases=[
                PhaseFileBatch(
                    batch_id="b1",
                    phase=MergePhase.ANALYSIS,
                    risk_level=RiskLevel.AUTO_RISKY,
                    file_paths=["a.py"],
                )
            ],
            risk_summary=RiskSummary(
                total_files=1,
                auto_safe_count=0,
                auto_risky_count=1,
                human_required_count=0,
                deleted_only_count=0,
                binary_count=0,
                excluded_count=0,
                estimated_auto_merge_rate=1.0,
            ),
            project_context_summary="",
        )
        state.file_diffs = [
            FileDiff(
                file_path="a.py",
                file_status=FileStatus.MODIFIED,
                risk_level=RiskLevel.AUTO_RISKY,
                risk_score=0.5,
                hunks=[],
            )
        ]

        agent = ConflictAnalystAgent(
            llm_config=AgentLLMConfig(api_key_env="ANTHROPIC_API_KEY"),
            git_tool=None,
        )

        captured: dict[str, object] = {}

        async def fake_analyze_file(
            file_diff,
            *,
            base_content,
            current_content,
            target_content,
            project_context="",
            forks_profile=None,
            chunk_size_chars=None,
            min_chunked_confidence=None,
            lang="en",
        ):
            captured["min_chunked_confidence"] = min_chunked_confidence
            return None

        agent.analyze_file = AsyncMock(side_effect=fake_analyze_file)  # type: ignore[method-assign]
        await agent.run(state)

        assert captured["min_chunked_confidence"] == pytest.approx(0.91)
