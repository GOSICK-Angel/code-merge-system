"""Planner / Judge consumption of the file dependency graph.

Anti-dead-code guard (plan §0 / §9): an empty graph must leave behavior
byte-identical to the pre-graph code; a non-empty graph must change planner
ordering / fanout and let judge emit missed-update issues.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.agents.judge_agent import JudgeAgent
from src.agents.planner_agent import PlannerAgent
from src.models.config import AgentLLMConfig, MergeConfig
from src.models.dependency import (
    ConfidenceLabel,
    DependencyEdge,
    DependencyKind,
    FileDependencyGraph,
)
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.state import MergeState
from src.tools.interface_change_extractor import InterfaceChange


# --------------------------------------------------------------------------
# Planner: topological tie-break within an equal-risk batch
# --------------------------------------------------------------------------


def _import_edge(src: str, tgt: str) -> DependencyEdge:
    return DependencyEdge(
        source_file=src,
        target_file=tgt,
        kind=DependencyKind.IMPORTS,
        confidence=ConfidenceLabel.EXTRACTED,
    )


def test_split_by_risk_level_topo_rank_orders_dependency_first() -> None:
    # alpha imports zeta -> zeta is depended-upon -> topo rank 0.
    diffs = {
        "m/alpha.py": FileDiff(
            file_path="m/alpha.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.5,
        ),
        "m/zeta.py": FileDiff(
            file_path="m/zeta.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.5,
        ),
    }
    paths = ["m/alpha.py", "m/zeta.py"]

    # No graph -> alphabetical secondary key.
    safe_plain, _, _ = PlannerAgent._split_by_risk_level(paths, diffs, set())
    assert safe_plain == ["m/alpha.py", "m/zeta.py"]

    # Graph present -> zeta (rank 0) before alpha (rank 1) at equal risk_score.
    topo = {"m/zeta.py": 0, "m/alpha.py": 1}
    safe_topo, _, _ = PlannerAgent._split_by_risk_level(
        paths, diffs, set(), topo_rank=topo
    )
    assert safe_topo == ["m/zeta.py", "m/alpha.py"]


def _planner() -> PlannerAgent:
    with patch("src.llm.client.LLMClientFactory.create"):
        return PlannerAgent(AgentLLMConfig())


def _fd(path: str) -> FileDiff:
    return FileDiff(
        file_path=path,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=0.5,
        lines_added=5,
        lines_deleted=2,
        lines_changed=7,
        change_category=FileChangeCategory.B,
    )


def _layered_state() -> MergeState:
    config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    config.module_config.enabled = False  # single untagged pass -> one batch
    state = MergeState(config=config)
    state.merge_base_commit = "base"
    state.file_categories = {
        "m/alpha.py": FileChangeCategory.B,
        "m/zeta.py": FileChangeCategory.B,
    }
    return state


def test_layered_plan_ordering_changes_with_graph() -> None:
    diffs = [_fd("m/alpha.py"), _fd("m/zeta.py")]

    plan_empty = _planner()._build_layered_plan(diffs, _layered_state())
    safe_empty = [fp for ph in plan_empty.phases for fp in ph.file_paths]

    state = _layered_state()
    state.dependency_graph = FileDependencyGraph(
        edges=(_import_edge("m/alpha.py", "m/zeta.py"),), file_count=2
    )
    plan_graph = _planner()._build_layered_plan(diffs, state)
    safe_graph = [fp for ph in plan_graph.phases for fp in ph.file_paths]

    assert safe_empty == ["m/alpha.py", "m/zeta.py"]
    assert safe_graph == ["m/zeta.py", "m/alpha.py"]


# --------------------------------------------------------------------------
# Planner: fanout dimension from impact_radius
# --------------------------------------------------------------------------


def test_compute_fanout_map_uses_impact_radius() -> None:
    state = MergeState(
        config=MergeConfig(upstream_ref="u", fork_ref="f"),
    )
    state.config.module_config.enabled = False
    # hub.py is imported by two files -> larger impact radius -> higher fanout.
    state.dependency_graph = FileDependencyGraph(
        edges=(
            _import_edge("a.py", "hub.py"),
            _import_edge("b.py", "hub.py"),
        ),
        file_count=3,
    )
    diffs = [_fd("hub.py"), _fd("a.py"), _fd("b.py")]
    fanout = _planner()._compute_fanout_map(diffs, state)
    assert fanout is not None
    assert fanout["hub.py"] > fanout["a.py"]
    assert fanout["hub.py"] > 0.0


def test_compute_fanout_map_none_when_no_signals() -> None:
    state = MergeState(config=MergeConfig(upstream_ref="u", fork_ref="f"))
    state.config.module_config.enabled = False
    # empty graph + module grouping off -> no fanout signal at all.
    diffs = [_fd("a.py")]
    assert _planner()._compute_fanout_map(diffs, state) is None


# --------------------------------------------------------------------------
# Judge: EXTRACTED-edge missed-update detection
# --------------------------------------------------------------------------


def _judge() -> JudgeAgent:
    with patch("src.llm.client.LLMClientFactory.create"):
        return JudgeAgent(AgentLLMConfig(), git_tool=None)


def _judge_state(
    repo: Path, graph: FileDependencyGraph, *, reverse_impacts=None
) -> SimpleNamespace:
    return SimpleNamespace(
        dependency_graph=graph,
        interface_changes=[
            InterfaceChange(
                file_path="lib/api.py",
                symbol="do_thing",
                change_kind="method_signature",
                before="def do_thing(a)",
                after="def do_thing(a, b)",
            )
        ],
        reverse_impacts=reverse_impacts or {},
        file_decision_records={},
    )


def test_judge_flags_missed_update_on_extracted_edge(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "caller.py").write_text(
        "from lib.api import do_thing\n\ndo_thing(1)\n", encoding="utf-8"
    )
    judge = _judge()
    judge.git_tool = SimpleNamespace(repo_path=tmp_path)  # type: ignore[assignment]

    graph = FileDependencyGraph(
        edges=(_import_edge("app/caller.py", "lib/api.py"),), file_count=2
    )
    issues = judge._check_dependency_graph_impacts(
        _judge_state(tmp_path, graph)  # type: ignore[arg-type]
    )
    assert len(issues) == 1
    assert issues[0].file_path == "app/caller.py"
    assert issues[0].issue_type == "dependency_missed_update"
    assert issues[0].must_fix_before_merge is True


def test_judge_empty_graph_no_issue(tmp_path: Path) -> None:
    judge = _judge()
    judge.git_tool = SimpleNamespace(repo_path=tmp_path)  # type: ignore[assignment]
    issues = judge._check_dependency_graph_impacts(
        _judge_state(tmp_path, FileDependencyGraph())  # type: ignore[arg-type]
    )
    assert issues == []


def test_judge_inferred_edge_does_not_raise(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "caller.py").write_text("do_thing(1)\n", encoding="utf-8")
    judge = _judge()
    judge.git_tool = SimpleNamespace(repo_path=tmp_path)  # type: ignore[assignment]
    graph = FileDependencyGraph(
        edges=(
            DependencyEdge(
                source_file="app/caller.py",
                target_file="lib/api.py",
                kind=DependencyKind.IMPORTS,
                confidence=ConfidenceLabel.INFERRED,
            ),
        ),
        file_count=2,
    )
    issues = judge._check_dependency_graph_impacts(
        _judge_state(tmp_path, graph)  # type: ignore[arg-type]
    )
    assert issues == []


def test_judge_skips_dependent_already_grepped(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "caller.py").write_text(
        "from lib.api import do_thing\n\ndo_thing(1)\n", encoding="utf-8"
    )
    judge = _judge()
    judge.git_tool = SimpleNamespace(repo_path=tmp_path)  # type: ignore[assignment]
    graph = FileDependencyGraph(
        edges=(_import_edge("app/caller.py", "lib/api.py"),), file_count=2
    )
    issues = judge._check_dependency_graph_impacts(
        _judge_state(tmp_path, graph, reverse_impacts={"do_thing": ["app/caller.py"]})  # type: ignore[arg-type]
    )
    assert issues == []


def test_judge_no_gittool_no_issue(tmp_path: Path) -> None:
    judge = _judge()  # git_tool=None
    graph = FileDependencyGraph(
        edges=(_import_edge("app/caller.py", "lib/api.py"),), file_count=2
    )
    issues = judge._check_dependency_graph_impacts(
        _judge_state(tmp_path, graph)  # type: ignore[arg-type]
    )
    assert issues == []


# --------------------------------------------------------------------------
# Contract declaration guard (anti-dead-code DoD (b))
# --------------------------------------------------------------------------


def test_dependency_graph_declared_in_consumer_contracts() -> None:
    from src.agents.contract import load_contract

    assert "dependency_graph" in load_contract("planner").inputs
    assert "dependency_graph" in load_contract("judge").inputs
    # Phase B step 9: planner_judge reads it via restricted_view for the
    # batch_ordering (topo) precheck, so it must be contract-declared too.
    assert "dependency_graph" in load_contract("planner_judge").inputs
    # Phase C C4: memory_extractor reads it via restricted_view for the
    # deterministic God Node / surprising-connection insights.
    mem = load_contract("memory_extractor")
    assert "dependency_graph" in mem.inputs
    assert "file_categories" in mem.inputs
