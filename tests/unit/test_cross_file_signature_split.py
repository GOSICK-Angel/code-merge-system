"""Regression tests for the judge cross-file compilation gap.

Two coupled defects let a genuine cross-file signature mismatch pass review:

1. ``InterfaceChangeExtractor`` only matched ``def`` / ``function`` — Go
   ``func`` signatures were invisible, so interface-change detection produced
   nothing on Go repos.

2. ``interface_changes`` / ``reverse_impacts`` were absent from the judge
   contract ``inputs``. Under the restricted view their ``getattr(..., default)``
   reads silently returned the default (``FieldNotInContract`` subclasses
   ``AttributeError``), so the cross-file checks were dead in production.

The new ``_check_cross_decision_signature_split`` flags a symbol whose upstream
signature changed when its definition file and a referencing file landed on
opposite ``take_target`` / ``take_current`` sides of the merge.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.contract import FieldNotInContract
from src.agents.judge_agent import JudgeAgent
from src.core.read_only_state_view import ReadOnlyStateView
from src.models.config import AgentLLMConfig, MergeConfig
from src.models.decision import DecisionSource, FileDecisionRecord, MergeDecision
from src.models.diff import FileStatus
from src.models.judge import (
    BatchVerdict,
    DisputePoint,
    ExecutorRebuttal,
    IssueSeverity,
    JudgeIssue,
)
from src.models.state import MergeState
from src.tools.interface_change_extractor import (
    InterfaceChange,
    InterfaceChangeExtractor,
)


# --------------------------------------------------------------------------- #
# Go func extraction
# --------------------------------------------------------------------------- #


def test_go_free_function_signature_change_detected() -> None:
    base = (
        "package auth\n"
        "func GenerateAuthToken(ctx context.Context, userID int64, "
        "expiry time.Duration) (string, error) {\n}\n"
    )
    upstream = (
        "package auth\n"
        "func GenerateAuthToken(ctx context.Context, userID int64, "
        "loginSource optional.Option[int64], expiry time.Duration) "
        "(string, error) {\n}\n"
    )
    changes = InterfaceChangeExtractor().extract("auth/token.go", base, upstream)
    by_symbol = {c.symbol: c for c in changes}
    assert "GenerateAuthToken" in by_symbol
    assert by_symbol["GenerateAuthToken"].change_kind == "method_signature"
    assert "loginSource" in by_symbol["GenerateAuthToken"].after


def test_go_method_with_receiver_signature_change_detected() -> None:
    base = "package repo\nfunc (r *Repo) Save(u *User) error { return nil }\n"
    upstream = (
        "package repo\nfunc (r *Repo) Save(u *User, opts ...Opt) error { return nil }\n"
    )
    changes = InterfaceChangeExtractor().extract("repo/save.go", base, upstream)
    by_symbol = {c.symbol: c for c in changes}
    assert "Save" in by_symbol
    assert "opts" in by_symbol["Save"].after


# --------------------------------------------------------------------------- #
# _check_cross_decision_signature_split
# --------------------------------------------------------------------------- #


def _record(file_path: str, decision: MergeDecision) -> FileDecisionRecord:
    return FileDecisionRecord(
        file_path=file_path,
        file_status=FileStatus.MODIFIED,
        decision=decision,
        decision_source=DecisionSource.AUTO_EXECUTOR,
        confidence=0.9,
        rationale="",
        phase="auto_merge",
        agent="executor",
    )


def _judge(tmp_path) -> JudgeAgent:
    with patch("src.llm.client.LLMClientFactory.create"):
        judge = JudgeAgent(AgentLLMConfig(), git_tool=MagicMock(repo_path=tmp_path))
    return judge


def _state_with(tmp_path, def_decision, caller_decision, caller_body: str):
    state = MergeState(
        config=MergeConfig(upstream_ref="upstream/main", fork_ref="feature/fork")
    )
    state.interface_changes = [
        InterfaceChange(
            file_path="auth/token.go",
            symbol="GenerateAuthToken",
            change_kind="method_signature",
            before="ctx, userID, expiry",
            after="ctx, userID, loginSource, expiry",
        )
    ]
    state.file_decision_records = {
        "auth/token.go": _record("auth/token.go", def_decision),
        "models/user/user.go": _record("models/user/user.go", caller_decision),
    }
    caller = tmp_path / "models" / "user" / "user.go"
    caller.parent.mkdir(parents=True, exist_ok=True)
    caller.write_text(caller_body, encoding="utf-8")
    return state


def test_opposite_decisions_with_reference_flagged(tmp_path) -> None:
    state = _state_with(
        tmp_path,
        def_decision=MergeDecision.TAKE_CURRENT,
        caller_decision=MergeDecision.TAKE_TARGET,
        caller_body="token, err := auth.GenerateAuthToken(ctx, u.ID, src, expiry)\n",
    )
    judge = _judge(tmp_path)
    issues = judge._check_cross_decision_signature_split(ReadOnlyStateView(state))
    assert len(issues) == 1
    assert issues[0].issue_type == "cross_file_signature_split"
    assert issues[0].issue_level == IssueSeverity.HIGH
    assert issues[0].file_path == "models/user/user.go"
    assert issues[0].must_fix_before_merge is True
    assert issues[0].veto_condition == "Cross-file signature split unresolved"


def test_same_direction_decisions_not_flagged(tmp_path) -> None:
    state = _state_with(
        tmp_path,
        def_decision=MergeDecision.TAKE_TARGET,
        caller_decision=MergeDecision.TAKE_TARGET,
        caller_body="token, err := auth.GenerateAuthToken(ctx, u.ID, src, expiry)\n",
    )
    judge = _judge(tmp_path)
    issues = judge._check_cross_decision_signature_split(ReadOnlyStateView(state))
    assert issues == []


def test_semantic_merge_definition_skipped(tmp_path) -> None:
    state = _state_with(
        tmp_path,
        def_decision=MergeDecision.SEMANTIC_MERGE,
        caller_decision=MergeDecision.TAKE_TARGET,
        caller_body="token, err := auth.GenerateAuthToken(ctx, u.ID, src, expiry)\n",
    )
    judge = _judge(tmp_path)
    issues = judge._check_cross_decision_signature_split(ReadOnlyStateView(state))
    assert issues == []


def test_symbol_not_referenced_not_flagged(tmp_path) -> None:
    state = _state_with(
        tmp_path,
        def_decision=MergeDecision.TAKE_CURRENT,
        caller_decision=MergeDecision.TAKE_TARGET,
        caller_body="func unrelated() { doSomethingElse() }\n",
    )
    judge = _judge(tmp_path)
    issues = judge._check_cross_decision_signature_split(ReadOnlyStateView(state))
    assert issues == []


def test_config_flag_disables_check(tmp_path) -> None:
    state = _state_with(
        tmp_path,
        def_decision=MergeDecision.TAKE_CURRENT,
        caller_decision=MergeDecision.TAKE_TARGET,
        caller_body="token, err := auth.GenerateAuthToken(ctx, u.ID, src, expiry)\n",
    )
    state.config = state.config.model_copy(
        update={"judge_cross_file_signature_check": False}
    )
    judge = _judge(tmp_path)
    issues = judge._check_cross_decision_signature_split(ReadOnlyStateView(state))
    assert issues == []


# --------------------------------------------------------------------------- #
# Contract regression — the fields must be readable through the restricted view
# --------------------------------------------------------------------------- #


def test_contract_grants_interface_change_fields(tmp_path) -> None:
    """Under the production restricted view the check must still fire — proving
    interface_changes / reverse_impacts are now in the judge contract."""
    state = _state_with(
        tmp_path,
        def_decision=MergeDecision.TAKE_CURRENT,
        caller_decision=MergeDecision.TAKE_TARGET,
        caller_body="token, err := auth.GenerateAuthToken(ctx, u.ID, src, expiry)\n",
    )
    judge = _judge(tmp_path)
    restricted = judge.restricted_view(state)
    # Direct reads must not raise FieldNotInContract.
    assert restricted.interface_changes
    assert restricted.reverse_impacts == {}
    issues = judge._check_cross_decision_signature_split(restricted)
    assert len(issues) == 1


def test_out_of_contract_field_still_blocked(tmp_path) -> None:
    state = _state_with(
        tmp_path,
        def_decision=MergeDecision.TAKE_CURRENT,
        caller_decision=MergeDecision.TAKE_TARGET,
        caller_body="x\n",
    )
    judge = _judge(tmp_path)
    restricted = judge.restricted_view(state)
    try:
        _ = restricted.sentinel_hits
        raise AssertionError("expected FieldNotInContract")
    except FieldNotInContract:
        pass


# --------------------------------------------------------------------------- #
# re_evaluate — deterministic must_fix / veto issues survive negotiation
# --------------------------------------------------------------------------- #


def _veto_issue() -> JudgeIssue:
    return JudgeIssue(
        file_path="models/user/user.go",
        issue_level=IssueSeverity.HIGH,
        issue_type="cross_file_signature_split",
        description="opposite merge sides",
        must_fix_before_merge=True,
        veto_condition="Cross-file signature split unresolved",
    )


def _soft_issue() -> JudgeIssue:
    return JudgeIssue(
        file_path="a.go",
        issue_level=IssueSeverity.LOW,
        issue_type="style_nit",
        description="advisory",
        must_fix_before_merge=False,
    )


async def test_re_evaluate_cannot_drop_deterministic_veto(tmp_path) -> None:
    """The executor↔judge negotiation must not erase a must_fix/veto issue even
    when the LLM rebuttal claims everything is resolved."""
    judge = _judge(tmp_path)
    veto, soft = _veto_issue(), _soft_issue()
    current = BatchVerdict(
        layer_id=None,
        approved=False,
        issues=[veto, soft],
        reviewed_files=["models/user/user.go", "a.go"],
        round_num=0,
    )
    rebuttal = ExecutorRebuttal(
        accepts_all=False,
        dispute_points=[
            DisputePoint(
                issue_id=veto.issue_id, counter_evidence="LGTM", accepts=False
            ),
            DisputePoint(
                issue_id=soft.issue_id, counter_evidence="LGTM", accepts=False
            ),
        ],
        overall_rationale="all fine",
    )
    # LLM tries to drop BOTH issues and approve.
    judge._call_llm_with_retry = AsyncMock(
        return_value='{"overall_approved": true, "remaining_issues": []}'
    )

    state = MergeState(config=MergeConfig(upstream_ref="u", fork_ref="f"))
    result = await judge.re_evaluate(rebuttal, current, ReadOnlyStateView(state))

    kept_types = {i.issue_type for i in result.issues}
    assert "cross_file_signature_split" in kept_types  # veto retained
    assert "style_nit" not in kept_types  # soft issue dropped as LLM said
    assert result.approved is False  # must_fix issue blocks approval
