"""Module-aware layered plan: grouping by functional module (outer),
file type/risk split (inner), topological module ordering, and the
mode=off fallback to untagged pure-layer batches."""

from __future__ import annotations

from unittest.mock import patch

from src.agents.planner_agent import PlannerAgent
from src.models.config import AgentLLMConfig, MergeConfig
from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
from src.models.state import MergeState


def _planner() -> PlannerAgent:
    with patch("src.llm.client.LLMClientFactory.create"):
        return PlannerAgent(AgentLLMConfig())


def _fd(path: str, cat: FileChangeCategory, risk: RiskLevel) -> FileDiff:
    return FileDiff(
        file_path=path,
        file_status=FileStatus.MODIFIED,
        risk_level=risk,
        risk_score=0.5,
        lines_added=10,
        lines_deleted=4,
        lines_changed=14,
        change_category=cat,
    )


def _state(**module_cfg: object) -> MergeState:
    config = MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    for k, v in module_cfg.items():
        setattr(config.module_config, k, v)
    state = MergeState(config=config)
    state.merge_base_commit = "base"
    state.file_categories = {
        "packages/auth/login.py": FileChangeCategory.C,
        "packages/auth/util.py": FileChangeCategory.B,
        "packages/orders/order.py": FileChangeCategory.C,
        "shared/types.py": FileChangeCategory.B,
    }
    return state


def _diffs() -> list[FileDiff]:
    return [
        _fd("packages/auth/login.py", FileChangeCategory.C, RiskLevel.AUTO_RISKY),
        _fd("packages/auth/util.py", FileChangeCategory.B, RiskLevel.AUTO_SAFE),
        _fd("packages/orders/order.py", FileChangeCategory.C, RiskLevel.AUTO_RISKY),
        _fd("shared/types.py", FileChangeCategory.B, RiskLevel.AUTO_SAFE),
    ]


def test_batches_are_tagged_by_module() -> None:
    plan = _planner()._build_layered_plan(_diffs(), _state())
    modules = {p.module for p in plan.phases}
    # packages/<mod> → auth, orders ; shared/ → top-level "shared"
    assert modules == {"auth", "orders", "shared"}


def test_module_summary_counts_files() -> None:
    plan = _planner()._build_layered_plan(_diffs(), _state())
    assert plan.module_summary == {"auth": 2, "orders": 1, "shared": 1}


def test_files_grouped_within_their_module() -> None:
    plan = _planner()._build_layered_plan(_diffs(), _state())
    by_module: dict[str | None, set[str]] = {}
    for p in plan.phases:
        by_module.setdefault(p.module, set()).update(p.file_paths)
    assert by_module["auth"] == {"packages/auth/login.py", "packages/auth/util.py"}
    assert by_module["orders"] == {"packages/orders/order.py"}
    assert by_module["shared"] == {"shared/types.py"}


def test_module_depends_on_orders_dependency_first() -> None:
    # auth depends on shared → shared's phases must precede auth's.
    state = _state(module_depends_on={"auth": ["shared"]})
    plan = _planner()._build_layered_plan(_diffs(), state)
    order: list[str | None] = []
    for p in plan.phases:
        if p.module not in order:
            order.append(p.module)
    assert order.index("shared") < order.index("auth")


def test_off_mode_leaves_batches_untagged() -> None:
    plan = _planner()._build_layered_plan(_diffs(), _state(mode="off"))
    assert all(p.module is None for p in plan.phases)
    assert plan.module_summary == {}


def test_module_internal_risk_split_preserved() -> None:
    plan = _planner()._build_layered_plan(_diffs(), _state())
    # Within auth: the B file is auto_safe, the C file is auto_risky —
    # they land in separate batches, both tagged auth.
    auth = [p for p in plan.phases if p.module == "auth"]
    risks = {p.risk_level for p in auth}
    assert RiskLevel.AUTO_SAFE in risks
    assert RiskLevel.AUTO_RISKY in risks
