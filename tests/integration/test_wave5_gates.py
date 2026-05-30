"""W5 Tier-A regression net: the new Wave-5 gate behaviours exercised
end-to-end through the orchestrator over ``FakeGitTool``.

Complements the unit suites (``test_w1_git_read_status``,
``test_w2_truncation_stop_reason``, ``test_p3_compile_gate``) by proving the
wiring holds in a full run:

- **W1** a genuine git read error on the B-class drift sanity records a
  ``gate_skip`` into ``state.errors`` (→ partial_failure), not a silent green.
- **W2** a provider-truncated per-file Judge review fails closed (a
  ``review_unavailable`` CRITICAL veto), not a silent PASS.
- **W4** a compiled-language (``.go``) auto-merge under a Python-only (ruff)
  gate records the ``no_compile_gate`` advisory.
"""

import json
from unittest.mock import AsyncMock

import pytest

from src.core.orchestrator import Orchestrator
from src.llm.client import LLMResponse
from src.models.config import GateCommandConfig, GateConfig
from src.models.state import MergeState
from src.tools.gate_skip import GATE_SKIP_PHASE
from src.tools.git_tool import GitReadStatus
from tests.integration.conftest import (
    FakeGitTool,
    JUDGE_VERDICT_PASS,
    PLANNER_JUDGE_APPROVED_1,
)

PLAN_ONE_GO_AUTO_SAFE = json.dumps(
    {
        "phases": [
            {
                "batch_id": "batch-go-1",
                "phase": "auto_merge",
                "file_paths": ["src/auth.go"],
                "risk_level": "auto_safe",
                "can_parallelize": True,
            }
        ],
        "risk_summary": {
            "total_files": 1,
            "auto_safe_count": 1,
            "auto_risky_count": 0,
            "human_required_count": 0,
            "deleted_only_count": 0,
            "binary_count": 0,
            "excluded_count": 0,
            "estimated_auto_merge_rate": 1.0,
            "top_risk_files": [],
        },
        "project_context_summary": "Integration test project",
        "special_instructions": [],
    }
)


@pytest.fixture
def fake_git_go_auto_safe(tmp_path) -> FakeGitTool:
    changed = [("M", "src/auth.go")]
    contents = {"src/auth.go": "package auth\n\nfunc Run() int {\n    return 0\n}\n"}
    return FakeGitTool(tmp_path, changed, contents, category="B")


# --------------------------------------------------------------------------- #
# W4 — per-language compile-gate advisory end-to-end
# --------------------------------------------------------------------------- #
async def test_w4_go_automerge_under_ruff_only_gate_records_advisory(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_go_auto_safe
):
    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_go_auto_safe)
    base = make_config()
    config = base.model_copy(
        update={
            "gate": GateConfig(
                enabled=True,
                commands=[
                    GateCommandConfig(
                        name="lint",
                        command="ruff check .",
                        baseline_parser="ruff_json",
                    )
                ],
            )
        }
    )
    orchestrator = Orchestrator(config)
    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ONE_GO_AUTO_SAFE]
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_APPROVED_1]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    result = await orchestrator.run(MergeState(config=config))

    msgs = [e.get("message", "") for e in result.errors]
    assert any("no_compile_gate" in m for m in msgs), msgs


# --------------------------------------------------------------------------- #
# W1 — a git read error on the B-class sanity alarms (gate_skip)
# --------------------------------------------------------------------------- #
class _BrokenHashGit(FakeGitTool):
    """FakeGitTool whose ref-hash read genuinely fails (GIT_ERROR), simulating a
    systemically broken git_tool at the B-class drift sanity gate."""

    def get_file_hash_checked(self, ref: str, file_path: str):
        return None, GitReadStatus.GIT_ERROR


@pytest.fixture
def broken_hash_git(tmp_path) -> _BrokenHashGit:
    changed = [("M", "src/utils.py")]
    contents = {"src/utils.py": "def util():\n    return 1\n"}
    return _BrokenHashGit(tmp_path, changed, contents, category="B")


async def test_w1_git_error_on_b_class_sanity_records_gate_skip(
    mocker, tmp_path, patch_llm_factory, make_config, broken_hash_git
):
    mocker.patch("src.core.orchestrator.GitTool", return_value=broken_hash_git)
    config = make_config()
    orchestrator = Orchestrator(config)
    orchestrator.planner._call_llm_with_retry = AsyncMock(
        side_effect=[PLAN_ONE_GO_AUTO_SAFE.replace("src/auth.go", "src/utils.py")]
    )
    orchestrator.planner_judge._call_llm_with_retry = AsyncMock(
        side_effect=[PLANNER_JUDGE_APPROVED_1]
    )
    orchestrator.judge._call_llm_with_retry = AsyncMock(
        side_effect=[JUDGE_VERDICT_PASS]
    )

    result = await orchestrator.run(MergeState(config=config))

    skips = [e for e in result.errors if e.get("phase") == GATE_SKIP_PHASE]
    assert any("b_class_drift_sanity" in e["message"] for e in skips), result.errors


# --------------------------------------------------------------------------- #
# W2 — a truncated per-file Judge review fails closed (veto)
# --------------------------------------------------------------------------- #
# The fixture's synthetic scenarios never drive the Judge's per-file LLM review
# (a clean semantic-merge / B-class auto-merge reaches only the final verdict
# call), so W2 is exercised against the orchestrator's REAL Judge agent by
# invoking review_file directly with a provider-truncated response.
async def test_w2_truncated_judge_review_fails_closed(
    mocker, tmp_path, patch_llm_factory, make_config, fake_git_auto_risky
):
    from datetime import datetime

    from src.models.decision import (
        DecisionSource,
        FileDecisionRecord,
        MergeDecision,
    )
    from src.models.diff import FileChangeCategory, FileDiff, FileStatus, RiskLevel
    from src.models.judge import IssueSeverity

    mocker.patch("src.core.orchestrator.GitTool", return_value=fake_git_auto_risky)
    config = make_config()
    judge = Orchestrator(config).judge
    # parseable JSON but stop_reason=max_tokens → W2 gate raises → fail closed.
    judge._call_llm_with_retry = AsyncMock(
        return_value=LLMResponse(text='{"issues": []}', stop_reason="max_tokens")
    )

    record = FileDecisionRecord(
        file_path="src/service.py",
        file_status=FileStatus.MODIFIED,
        decision=MergeDecision.SEMANTIC_MERGE,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        confidence=0.9,
        rationale="t",
        timestamp=datetime.now(),
    )
    fd = FileDiff(
        file_path="src/service.py",
        file_status=FileStatus.MODIFIED,
        risk_level=RiskLevel.AUTO_RISKY,
        risk_score=0.5,
        change_category=FileChangeCategory.C,
        lines_added=1,
        lines_deleted=1,
    )

    issues = await judge.review_file(
        "src/service.py", "def merged():\n    pass\n", record, fd
    )

    assert any(
        i.issue_type == "review_unavailable" and i.issue_level == IssueSeverity.CRITICAL
        for i in issues
    ), [i.issue_type for i in issues]
