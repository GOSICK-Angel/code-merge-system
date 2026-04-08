"""Tests for P0: ABCDE three-way classification and layered merge ordering."""

from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.models.decision import MergeDecision
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.plan import (
    CategorySummary,
    DEFAULT_LAYERS,
    MergeLayer,
    MergePlan,
    MergePhase,
    PhaseFileBatch,
    RiskSummary,
)
from src.models.config import MergeConfig, MergeLayerConfig
from src.models.state import MergeState
from src.tools.file_classifier import (
    classify_three_way,
    classify_all_files,
    category_summary,
)


def _mock_git(file_hashes: dict[str, dict[str, str | None]]) -> MagicMock:
    git = MagicMock()

    def get_file_hash(ref: str, path: str) -> str | None:
        ref_data = file_hashes.get(ref, {})
        return ref_data.get(path)

    git.get_file_hash = MagicMock(side_effect=get_file_hash)

    all_files: dict[str, list[str]] = {}
    for ref, paths in file_hashes.items():
        all_files[ref] = [p for p, h in paths.items() if h is not None]
    git.list_files = MagicMock(side_effect=lambda ref: all_files.get(ref, []))

    return git


class TestClassifyThreeWay:
    def test_a_class_unchanged(self):
        git = _mock_git(
            {
                "base": {"f.py": "aaa"},
                "head": {"f.py": "aaa"},
                "upstream": {"f.py": "aaa"},
            }
        )
        assert classify_three_way("f.py", "base", "head", "upstream", git) == (
            FileChangeCategory.A
        )

    def test_a_class_both_changed_same(self):
        git = _mock_git(
            {
                "base": {"f.py": "old"},
                "head": {"f.py": "new"},
                "upstream": {"f.py": "new"},
            }
        )
        assert classify_three_way("f.py", "base", "head", "upstream", git) == (
            FileChangeCategory.A
        )

    def test_b_class_upstream_only(self):
        git = _mock_git(
            {
                "base": {"f.py": "old"},
                "head": {"f.py": "old"},
                "upstream": {"f.py": "new"},
            }
        )
        assert classify_three_way("f.py", "base", "head", "upstream", git) == (
            FileChangeCategory.B
        )

    def test_c_class_both_changed(self):
        git = _mock_git(
            {
                "base": {"f.py": "old"},
                "head": {"f.py": "head_change"},
                "upstream": {"f.py": "up_change"},
            }
        )
        assert classify_three_way("f.py", "base", "head", "upstream", git) == (
            FileChangeCategory.C
        )

    def test_d_missing_upstream_new(self):
        git = _mock_git(
            {
                "base": {"f.py": None},
                "head": {"f.py": None},
                "upstream": {"f.py": "new_file"},
            }
        )
        assert classify_three_way("f.py", "base", "head", "upstream", git) == (
            FileChangeCategory.D_MISSING
        )

    def test_d_extra_current_only_file(self):
        git = _mock_git(
            {
                "base": {"f.py": None},
                "head": {"f.py": "custom_file"},
                "upstream": {"f.py": None},
            }
        )
        assert classify_three_way("f.py", "base", "head", "upstream", git) == (
            FileChangeCategory.D_EXTRA
        )

    def test_e_class_current_only_change(self):
        git = _mock_git(
            {
                "base": {"f.py": "old"},
                "head": {"f.py": "custom"},
                "upstream": {"f.py": "old"},
            }
        )
        assert classify_three_way("f.py", "base", "head", "upstream", git) == (
            FileChangeCategory.E
        )

    def test_both_missing(self):
        git = _mock_git(
            {
                "base": {},
                "head": {},
                "upstream": {},
            }
        )
        assert classify_three_way("f.py", "base", "head", "upstream", git) == (
            FileChangeCategory.A
        )


