"""Phase B (plan §7 step 7/8/9) consumption of the file dependency graph.

Anti-dead-code guard (plan §0 / §9): an empty graph must leave behavior
byte-identical to the pre-graph code; a non-empty graph must change the
conflict_analyst prompt (blast-radius / God Node), the executor prompt
(downstream dependents), and the planner_judge verdict (batch_ordering issues).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.conflict_analyst_agent import (
    ConflictAnalystAgent,
    _format_blast_radius_block,
)
from src.agents.executor_agent import ExecutorAgent
from src.llm.prompts.executor_prompts import (
    build_deletion_analysis_prompt,
    build_semantic_merge_prompt,
)
from src.llm.prompts.planner_judge_prompts import precheck_batch_topological_order
from src.models.config import AgentLLMConfig, MergeConfig
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import MergeDecision
from src.models.dependency import (
    ConfidenceLabel,
    DependencyEdge,
    DependencyImpactHint,
    DependencyKind,
    FileDependencyGraph,
)
from src.models.diff import FileDiff, FileStatus, RiskLevel
from src.models.plan import MergePhase, MergePlan, PhaseFileBatch, RiskSummary
from src.models.state import MergeState


def _import_edge(
    src: str, tgt: str, conf: ConfidenceLabel = ConfidenceLabel.EXTRACTED
) -> DependencyEdge:
    return DependencyEdge(
        source_file=src,
        target_file=tgt,
        kind=DependencyKind.IMPORTS,
        confidence=conf,
    )


# --------------------------------------------------------------------------
# Model: impact_hint + DependencyImpactHint
# --------------------------------------------------------------------------


def test_impact_hint_counts_and_god_node() -> None:
    # hub.py imported by a, b, c -> 3 direct dependents.
    graph = FileDependencyGraph(
        edges=(
            _import_edge("a.py", "hub.py"),
            _import_edge("b.py", "hub.py"),
            _import_edge("c.py", "hub.py"),
        ),
        file_count=4,
    )
    hint = graph.impact_hint("hub.py", god_node_min_dependents=3)
    assert hint.direct_dependents == 3
    assert hint.impact_radius == 3
    assert hint.is_god_node is True
    # Threshold above the count -> not a god node, but still has signal.
    hint2 = graph.impact_hint("hub.py", god_node_min_dependents=4)
    assert hint2.is_god_node is False
    assert hint2.has_signal is True


def test_impact_hint_empty_graph_no_signal() -> None:
    hint = FileDependencyGraph().impact_hint("anything.py")
    assert hint.direct_dependents == 0
    assert hint.impact_radius == 0
    assert hint.is_god_node is False
    assert hint.has_signal is False


def test_format_blast_radius_block_empty_when_no_signal() -> None:
    assert _format_blast_radius_block(None) == ""
    assert _format_blast_radius_block(DependencyImpactHint()) == ""


def test_format_blast_radius_block_god_node_text() -> None:
    block = _format_blast_radius_block(
        DependencyImpactHint(direct_dependents=9, impact_radius=15, is_god_node=True)
    )
    assert "GOD NODE" in block
    assert "Dependency Impact" in block
    block_plain = _format_blast_radius_block(
        DependencyImpactHint(direct_dependents=2, impact_radius=2, is_god_node=False)
    )
    assert "GOD NODE" not in block_plain
    assert "conservative" in block_plain


# --------------------------------------------------------------------------
# conflict_analyst: blast-radius injected into the analysis prompt
# --------------------------------------------------------------------------


def _analyst() -> ConflictAnalystAgent:
    return ConflictAnalystAgent(
        AgentLLMConfig(
            provider="anthropic",
            model="test-model",
            api_key_env="ANTHROPIC_API_KEY",
            max_retries=1,
        )
    )


def _small_fd() -> FileDiff:
    return FileDiff(
        file_path="hub.py",
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.5,
        lines_added=3,
        lines_deleted=1,
    )


async def _capture_analyst_prompt(
    agent: ConflictAnalystAgent, impact_hint: DependencyImpactHint | None
) -> str:
    captured: dict[str, str] = {}

    async def _fake_llm(messages, **kw):  # type: ignore[no-untyped-def]
        captured["prompt"] = messages[0]["content"]
        return "{}"

    with (
        patch.object(
            agent, "_call_llm_with_retry", new=AsyncMock(side_effect=_fake_llm)
        ),
        patch(
            "src.agents.conflict_analyst_agent.parse_conflict_analysis",
            return_value=ConflictAnalysis(
                file_path="hub.py",
                conflict_points=[],
                overall_confidence=0.9,
                recommended_strategy=MergeDecision.SEMANTIC_MERGE,
                conflict_type=ConflictType.UNKNOWN,
            ),
        ),
    ):
        await agent.analyze_file(
            file_diff=_small_fd(),
            base_content="BASE",
            current_content="CURRENT",
            target_content="TARGET",
            impact_hint=impact_hint,
        )
    return captured["prompt"]


async def test_analyst_prompt_includes_god_node_hint() -> None:
    prompt = await _capture_analyst_prompt(
        _analyst(),
        DependencyImpactHint(direct_dependents=9, impact_radius=20, is_god_node=True),
    )
    assert "GOD NODE" in prompt
    assert "Dependency Impact" in prompt


async def test_analyst_prompt_no_block_without_graph() -> None:
    # No hint -> safe degrade -> no blast-radius text.
    prompt = await _capture_analyst_prompt(_analyst(), None)
    assert "Dependency Impact" not in prompt
    assert "GOD NODE" not in prompt


# --------------------------------------------------------------------------
# executor: downstream-dependents section in the prompts
# --------------------------------------------------------------------------


def _ca() -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path="hub.py",
        conflict_points=[],
        overall_confidence=0.8,
        recommended_strategy=MergeDecision.SEMANTIC_MERGE,
        conflict_type=ConflictType.UNKNOWN,
        rationale="r",
    )


def test_semantic_merge_prompt_dependents_section() -> None:
    fd = _small_fd()
    with_deps = build_semantic_merge_prompt(
        fd,
        _ca(),
        "CUR",
        "TGT",
        "",
        dependents=["a.py", "b.py"],
        referenced_symbols=frozenset({"do_thing"}),
    )
    assert "Downstream Dependents" in with_deps
    assert "2 file(s) import this one" in with_deps
    assert "do_thing" in with_deps

    without = build_semantic_merge_prompt(fd, _ca(), "CUR", "TGT", "")
    assert "Downstream Dependents" not in without


def test_deletion_prompt_dependents_section() -> None:
    with_deps = build_deletion_analysis_prompt(
        "hub.py", 40, "", dependents=["a.py", "b.py", "c.py"]
    )
    assert "Downstream Dependents" in with_deps
    assert "3 file(s) import this one" in with_deps

    without = build_deletion_analysis_prompt("hub.py", 40, "")
    assert "Downstream Dependents" not in without


def _executor() -> ExecutorAgent:
    return ExecutorAgent(
        AgentLLMConfig(
            provider="openai",
            model="test-model",
            api_key_env="OPENAI_API_KEY",
            max_retries=1,
        )
    )


def _state_with_graph(graph: FileDependencyGraph) -> MergeState:
    state = MergeState(
        config=MergeConfig(upstream_ref="upstream/main", fork_ref="fork")
    )
    state.dependency_graph = graph
    return state


async def test_execute_semantic_merge_passes_dependents() -> None:
    agent = _executor()
    mock_git = MagicMock()
    mock_git.get_file_content.side_effect = lambda ref, path: (
        "CURRENT" if ref == "fork" else "TARGET"
    )
    agent.git_tool = mock_git

    graph = FileDependencyGraph(
        edges=(_import_edge("a.py", "hub.py"), _import_edge("b.py", "hub.py")),
        file_count=3,
    )
    captured: dict[str, object] = {}

    def _capture_prompt(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured["dependents"] = kwargs.get("dependents")
        return "PROMPT"

    fd = FileDiff(
        file_path="hub.py",
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.5,
    )
    # Fail the LLM call (caught -> escalate record) so we stop right after the
    # prompt is built, without running the full merge / snapshot flow.
    with (
        patch("src.agents.executor_agent.build_semantic_merge_prompt", _capture_prompt),
        patch.object(
            agent,
            "_call_llm_with_retry_meta",
            new=AsyncMock(side_effect=RuntimeError("stop")),
        ),
    ):
        await agent.execute_semantic_merge(fd, _ca(), _state_with_graph(graph))

    assert set(captured["dependents"]) == {"a.py", "b.py"}  # type: ignore[arg-type]


async def test_analyze_deletion_risk_context_mentions_dependents() -> None:
    agent = _executor()
    graph = FileDependencyGraph(edges=(_import_edge("a.py", "hub.py"),), file_count=2)
    state = _state_with_graph(graph)
    fd = FileDiff(
        file_path="hub.py",
        file_status=FileStatus.DELETED,
        risk_level=RiskLevel.DELETED_ONLY,
        risk_score=0.5,
        lines_deleted=40,
    )
    with patch.object(
        agent, "_call_llm_with_retry", new=AsyncMock(return_value="reason text")
    ):
        item = await agent.analyze_deletion("hub.py", fd, state)
    assert "dependent file(s)" in item.risk_context
    assert "Deletion risk" in item.risk_context


async def test_analyze_deletion_no_dependents_clean_context() -> None:
    agent = _executor()
    state = _state_with_graph(FileDependencyGraph())
    fd = FileDiff(
        file_path="orphan.py",
        file_status=FileStatus.DELETED,
        risk_level=RiskLevel.DELETED_ONLY,
        risk_score=0.5,
        lines_deleted=10,
    )
    with patch.object(
        agent, "_call_llm_with_retry", new=AsyncMock(return_value="reason text")
    ):
        item = await agent.analyze_deletion("orphan.py", fd, state)
    assert "Deletion risk" not in item.risk_context


# --------------------------------------------------------------------------
# planner_judge: batch_ordering (topological) precheck
# --------------------------------------------------------------------------


def _plan(batches: list[tuple[RiskLevel, list[str]]]) -> MergePlan:
    phases = [
        PhaseFileBatch(
            batch_id=f"b{i}",
            phase=MergePhase.AUTO_MERGE,
            file_paths=files,
            risk_level=risk,
        )
        for i, (risk, files) in enumerate(batches)
    ]
    total = sum(len(f) for _, f in batches)
    return MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream",
        fork_ref="fork",
        merge_base_commit="base",
        phases=phases,
        risk_summary=RiskSummary(
            total_files=total,
            auto_safe_count=total,
            auto_risky_count=0,
            human_required_count=0,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=1.0,
        ),
        project_context_summary="",
    )


def test_topo_precheck_empty_graph_no_issue() -> None:
    plan = _plan([(RiskLevel.AUTO_SAFE, ["sub.py", "base.py"])])
    assert precheck_batch_topological_order(plan, None) == []
    assert precheck_batch_topological_order(plan, FileDependencyGraph()) == []


def test_topo_precheck_flags_dependent_before_dependency() -> None:
    # sub.py imports base.py -> base must merge first. Plan merges sub first.
    plan = _plan([(RiskLevel.AUTO_SAFE, ["sub.py", "base.py"])])
    graph = FileDependencyGraph(
        edges=(_import_edge("sub.py", "base.py"),), file_count=2
    )
    issues = precheck_batch_topological_order(plan, graph)
    assert len(issues) == 1
    assert issues[0].file_path == "sub.py"
    assert issues[0].issue_type == "batch_ordering"
    assert issues[0].source == "precheck"
    # Reordering, not reclassification: level unchanged.
    assert issues[0].current_classification == issues[0].suggested_classification
    assert "base.py" in issues[0].reason


def test_topo_precheck_correct_order_no_issue() -> None:
    # base.py merged first, then sub.py -> no violation.
    plan = _plan([(RiskLevel.AUTO_SAFE, ["base.py", "sub.py"])])
    graph = FileDependencyGraph(
        edges=(_import_edge("sub.py", "base.py"),), file_count=2
    )
    assert precheck_batch_topological_order(plan, graph) == []


def test_topo_precheck_inferred_edge_ignored() -> None:
    plan = _plan([(RiskLevel.AUTO_SAFE, ["sub.py", "base.py"])])
    graph = FileDependencyGraph(
        edges=(_import_edge("sub.py", "base.py", ConfidenceLabel.INFERRED),),
        file_count=2,
    )
    assert precheck_batch_topological_order(plan, graph) == []


def test_topo_precheck_caps_issue_count() -> None:
    # 40 dependents each merged before their dependency -> capped at 25.
    files: list[str] = []
    edges: list[DependencyEdge] = []
    for i in range(40):
        s, t = f"sub{i}.py", f"base{i}.py"
        files.extend([s, t])  # dependent always before dependency
        edges.append(_import_edge(s, t))
    plan = _plan([(RiskLevel.AUTO_SAFE, files)])
    graph = FileDependencyGraph(edges=tuple(edges), file_count=len(files))
    issues = precheck_batch_topological_order(plan, graph)
    assert len(issues) == 25
