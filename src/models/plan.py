from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4
from pydantic import BaseModel, Field
from src.models.diff import RiskLevel, FileChangeCategory
from src.models.config import GateCommandConfig

if TYPE_CHECKING:
    from src.tools.config_drift_detector import ConfigDrift


class MergePhase(str, Enum):
    ANALYSIS = "analysis"
    PLAN_REVIEW = "plan_review"
    PLAN_REVISING = "plan_revising"
    AUTO_MERGE = "auto_merge"
    CONFLICT_ANALYSIS = "conflict_analysis"
    HUMAN_REVIEW = "human_review"
    JUDGE_REVIEW = "judge_review"
    REPORT = "report"


class MergeLayer(BaseModel):
    layer_id: int
    name: str
    description: str = ""
    path_patterns: list[str] = Field(default_factory=list)
    depends_on: list[int] = Field(default_factory=list)
    gate_commands: list[GateCommandConfig] = Field(default_factory=list)


def _gate(name: str, command: str, **kwargs: Any) -> dict[str, Any]:
    return {"name": name, "command": command, **kwargs}


DEFAULT_LAYERS: list[dict[str, Any]] = [
    {
        "layer_id": 0,
        "name": "infrastructure",
        "description": "Docker, CI/CD, dev scripts, root config files",
        "path_patterns": [
            "docker/**",
            "dev/**",
            "ci/**",
            ".github/**",
            "Makefile",
            ".gitignore",
            ".dockerignore",
        ],
        "depends_on": [],
        "gate_commands": [],
    },
    {
        "layer_id": 1,
        "name": "dependencies",
        "description": "Package manifests and lock files",
        "path_patterns": [
            "**/pyproject.toml",
            "**/package.json",
            "**/uv.lock",
            "**/pnpm-lock.yaml",
            "**/poetry.lock",
            "**/requirements*.txt",
        ],
        "depends_on": [0],
        "gate_commands": [],
    },
    {
        "layer_id": 2,
        "name": "types_configs",
        "description": "Type definitions, enums, constants, configuration schemas",
        "path_patterns": [
            "**/types/**",
            "**/configs/**",
            "**/constants/**",
            "**/enums/**",
            "**/*.d.ts",
        ],
        "depends_on": [1],
        "gate_commands": [
            _gate("lint", "ruff check ."),
        ],
    },
    {
        "layer_id": 3,
        "name": "models_extensions",
        "description": "Data models, ORM, extensions, base libraries, migrations",
        "path_patterns": [
            "**/models/**",
            "**/extensions/**",
            "**/libs/**",
            "**/migrations/**",
        ],
        "depends_on": [2],
        "gate_commands": [
            _gate("lint", "ruff check ."),
            _gate("test", "pytest tests/ -x -q --tb=no", timeout_seconds=600),
        ],
    },
    {
        "layer_id": 4,
        "name": "core_engine",
        "description": "Core business logic and engine modules",
        "path_patterns": ["**/core/**"],
        "depends_on": [3],
        "gate_commands": [
            _gate("lint", "ruff check ."),
            _gate("test", "pytest tests/ -x -q --tb=no", timeout_seconds=600),
        ],
    },
    {
        "layer_id": 5,
        "name": "services_controllers",
        "description": "Service layer, task queues, API controllers",
        "path_patterns": [
            "**/services/**",
            "**/tasks/**",
            "**/controllers/**",
        ],
        "depends_on": [4],
        "gate_commands": [
            _gate("lint", "ruff check ."),
            _gate(
                "test",
                "pytest tests/ -x -q --tb=no",
                timeout_seconds=600,
                pass_criteria="not_worse_than_baseline",
            ),
        ],
    },
    {
        "layer_id": 6,
        "name": "frontend",
        "description": "Frontend components and routes",
        "path_patterns": [
            "web/app/**",
            "web/service/**",
            "web/components/**",
            "web/hooks/**",
            "web/context/**",
            "web/utils/**",
            "src/app/**",
            "src/components/**",
        ],
        "depends_on": [2],
        "gate_commands": [],
    },
    {
        "layer_id": 7,
        "name": "i18n",
        "description": "Internationalization files",
        "path_patterns": ["**/i18n/**", "**/locales/**"],
        "depends_on": [6],
        "gate_commands": [],
    },
    {
        "layer_id": 8,
        "name": "tests",
        "description": "Test files",
        "path_patterns": [
            "**/tests/**",
            "**/__tests__/**",
            "**/e2e/**",
            "**/*.test.*",
            "**/*.spec.*",
        ],
        "depends_on": [4, 5, 6],
        "gate_commands": [
            _gate(
                "test_full",
                "pytest tests/ -q --tb=no",
                timeout_seconds=900,
                pass_criteria="not_worse_than_baseline",
            ),
        ],
    },
    {
        "layer_id": 9,
        "name": "sdk_plugins",
        "description": "SDKs, plugins, and auxiliary packages",
        "path_patterns": ["sdks/**", "plugins/**"],
        "depends_on": [5],
        "gate_commands": [],
    },
]