class TestClassifyAllFiles:
    def test_mixed_categories(self):
        git = _mock_git(
            {
                "base": {
                    "same.py": "x",
                    "upstream_mod.py": "old",
                    "both_mod.py": "old",
                    "current_mod.py": "old",
                },
                "head": {
                    "same.py": "x",
                    "upstream_mod.py": "old",
                    "both_mod.py": "head_v",
                    "current_mod.py": "head_v",
                    "extra.py": "custom",
                },
                "upstream": {
                    "same.py": "x",
                    "upstream_mod.py": "new",
                    "both_mod.py": "up_v",
                    "current_mod.py": "old",
                    "new_file.py": "fresh",
                },
            }
        )
        result = classify_all_files("base", "head", "upstream", git)

        assert result["same.py"] == FileChangeCategory.A
        assert result["upstream_mod.py"] == FileChangeCategory.B
        assert result["both_mod.py"] == FileChangeCategory.C
        assert result["current_mod.py"] == FileChangeCategory.E
        assert result["extra.py"] == FileChangeCategory.D_EXTRA
        assert result["new_file.py"] == FileChangeCategory.D_MISSING

    def test_category_summary(self):
        cats = {
            "a1.py": FileChangeCategory.A,
            "a2.py": FileChangeCategory.A,
            "b1.py": FileChangeCategory.B,
            "c1.py": FileChangeCategory.C,
            "d1.py": FileChangeCategory.D_MISSING,
            "e1.py": FileChangeCategory.D_EXTRA,
        }
        result = category_summary(cats)
        assert result["unchanged"] == 2
        assert result["upstream_only"] == 1
        assert result["both_changed"] == 1
        assert result["upstream_new"] == 1
        assert result["current_only"] == 1
        assert result["current_only_change"] == 0


class TestMergeLayerModel:
    def test_default_layers_parse(self):
        layers = [MergeLayer(**data) for data in DEFAULT_LAYERS]
        assert len(layers) == 10
        assert layers[0].name == "infrastructure"
        assert layers[0].layer_id == 0
        assert layers[9].name == "sdk_plugins"
        assert layers[9].layer_id == 9

    def test_layer_dependencies(self):
        layers = [MergeLayer(**data) for data in DEFAULT_LAYERS]
        deps_map = {layer.layer_id: layer.depends_on for layer in layers}
        assert deps_map[0] == []
        assert deps_map[1] == [0]
        assert deps_map[4] == [3]
        assert 4 in deps_map[8]
        assert 5 in deps_map[8]

    def test_custom_layers_config(self):
        config = MergeLayerConfig(
            custom_layers=[
                {
                    "layer_id": 0,
                    "name": "all",
                    "path_patterns": ["**/*"],
                }
            ]
        )
        assert len(config.custom_layers) == 1


class TestPhaseFileBatchExtensions:
    def test_batch_with_layer_and_category(self):
        batch = PhaseFileBatch(
            batch_id="test",
            phase=MergePhase.AUTO_MERGE,
            file_paths=["a.py"],
            risk_level=RiskLevel.AUTO_SAFE,
            layer_id=2,
            change_category=FileChangeCategory.B,
        )
        assert batch.layer_id == 2
        assert batch.change_category == FileChangeCategory.B

    def test_batch_backward_compat(self):
        batch = PhaseFileBatch(
            batch_id="test",
            phase=MergePhase.AUTO_MERGE,
            file_paths=["a.py"],
            risk_level=RiskLevel.AUTO_SAFE,
        )
        assert batch.layer_id is None
        assert batch.change_category is None


class TestCategorySummaryModel:
    def test_category_summary_model(self):
        cs = CategorySummary(
            total_files=100,
            a_unchanged=50,
            b_upstream_only=20,
            c_both_changed=10,
            d_missing=5,
            d_extra=10,
            e_current_only=5,
        )
        assert cs.total_files == 100
        assert cs.b_upstream_only == 20


class TestMergePlanExtensions:
    def test_plan_with_layers_and_category(self):
        plan = MergePlan(
            created_at=datetime.now(),
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            merge_base_commit="abc",
            phases=[],
            risk_summary=RiskSummary(
                total_files=0,
                auto_safe_count=0,
                auto_risky_count=0,
                human_required_count=0,
                deleted_only_count=0,
                binary_count=0,
                excluded_count=0,
                estimated_auto_merge_rate=0.0,
            ),
            category_summary=CategorySummary(total_files=100, a_unchanged=80),
            layers=[MergeLayer(layer_id=0, name="infra")],
            project_context_summary="test",
        )
        assert plan.version == "2.0"
        assert plan.category_summary is not None
        assert plan.category_summary.a_unchanged == 80
        assert len(plan.layers) == 1

    def test_plan_backward_compat(self):
        plan = MergePlan(
            created_at=datetime.now(),
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
            merge_base_commit="abc",
            phases=[],
            risk_summary=RiskSummary(
                total_files=0,
                auto_safe_count=0,
                auto_risky_count=0,
                human_required_count=0,
                deleted_only_count=0,
                binary_count=0,
                excluded_count=0,
                estimated_auto_merge_rate=0.0,
            ),
            project_context_summary="test",
        )
        assert plan.category_summary is None
        assert plan.layers == []


