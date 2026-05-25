"""Tests for P0: ABCDE three-way classification and layered merge ordering."""

from datetime import datetime
from unittest.mock import MagicMock, patch

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
    files_with_hashes: dict[str, dict[str, str]] = {}
    for ref, paths in file_hashes.items():
        all_files[ref] = [p for p, h in paths.items() if h is not None]
        files_with_hashes[ref] = {p: h for p, h in paths.items() if h is not None}
    git.list_files = MagicMock(side_effect=lambda ref: all_files.get(ref, []))
    git.list_files_with_hashes = MagicMock(
        side_effect=lambda ref: files_with_hashes.get(ref, {})
    )

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
        assert len(layers) >= 2
        assert layers[0].name == "infrastructure"
        assert layers[0].layer_id == 0
        layer_ids = {ly.layer_id for ly in layers}
        assert 0 in layer_ids
        assert any("**" in ly.path_patterns for ly in layers), (
            "DEFAULT_LAYERS must include a catch-all layer to prevent file loss"
        )

    def test_layer_dependencies(self):
        layers = [MergeLayer(**data) for data in DEFAULT_LAYERS]
        deps_map = {layer.layer_id: layer.depends_on for layer in layers}
        assert deps_map[0] == []
        for lid, deps in deps_map.items():
            for dep in deps:
                assert dep in deps_map, f"layer {lid} depends on undeclared {dep}"

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
        state.file_diffs = [fd_core, fd_docker, fd_service]

        plan = planner._build_layered_plan([fd_core, fd_docker, fd_service], state)

        assert plan.version == "2.0"
        assert plan.category_summary is not None
        assert plan.category_summary.b_upstream_only == 3
        assert plan.category_summary.c_both_changed == 3
        assert plan.category_summary.d_missing == 1
        assert len(plan.layers) >= 2

        all_files_in_plan = []
        for phase in plan.phases:
            all_files_in_plan.extend(phase.file_paths)

        assert "docker/Dockerfile" in all_files_in_plan
        assert "api/core/new_module.py" in all_files_in_plan
        assert "api/services/auth.py" in all_files_in_plan
        assert "unchanged.py" not in all_files_in_plan
        assert "custom_only.py" not in all_files_in_plan

        # Module-aware ordering: layers are ordered WITHIN each module's
        # contiguous run of phases, not globally (module is the outer sort).
        from itertools import groupby

        layer_by_id = {ly.layer_id: ly for ly in plan.layers}
        for module, group in groupby(plan.phases, key=lambda p: p.module):
            layer_order: list[int] = []
            for p in group:
                if p.layer_id is not None and p.layer_id not in layer_order:
                    layer_order.append(p.layer_id)
            for lid in layer_order:
                ly = layer_by_id.get(lid)
                if ly is None:
                    continue
                idx = layer_order.index(lid)
                for dep in ly.depends_on:
                    if dep in layer_order:
                        assert layer_order.index(dep) < idx, (
                            f"Layer {lid} before dep {dep} in module "
                            f"{module}: {layer_order}"
                        )

        # Module grouping is on by default — every actionable batch is
        # tagged and the summary is populated.
        assert all(p.module for p in plan.phases)
        assert plan.module_summary

        b_phases = [p for p in plan.phases if p.change_category == FileChangeCategory.B]
        for bp in b_phases:
            assert bp.risk_level == RiskLevel.AUTO_SAFE
            assert bp.phase == MergePhase.AUTO_MERGE

        human_phases = [
            p for p in plan.phases if p.risk_level == RiskLevel.HUMAN_REQUIRED
        ]
        assert len(human_phases) >= 1
        assert "api/services/auth.py" in human_phases[0].file_paths

    def test_planner_preserves_classifier_human_required_for_d_missing(self):
        from src.agents.planner_agent import PlannerAgent
        from src.models.config import AgentLLMConfig
        from src.models.decision import (
            FileDecisionRecord,
            DecisionSource,
            MergeDecision,
        )
        from src.models.diff import FileStatus

        with patch("src.llm.client.LLMClientFactory.create"):
            planner = PlannerAgent(AgentLLMConfig())

        config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
        config.layer_config.custom_layers = [
            {
                "layer_id": 0,
                "name": "deps_only",
                "path_patterns": ["**/pyproject.toml", "**/requirements*.txt"],
                "depends_on": [],
            }
        ]
        state = MergeState(config=config)
        state.merge_base_commit = "abc123"
        state.file_categories = {
            "secrets/.env.example": FileChangeCategory.D_MISSING,
            "fork_only/keep.py": FileChangeCategory.C,
            "obscure_dir/orphan.py": FileChangeCategory.B,
        }
        state.file_decision_records = {
            "fork_only/keep.py": FileDecisionRecord(
                file_path="fork_only/keep.py",
                file_status=FileStatus.MODIFIED,
                decision=MergeDecision.TAKE_CURRENT,
                decision_source=DecisionSource.AUTO_PLANNER,
                confidence=1.0,
                rationale="forks-profile retention",
                phase="initialize",
                agent="force_decision_policy",
            )
        }

        fd_secret = FileDiff(
            file_path="secrets/.env.example",
            file_status=FileStatus.ADDED,
            risk_level=RiskLevel.HUMAN_REQUIRED,
            risk_score=0.85,
            is_security_sensitive=True,
            change_category=FileChangeCategory.D_MISSING,
        )
        fd_orphan = FileDiff(
            file_path="obscure_dir/orphan.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_RISKY,
            risk_score=0.4,
            change_category=FileChangeCategory.B,
        )
        state.file_diffs = [fd_secret, fd_orphan]

        plan = planner._build_layered_plan(state.file_diffs, state)

        all_files = {fp for ph in plan.phases for fp in ph.file_paths}
        assert "fork_only/keep.py" not in all_files, (
            "F3: force-decided files must not re-enter the plan"
        )
        assert "obscure_dir/orphan.py" in all_files, (
            "F1: fallback-layer files must still be batched"
        )
        assert "secrets/.env.example" in all_files

        secret_phases = [
            ph for ph in plan.phases if "secrets/.env.example" in ph.file_paths
        ]
        assert secret_phases
        assert secret_phases[0].risk_level == RiskLevel.HUMAN_REQUIRED, (
            "F2: classifier HUMAN_REQUIRED on D_MISSING must propagate to batch"
        )

        orphan_phases = [
            ph for ph in plan.phases if "obscure_dir/orphan.py" in ph.file_paths
        ]
        assert orphan_phases
        assert orphan_phases[0].risk_level == RiskLevel.AUTO_RISKY
        assert orphan_phases[0].layer_id is None, (
            "Fallback-layer files batched with layer_id=None"
        )


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
        # DELETED_ONLY is no longer handled in _select_strategy_by_category;
        # it is routed to analyze_deletion() in AutoMergePhase pre-pass.
        # Passing DELETED_ONLY risk_level with no category falls through to TAKE_TARGET.
        from src.agents.executor_agent import ExecutorAgent
        from src.models.config import AgentLLMConfig
        from src.models.decision import MergeDecision

        with patch("src.llm.client.LLMClientFactory.create"):
            executor = ExecutorAgent(AgentLLMConfig())
        strategy = executor._select_strategy_by_category(None, RiskLevel.DELETED_ONLY)
        assert strategy == MergeDecision.TAKE_TARGET


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
        from src.core.phases._gate_helpers import verify_layer_deps

        layers = [
            MergeLayer(layer_id=0, name="base"),
            MergeLayer(layer_id=1, name="mid", depends_on=[0]),
        ]
        state = self._make_state_with_layers(layers)
        completed = {0}

        assert verify_layer_deps(1, completed, state) is True

    def test_deps_not_met_returns_false(self):
        from src.core.phases._gate_helpers import verify_layer_deps

        layers = [
            MergeLayer(layer_id=0, name="base"),
            MergeLayer(layer_id=1, name="mid", depends_on=[0]),
        ]
        state = self._make_state_with_layers(layers)
        completed: set[int] = set()

        assert verify_layer_deps(1, completed, state) is False

    def test_no_layers_returns_true(self):
        from src.core.phases._gate_helpers import verify_layer_deps

        state = self._make_state_with_layers([])
        assert verify_layer_deps(0, set(), state) is True

    def test_unknown_layer_returns_true(self):
        from src.core.phases._gate_helpers import verify_layer_deps

        layers = [MergeLayer(layer_id=0, name="base")]
        state = self._make_state_with_layers(layers)
        assert verify_layer_deps(99, set(), state) is True