class LayerCycleError(ValueError):
    """Raised when layer dependencies contain a cycle."""


def topological_sort_layers(layers: list[MergeLayer]) -> list[MergeLayer]:
    layer_map = {ly.layer_id: ly for ly in layers}
    in_degree: dict[int, int] = {ly.layer_id: 0 for ly in layers}
    dependents: dict[int, list[int]] = {ly.layer_id: [] for ly in layers}

    for ly in layers:
        for dep in ly.depends_on:
            if dep in layer_map:
                in_degree[ly.layer_id] += 1
                dependents[dep].append(ly.layer_id)

    queue = sorted(lid for lid, deg in in_degree.items() if deg == 0)
    result: list[MergeLayer] = []

    while queue:
        lid = queue.pop(0)
        result.append(layer_map[lid])
        for child in sorted(dependents[lid]):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if len(result) != len(layers):
        sorted_ids = {ly.layer_id for ly in result}
        cycle_ids = [ly.layer_id for ly in layers if ly.layer_id not in sorted_ids]
        raise LayerCycleError(
            f"Cycle detected in layer dependencies involving layers: {cycle_ids}"
        )

    return result


class PhaseFileBatch(BaseModel):
    batch_id: str
    phase: MergePhase
    file_paths: list[str]
    risk_level: RiskLevel
    layer_id: int | None = None
    change_category: FileChangeCategory | None = None
    estimated_duration_minutes: float | None = None
    can_parallelize: bool = True


class CategorySummary(BaseModel):
    total_files: int = 0
    a_unchanged: int = 0
    b_upstream_only: int = 0
    c_both_changed: int = 0
    d_missing: int = 0
    d_extra: int = 0
    e_current_only: int = 0


class RiskSummary(BaseModel):
    total_files: int
    auto_safe_count: int
    auto_risky_count: int
    human_required_count: int
    deleted_only_count: int
    binary_count: int
    excluded_count: int
    estimated_auto_merge_rate: float = Field(ge=0.0, le=1.0)
    top_risk_files: list[str] = Field(default_factory=list)


class MergePlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime
    upstream_ref: str
    fork_ref: str
    merge_base_commit: str
    phases: list[PhaseFileBatch]
    risk_summary: RiskSummary
    category_summary: CategorySummary | None = None
    layers: list[MergeLayer] = Field(default_factory=list)
    project_context_summary: str
    special_instructions: list[str] = Field(default_factory=list)
    version: str = "2.0"


class PhaseExecutionRecord(BaseModel):
    phase_id: str
    started_at: datetime
    completed_at: datetime | None = None
    files_processed: int = 0
    files_skipped: int = 0
    commit_hash: str | None = None
    notes: list[str] = Field(default_factory=list)


class PhaseJudgeRecord(BaseModel):
    phase_id: str
    round_number: int
    verdict: str
    reviewed_at: datetime = Field(default_factory=datetime.now)
    issues: list[dict[str, str]] = Field(default_factory=list)
    veto_triggered: bool = False
    repair_instructions: list[str] = Field(default_factory=list)


class PhaseGateRecord(BaseModel):
    phase_id: str
    gate_results: list[dict[str, Any]] = Field(default_factory=list)
    all_passed: bool
    timestamp: datetime = Field(default_factory=datetime.now)


class OpenIssue(BaseModel):
    issue_id: str = Field(default_factory=lambda: str(uuid4()))
    phase_id: str
    description: str
    severity: str
    assigned_to_phase: str | None = None
    resolved: bool = False


class MergePlanLive(MergePlan):
    execution_records: list[PhaseExecutionRecord] = Field(default_factory=list)
    judge_records: list[PhaseJudgeRecord] = Field(default_factory=list)
    gate_records: list[PhaseGateRecord] = Field(default_factory=list)
    open_issues: list[OpenIssue] = Field(default_factory=list)
    todo_merge_count: int = 0
    todo_merge_limit: int = 30
    config_drifts: list[ConfigDrift] = Field(
        default_factory=list,
        description="Configuration drift entries detected during planning",
    )
    pollution_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Summary of pollution audit results from Phase 0",
    )


def _rebuild_plan_models() -> None:
    from src.tools.config_drift_detector import ConfigDrift

    MergePlanLive.model_rebuild(_types_namespace={"ConfigDrift": ConfigDrift})


_rebuild_plan_models()