class TestFileDiffChangeCategory:
    def test_file_diff_with_category(self):
        fd = FileDiff(
            file_path="src/foo.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.1,
            change_category=FileChangeCategory.B,
        )
        assert fd.change_category == FileChangeCategory.B

    def test_file_diff_category_default_none(self):
        fd = FileDiff(
            file_path="src/foo.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.1,
        )
        assert fd.change_category is None


class TestMergeStateCategories:
    def test_state_has_file_categories(self):
        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        assert state.file_categories == {}
        assert state.merge_base_commit == ""

    def test_state_file_categories_populated(self):
        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        state.file_categories = {
            "a.py": FileChangeCategory.B,
            "b.py": FileChangeCategory.C,
        }
        assert len(state.file_categories) == 2
        assert state.file_categories["a.py"] == FileChangeCategory.B


class TestPlannerLayeredPlan:
    def test_planner_generates_layered_plan(self):
        from src.agents.planner_agent import PlannerAgent
        from src.models.config import AgentLLMConfig

        with patch("src.llm.client.LLMClientFactory.create"):
            planner = PlannerAgent(AgentLLMConfig())

        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        state = MergeState(config=config)
        state.merge_base_commit = "abc123"
        state.file_categories = {
            "docker/Dockerfile": FileChangeCategory.B,
            "docker/compose.yaml": FileChangeCategory.C,
            "api/pyproject.toml": FileChangeCategory.B,
            "api/core/engine.py": FileChangeCategory.C,
            "api/core/new_module.py": FileChangeCategory.D_MISSING,
            "api/services/auth.py": FileChangeCategory.C,
            "web/app/page.tsx": FileChangeCategory.B,
            "unchanged.py": FileChangeCategory.A,
            "custom_only.py": FileChangeCategory.E,
        }

        fd_core = FileDiff(
            file_path="api/core/engine.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_RISKY,
            risk_score=0.5,
            change_category=FileChangeCategory.C,
        )
        fd_docker = FileDiff(
            file_path="docker/compose.yaml",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.2,
            change_category=FileChangeCategory.C,
        )
        fd_service = FileDiff(
            file_path="api/services/auth.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.HUMAN_REQUIRED,
            risk_score=0.9,
            is_security_sensitive=True,
            change_category=FileChangeCategory.C,
        )
        object.__setattr__(state, "_file_diffs", [fd_core, fd_docker, fd_service])

        plan = planner._build_layered_plan([fd_core, fd_docker, fd_service], state)

        assert plan.version == "2.0"
        assert plan.category_summary is not None
        assert plan.category_summary.b_upstream_only == 3
        assert plan.category_summary.c_both_changed == 3
        assert plan.category_summary.d_missing == 1
        assert len(plan.layers) == 10

        all_files_in_plan = []
        for phase in plan.phases:
            all_files_in_plan.extend(phase.file_paths)

        assert "docker/Dockerfile" in all_files_in_plan
        assert "api/core/new_module.py" in all_files_in_plan
        assert "api/services/auth.py" in all_files_in_plan
        assert "unchanged.py" not in all_files_in_plan
        assert "custom_only.py" not in all_files_in_plan

        layer_ids = [p.layer_id for p in plan.phases if p.layer_id is not None]
        layer_order = []
        for lid in layer_ids:
            if lid not in layer_order:
                layer_order.append(lid)
        layer_by_id = {ly.layer_id: ly for ly in plan.layers}
        for lid in layer_order:
            ly = layer_by_id.get(lid)
            if ly is None:
                continue
            idx = layer_order.index(lid)
            for dep in ly.depends_on:
                if dep in layer_order:
                    dep_idx = layer_order.index(dep)
                    assert dep_idx < idx, (
                        f"Layer {lid} appears before dependency {dep}: {layer_order}"
                    )

        b_phases = [p for p in plan.phases if p.change_category == FileChangeCategory.B]
        for bp in b_phases:
            assert bp.risk_level == RiskLevel.AUTO_SAFE
            assert bp.phase == MergePhase.AUTO_MERGE

        human_phases = [
            p for p in plan.phases if p.risk_level == RiskLevel.HUMAN_REQUIRED
        ]
        assert len(human_phases) >= 1
        assert "api/services/auth.py" in human_phases[0].file_paths