class TestVacuouslyCompleteLayers:
    """Regression for layered_execution dep-gate false-cascade.

    Real-world repro (t1-0003): planner declared layers 0/1/2 with
    chain depends_on, but only layer 2 received any AUTO_SAFE /
    AUTO_RISKY batches. Without pre-fill, layer 2's dep check on
    layer 1 (empty) returned False and every file in layer 2 got an
    ``escalate_human`` record with rationale
    ``"layer 2 skipped: dependencies [1] not in completed_layers"``.
    """

    def test_empty_layer_marked_complete(self):
        from src.core.phases._gate_helpers import vacuously_complete_layers
        from src.models.plan import MergeLayer

        layer_index = {
            0: MergeLayer(layer_id=0, name="infra"),
            1: MergeLayer(layer_id=1, name="deps", depends_on=[0]),
            2: MergeLayer(layer_id=2, name="rest", depends_on=[1]),
        }
        # Only layer 2 has any AUTO batch.
        layers_with_batches: set[int | None] = {2}
        result = vacuously_complete_layers(layer_index, layers_with_batches)
        assert result == {0, 1}

    def test_layer_with_batches_not_marked_complete(self):
        from src.core.phases._gate_helpers import vacuously_complete_layers
        from src.models.plan import MergeLayer

        layer_index = {
            0: MergeLayer(layer_id=0, name="a"),
            1: MergeLayer(layer_id=1, name="b", depends_on=[0]),
        }
        # Both layers have batches — nothing is vacuously complete.
        layers_with_batches: set[int | None] = {0, 1}
        assert vacuously_complete_layers(layer_index, layers_with_batches) == set()

    def test_none_key_in_batches_ignored(self):
        """``layer_batches`` may contain ``None`` (batches without a layer);
        that key must not collide with declared layer IDs."""
        from src.core.phases._gate_helpers import vacuously_complete_layers
        from src.models.plan import MergeLayer

        layer_index = {0: MergeLayer(layer_id=0, name="only")}
        layers_with_batches: set[int | None] = {None}
        # layer 0 has no batches, so it is vacuously complete.
        assert vacuously_complete_layers(layer_index, layers_with_batches) == {0}

    def test_empty_plan_returns_empty_set(self):
        from src.core.phases._gate_helpers import vacuously_complete_layers

        assert vacuously_complete_layers({}, set()) == set()

    def test_resolves_t1_0003_cascade_shape(self):
        """End-to-end shape: with prefill, layer 2's dep on layer 1 (empty)
        is satisfied via the prefill, so verify_layer_deps returns True."""
        from src.core.phases._gate_helpers import (
            vacuously_complete_layers,
            verify_layer_deps,
        )
        from src.models.plan import MergeLayer

        layers = [
            MergeLayer(layer_id=0, name="infrastructure"),
            MergeLayer(layer_id=1, name="dependencies", depends_on=[0]),
            MergeLayer(layer_id=2, name="everything_else", depends_on=[1]),
        ]
        state = TestVerifyLayerDepsBlocking()._make_state_with_layers(layers)
        layer_index = {ly.layer_id: ly for ly in layers}

        # Layers 0 and 1 have no batches; layer 2 has the only batch.
        completed = vacuously_complete_layers(layer_index, {2})
        assert verify_layer_deps(2, completed, state) is True
