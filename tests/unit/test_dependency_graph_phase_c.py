"""Phase C (plan §7 step 10/11) consumption of the file dependency graph.

Anti-dead-code guard (plan §0 / §9): an empty graph must leave behavior
byte-identical to the pre-graph code; a non-empty graph must change planner
risk (God Node), module grouping (communities), import resolution (aliases),
memory insights (hub/surprising), and the human decision card (blast radius).
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.agents.memory_extractor_agent import _graph_insights
from src.agents.planner_agent import PlannerAgent
from src.core.phases.conflict_analysis import _build_human_decision_request
from src.memory.models import MemoryEntryType
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
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.state import MergeState
from src.tools.dep_extractors.alias_resolver import build_alias_map
from src.tools.dependency_extractor import DependencyExtractor
from src.tools.module_inference import infer_communities

_HAS_TS = (
    importlib.util.find_spec("tree_sitter") is not None
    and importlib.util.find_spec("tree_sitter_typescript") is not None
)


def _edge(
    src: str, tgt: str, conf: ConfidenceLabel = ConfidenceLabel.EXTRACTED
) -> DependencyEdge:
    return DependencyEdge(
        source_file=src,
        target_file=tgt,
        kind=DependencyKind.IMPORTS,
        confidence=conf,
    )


def _planner() -> PlannerAgent:
    with patch("src.llm.client.LLMClientFactory.create"):
        return PlannerAgent(AgentLLMConfig())


def _fd(path: str, risk: float = 0.2) -> FileDiff:
    return FileDiff(
        file_path=path,
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_SAFE,
        risk_score=risk,
        lines_added=5,
        lines_deleted=2,
        change_category=FileChangeCategory.B,
    )


# ==========================================================================
# C1 — God Node risk bump (planner)
# ==========================================================================


def test_god_node_risk_bump_raises_and_reclassifies() -> None:
    state = MergeState(config=MergeConfig(upstream_ref="u", fork_ref="f"))
    state.config.dependency_graph.god_node_min_dependents = 3
    state.config.dependency_graph.god_node_risk_bump = 0.5
    # hub.py imported by 3 files -> God Node.
    state.dependency_graph = FileDependencyGraph(
        edges=(
            _edge("a.py", "hub.py"),
            _edge("b.py", "hub.py"),
            _edge("c.py", "hub.py"),
        ),
        file_count=4,
    )
    diffs = [_fd("hub.py", risk=0.2), _fd("a.py", risk=0.2)]
    out = _planner()._apply_god_node_risk(diffs, state)
    by_path = {fd.file_path: fd for fd in out}
    assert by_path["hub.py"].risk_score == pytest.approx(0.7)
    assert by_path["a.py"].risk_score == pytest.approx(0.2)  # not a hub, unchanged
    # bumped past auto_risky threshold -> risk_level escalated.
    assert by_path["hub.py"].risk_level != RiskLevel.AUTO_SAFE


def test_god_node_empty_graph_unchanged() -> None:
    state = MergeState(config=MergeConfig(upstream_ref="u", fork_ref="f"))
    diffs = [_fd("hub.py")]
    out = _planner()._apply_god_node_risk(diffs, state)
    assert out is diffs  # identity: byte-identical safe degrade


def test_god_node_bump_zero_unchanged() -> None:
    state = MergeState(config=MergeConfig(upstream_ref="u", fork_ref="f"))
    state.config.dependency_graph.god_node_min_dependents = 1
    state.config.dependency_graph.god_node_risk_bump = 0.0
    state.dependency_graph = FileDependencyGraph(
        edges=(_edge("a.py", "hub.py"),), file_count=2
    )
    diffs = [_fd("hub.py")]
    out = _planner()._apply_god_node_risk(diffs, state)
    assert out is diffs


# ==========================================================================
# C2 — graph-driven communities (label propagation)
# ==========================================================================


def test_infer_communities_groups_connected_files() -> None:
    # Two clusters: {x/a, x/b} densely linked, {y/c, y/d} densely linked.
    edges = [
        ("x/a.py", "x/b.py"),
        ("x/b.py", "x/a.py"),
        ("y/c.py", "y/d.py"),
        ("y/d.py", "y/c.py"),
    ]
    paths = ["x/a.py", "x/b.py", "y/c.py", "y/d.py"]
    fallback = {p: p.split("/")[0] for p in paths}
    result = infer_communities(edges, paths, fallback)
    # a,b share a community; c,d share another; the two differ.
    assert result["x/a.py"] == result["x/b.py"]
    assert result["y/c.py"] == result["y/d.py"]
    assert result["x/a.py"] != result["y/c.py"]


def test_infer_communities_no_edges_falls_back() -> None:
    paths = ["x/a.py", "y/b.py"]
    fallback = {"x/a.py": "x", "y/b.py": "y"}
    result = infer_communities([], paths, fallback)
    assert result == fallback  # no edges -> identical to path topology


def test_assign_modules_graph_mode_uses_communities() -> None:
    state = MergeState(config=MergeConfig(upstream_ref="u", fork_ref="f"))
    state.config.module_config.mode = "graph"
    # Cross-directory coupling: api/handler imports core/db; with graph mode
    # they cluster together instead of staying in separate path modules.
    state.dependency_graph = FileDependencyGraph(
        edges=(
            _edge("api/handler.py", "core/db.py"),
            _edge("api/handler.py", "core/db.py"),
        ),
        file_count=2,
    )
    paths = ["api/handler.py", "core/db.py"]
    module_map, _ = _planner()._assign_modules(paths, state)
    assert module_map["api/handler.py"] == module_map["core/db.py"]


def test_assign_modules_auto_mode_unaffected_by_graph() -> None:
    state = MergeState(config=MergeConfig(upstream_ref="u", fork_ref="f"))
    # default mode "auto" -> graph ignored, path topology used.
    state.dependency_graph = FileDependencyGraph(
        edges=(_edge("api/handler.py", "core/db.py"),), file_count=2
    )
    paths = ["api/handler.py", "core/db.py"]
    module_map, _ = _planner()._assign_modules(paths, state)
    assert module_map["api/handler.py"] == "api"
    assert module_map["core/db.py"] == "core"


# ==========================================================================
# C3 — import alias / monorepo resolution
# ==========================================================================


def test_build_alias_map_tsconfig_paths() -> None:
    configs = {
        "tsconfig.json": (
            '{"compilerOptions": {"baseUrl": "src", "paths": {"@app/*": ["app/*"]}}}'
        )
    }
    amap = build_alias_map(configs)
    assert not amap.is_empty
    path_set = {"src/app/foo.ts"}
    assert amap.resolve_js("@app/foo", path_set) == "src/app/foo.ts"


def test_build_alias_map_jsonc_comments_tolerated() -> None:
    configs = {
        "tsconfig.json": (
            '{\n  // base\n  "compilerOptions": {\n'
            '    "baseUrl": ".",\n    "paths": {"@/*": ["lib/*"]},\n  }\n}'
        )
    }
    amap = build_alias_map(configs)
    assert amap.resolve_js("@/util", {"lib/util.ts"}) == "lib/util.ts"


def test_build_alias_map_go_module() -> None:
    configs = {"go.mod": "module github.com/org/repo\n\ngo 1.21\n"}
    amap = build_alias_map(configs)
    assert amap.go_module == "github.com/org/repo"
    path_set = {"pkg/store/store.go"}
    assert (
        amap.resolve_go("github.com/org/repo/pkg/store", path_set)
        == "pkg/store/store.go"
    )


def test_build_alias_map_package_json_workspace() -> None:
    configs = {"packages/ui/package.json": '{"name": "@org/ui", "version": "1.0.0"}'}
    amap = build_alias_map(configs)
    assert amap.pkg_names == {"@org/ui": "packages/ui"}
    assert (
        amap.resolve_js("@org/ui/button", {"packages/ui/button.ts"})
        == "packages/ui/button.ts"
    )


def test_build_alias_map_empty_for_unknown_configs() -> None:
    assert build_alias_map({"README.md": "# hi"}).is_empty
    assert build_alias_map({"tsconfig.json": "not json {{{"}).is_empty


@pytest.mark.skipif(not _HAS_TS, reason="tree-sitter ([ast] extra) not installed")
def test_extract_from_sources_resolves_alias_edge() -> None:
    files = {
        "src/app/main.ts": "import { foo } from '@app/util';\n",
        "src/app/util.ts": "export const foo = 1;\n",
    }
    amap = build_alias_map(
        {"tsconfig.json": '{"compilerOptions": {"paths": {"@app/*": ["src/app/*"]}}}'}
    )
    # Without alias map the bare specifier resolves to nothing.
    plain = DependencyExtractor.extract_from_sources(files, languages=["typescript"])
    assert not any(e.target_file == "src/app/util.ts" for e in plain.edges)
    # With alias map the edge appears.
    aliased = DependencyExtractor.extract_from_sources(
        files, languages=["typescript"], alias_map=amap
    )
    assert any(e.target_file == "src/app/util.ts" for e in aliased.edges)


# ==========================================================================
# C4 — memory_extractor deterministic graph insights
# ==========================================================================


def _mem_view(graph: FileDependencyGraph, categories: dict) -> SimpleNamespace:
    cfg = MergeConfig(upstream_ref="u", fork_ref="f")
    cfg.dependency_graph.god_node_min_dependents = 3
    return SimpleNamespace(
        dependency_graph=graph,
        file_categories=categories,
        config=cfg,
    )


def test_graph_insights_god_node() -> None:
    graph = FileDependencyGraph(
        edges=(
            _edge("a.py", "hub.py"),
            _edge("b.py", "hub.py"),
            _edge("c.py", "hub.py"),
        ),
        file_count=4,
    )
    view = _mem_view(graph, {"hub.py": FileChangeCategory.C})
    entries = _graph_insights(view, "analysis", set(), 5)
    assert any(
        e.entry_type == MemoryEntryType.CODEBASE_INSIGHT and "hub" in e.content.lower()
        for e in entries
    )
    assert all("dependency_graph" in e.tags for e in entries)


def test_graph_insights_surprising_cross_dir() -> None:
    graph = FileDependencyGraph(edges=(_edge("api/h.py", "core/db.py"),), file_count=2)
    view = _mem_view(
        graph,
        {"api/h.py": FileChangeCategory.C, "core/db.py": FileChangeCategory.B},
    )
    entries = _graph_insights(view, "analysis", set(), 5)
    rel = [e for e in entries if e.entry_type == MemoryEntryType.RELATIONSHIP]
    assert rel and "surprising_connection" in rel[0].tags


def test_graph_insights_empty_graph() -> None:
    view = _mem_view(FileDependencyGraph(), {"a.py": FileChangeCategory.C})
    assert _graph_insights(view, "analysis", set(), 5) == []


def test_graph_insights_same_dir_not_surprising() -> None:
    # same top dir -> not a cross-directory surprising connection.
    graph = FileDependencyGraph(edges=(_edge("api/h.py", "api/db.py"),), file_count=2)
    view = _mem_view(
        graph,
        {"api/h.py": FileChangeCategory.C, "api/db.py": FileChangeCategory.B},
    )
    rel = [
        e
        for e in _graph_insights(view, "analysis", set(), 5)
        if e.entry_type == MemoryEntryType.RELATIONSHIP
    ]
    assert rel == []


# ==========================================================================
# C5 — human decision card blast radius
# ==========================================================================


def _analysis() -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path="hub.py",
        conflict_points=[],
        overall_confidence=0.5,
        recommended_strategy=MergeDecision.ESCALATE_HUMAN,
        conflict_type=ConflictType.UNKNOWN,
        rationale="r",
        confidence=0.5,
    )


def test_decision_card_populates_blast_radius() -> None:
    fd = _fd("hub.py")
    hint = DependencyImpactHint(direct_dependents=9, impact_radius=20, is_god_node=True)
    req = _build_human_decision_request(fd, _analysis(), impact_hint=hint)
    assert req.dependents_count == 9
    assert req.blast_radius == 20
    assert req.is_god_node is True
    assert "Dependency impact" in req.context_summary
    assert "dependency hub" in req.context_summary


def test_decision_card_no_hint_zeros() -> None:
    req = _build_human_decision_request(_fd("hub.py"), _analysis())
    assert req.dependents_count == 0
    assert req.blast_radius == 0
    assert req.is_god_node is False
    assert "Dependency impact" not in req.context_summary