class TestExecutorCategoryDispatch:
    def test_select_strategy_b_class(self):
        from src.agents.executor_agent import ExecutorAgent
        from src.models.config import AgentLLMConfig

        with patch("src.llm.client.LLMClientFactory.create"):
            executor = ExecutorAgent(AgentLLMConfig())
        strategy = executor._select_strategy_by_category(
            FileChangeCategory.B, RiskLevel.AUTO_SAFE
        )
        assert strategy == MergeDecision.TAKE_TARGET

    def test_select_strategy_d_missing(self):
        from src.agents.executor_agent import ExecutorAgent
        from src.models.config import AgentLLMConfig
        from src.models.decision import MergeDecision

        with patch("src.llm.client.LLMClientFactory.create"):
            executor = ExecutorAgent(AgentLLMConfig())
        strategy = executor._select_strategy_by_category(
            FileChangeCategory.D_MISSING, RiskLevel.AUTO_SAFE
        )
        assert strategy == MergeDecision.TAKE_TARGET

    def test_select_strategy_a_class_skip(self):
        from src.agents.executor_agent import ExecutorAgent
        from src.models.config import AgentLLMConfig
        from src.models.decision import MergeDecision

        with patch("src.llm.client.LLMClientFactory.create"):
            executor = ExecutorAgent(AgentLLMConfig())
        strategy = executor._select_strategy_by_category(
            FileChangeCategory.A, RiskLevel.AUTO_SAFE
        )
        assert strategy == MergeDecision.SKIP

    def test_select_strategy_e_class_skip(self):
        from src.agents.executor_agent import ExecutorAgent
        from src.models.config import AgentLLMConfig
        from src.models.decision import MergeDecision

        with patch("src.llm.client.LLMClientFactory.create"):
            executor = ExecutorAgent(AgentLLMConfig())
        strategy = executor._select_strategy_by_category(
            FileChangeCategory.E, RiskLevel.AUTO_SAFE
        )
        assert strategy == MergeDecision.SKIP

    def test_select_strategy_none_fallback(self):
        from src.agents.executor_agent import ExecutorAgent
        from src.models.config import AgentLLMConfig
        from src.models.decision import MergeDecision

        with patch("src.llm.client.LLMClientFactory.create"):
            executor = ExecutorAgent(AgentLLMConfig())
        strategy = executor._select_strategy_by_category(None, RiskLevel.DELETED_ONLY)
        assert strategy == MergeDecision.SKIP


class TestTopologicalSortLayers:
    """Tests for topological_sort_layers()."""

    def test_default_layers_sorted_correctly(self):
        from src.models.plan import topological_sort_layers

        layers = [MergeLayer(**d) for d in DEFAULT_LAYERS]
        result = topological_sort_layers(layers)
        result_ids = [ly.layer_id for ly in result]
        for ly in result:
            idx = result_ids.index(ly.layer_id)
            for dep in ly.depends_on:
                dep_idx = result_ids.index(dep)
                assert dep_idx < idx, (
                    f"Layer {ly.layer_id} appears before its dependency {dep}"
                )

    def test_reverse_id_order_with_forward_deps(self):
        from src.models.plan import topological_sort_layers

        layers = [
            MergeLayer(layer_id=5, name="top", depends_on=[3]),
            MergeLayer(layer_id=3, name="mid", depends_on=[1]),
            MergeLayer(layer_id=1, name="base"),
        ]
        result = topological_sort_layers(layers)
        result_ids = [ly.layer_id for ly in result]
        assert result_ids == [1, 3, 5]

    def test_non_sequential_ids(self):
        from src.models.plan import topological_sort_layers

        layers = [
            MergeLayer(layer_id=100, name="a"),
            MergeLayer(layer_id=50, name="b", depends_on=[100]),
            MergeLayer(layer_id=200, name="c", depends_on=[50]),
        ]
        result = topological_sort_layers(layers)
        result_ids = [ly.layer_id for ly in result]
        assert result_ids.index(100) < result_ids.index(50)
        assert result_ids.index(50) < result_ids.index(200)

    def test_cycle_detection_raises(self):
        from src.models.plan import topological_sort_layers, LayerCycleError

        layers = [
            MergeLayer(layer_id=1, name="a", depends_on=[2]),
            MergeLayer(layer_id=2, name="b", depends_on=[1]),
        ]
        with pytest.raises(LayerCycleError, match="Cycle detected"):
            topological_sort_layers(layers)

    def test_independent_layers_stable(self):
        from src.models.plan import topological_sort_layers

        layers = [
            MergeLayer(layer_id=3, name="c"),
            MergeLayer(layer_id=1, name="a"),
            MergeLayer(layer_id=2, name="b"),
        ]
        result = topological_sort_layers(layers)
        result_ids = [ly.layer_id for ly in result]
        assert result_ids == [1, 2, 3]

    def test_diamond_dependency(self):
        from src.models.plan import topological_sort_layers

        layers = [
            MergeLayer(layer_id=0, name="root"),
            MergeLayer(layer_id=1, name="left", depends_on=[0]),
            MergeLayer(layer_id=2, name="right", depends_on=[0]),
            MergeLayer(layer_id=3, name="join", depends_on=[1, 2]),
        ]
        result = topological_sort_layers(layers)
        result_ids = [ly.layer_id for ly in result]
        assert result_ids.index(0) < result_ids.index(1)
        assert result_ids.index(0) < result_ids.index(2)
        assert result_ids.index(1) < result_ids.index(3)
        assert result_ids.index(2) < result_ids.index(3)


class TestVerifyLayerDepsBlocking:
    """Tests for orchestrator layer dependency blocking."""

    def _make_state_with_layers(self, layers):
        from src.models.plan import MergePlan, RiskSummary

        plan = MergePlan(
            created_at=datetime.now(),
            upstream_ref="upstream",
            fork_ref="fork",
            merge_base_commit="base",
            phases=[],
            risk_summary=RiskSummary(
                total_files=0,
                auto_safe_count=0,
                auto_risky_count=0,
                human_required_count=0,
                deleted_only_count=0,
                binary_count=0,
                excluded_count=0,
                estimated_auto_merge_rate=0.0,
            ),
            project_context_summary="test",
            layers=layers,
        )
        state = MergeState(
            config=MergeConfig(
                upstream_ref="upstream/main",
                fork_ref="feature/fork",
            ),
        )
        state.merge_plan = plan
        return state

    def test_deps_met_returns_true(self):
        from src.core.orchestrator import Orchestrator
        from src.models.config import MergeConfig

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
        )

        layers = [
            MergeLayer(layer_id=0, name="base"),
            MergeLayer(layer_id=1, name="mid", depends_on=[0]),
        ]
        state = self._make_state_with_layers(layers)
        completed = {0}

        assert orch._verify_layer_deps(1, completed, state) is True

    def test_deps_not_met_returns_false(self):
        from src.core.orchestrator import Orchestrator
        from src.models.config import MergeConfig

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
        )

        layers = [
            MergeLayer(layer_id=0, name="base"),
            MergeLayer(layer_id=1, name="mid", depends_on=[0]),
        ]
        state = self._make_state_with_layers(layers)
        completed: set[int] = set()

        assert orch._verify_layer_deps(1, completed, state) is False

    def test_no_layers_returns_true(self):
        from src.core.orchestrator import Orchestrator
        from src.models.config import MergeConfig

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
        )

        state = self._make_state_with_layers([])
        assert orch._verify_layer_deps(0, set(), state) is True

    def test_unknown_layer_returns_true(self):
        from src.core.orchestrator import Orchestrator
        from src.models.config import MergeConfig

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = MergeConfig(
            upstream_ref="upstream/main",
            fork_ref="feature/fork",
        )

        layers = [MergeLayer(layer_id=0, name="base")]
        state = self._make_state_with_layers(layers)
        assert orch._verify_layer_deps(99, set(), state) is True
