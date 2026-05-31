import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.agents.planner_agent import PlannerAgent
from src.models.config import MergeConfig, AgentLLMConfig
from src.models.diff import RiskLevel, FileDiff, FileStatus, FileChangeCategory
from src.models.plan import (
    MergePlan,
    MergePhase,
    PhaseFileBatch,
    RiskSummary,
)
from src.models.plan_judge import PlanIssue, PlanJudgeResult, PlanJudgeVerdict
from src.models.plan_review import (
    IssueResponseAction,
    PlannerIssueResponse,
    PlanDiffEntry,
    ReviewConclusionReason,
)
from src.models.state import MergeState


def _make_llm_config() -> AgentLLMConfig:
    return AgentLLMConfig(
        provider="anthropic", model="test-model", api_key_env="TEST_KEY"
    )


def _make_config() -> MergeConfig:
    return MergeConfig(upstream_ref="upstream/main", fork_ref="origin/main")


def _make_plan(
    batches: list[tuple[str, list[str], str]],
) -> MergePlan:
    phases = []
    for phase_str, paths, risk_str in batches:
        phases.append(
            PhaseFileBatch(
                batch_id=str(uuid4()),
                phase=MergePhase(phase_str),
                file_paths=paths,
                risk_level=RiskLevel(risk_str),
                can_parallelize=True,
            )
        )

    safe = sum(len(b.file_paths) for b in phases if b.risk_level == RiskLevel.AUTO_SAFE)
    risky = sum(
        len(b.file_paths) for b in phases if b.risk_level == RiskLevel.AUTO_RISKY
    )
    human = sum(
        len(b.file_paths) for b in phases if b.risk_level == RiskLevel.HUMAN_REQUIRED
    )
    total = sum(len(b.file_paths) for b in phases)
    rate = safe / total if total else 0.0

    return MergePlan(
        created_at=datetime.now(),
        upstream_ref="upstream/main",
        fork_ref="origin/main",
        merge_base_commit="abc123",
        phases=phases,
        risk_summary=RiskSummary(
            total_files=total,
            auto_safe_count=safe,
            auto_risky_count=risky,
            human_required_count=human,
            deleted_only_count=0,
            binary_count=0,
            excluded_count=0,
            estimated_auto_merge_rate=rate,
            top_risk_files=[],
        ),
        project_context_summary="test project",
    )


def _make_issue(fp: str, current: RiskLevel, suggested: RiskLevel) -> PlanIssue:
    return PlanIssue(
        file_path=fp,
        current_classification=current,
        suggested_classification=suggested,
        reason="test reason",
        issue_type="risk_underestimation",
    )


class TestApplyJudgeIssuesToPlan:
    def _make_agent(self) -> PlannerAgent:
        with patch.dict("os.environ", {"TEST_KEY": "sk-test-dummy"}):
            return PlannerAgent(llm_config=_make_llm_config())

    def test_escalate_auto_risky_to_human_required(self):
        agent = self._make_agent()
        plan = _make_plan(
            [
                ("auto_merge", ["a.py", "b.py", "c.py"], "auto_safe"),
                ("conflict_analysis", ["d.py", "e.py"], "auto_risky"),
            ]
        )
        issues = [
            _make_issue("d.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED),
        ]

        result = agent._apply_judge_issues_to_plan(plan, issues)

        all_files = {fp for batch in result["phases"] for fp in batch["file_paths"]}
        assert "d.py" in all_files, "d.py should still be in the plan"

        human_files = []
        for batch in result["phases"]:
            if batch["risk_level"] == "human_required":
                human_files.extend(batch["file_paths"])
        assert "d.py" in human_files

        risky_files = []
        for batch in result["phases"]:
            if batch["risk_level"] == "auto_risky":
                risky_files.extend(batch["file_paths"])
        assert "d.py" not in risky_files
        assert "e.py" in risky_files

    def test_reclassify_auto_safe_to_auto_risky(self):
        agent = self._make_agent()
        plan = _make_plan(
            [
                ("auto_merge", ["a.py", "b.py"], "auto_safe"),
            ]
        )
        issues = [
            _make_issue("b.py", RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY),
        ]

        result = agent._apply_judge_issues_to_plan(plan, issues)

        all_files = {fp for batch in result["phases"] for fp in batch["file_paths"]}
        assert "b.py" in all_files, "b.py must not be lost"

        risky_files = []
        for batch in result["phases"]:
            if batch["risk_level"] == "auto_risky":
                risky_files.extend(batch["file_paths"])
        assert "b.py" in risky_files

    def test_multiple_reclassifications_different_targets(self):
        agent = self._make_agent()
        plan = _make_plan(
            [
                ("auto_merge", ["a.py", "b.py", "c.py"], "auto_safe"),
                ("conflict_analysis", ["d.py", "e.py", "f.py"], "auto_risky"),
            ]
        )
        issues = [
            _make_issue("b.py", RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY),
            _make_issue("d.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED),
            _make_issue("f.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED),
        ]

        result = agent._apply_judge_issues_to_plan(plan, issues)

        all_files = {fp for batch in result["phases"] for fp in batch["file_paths"]}
        assert all_files == {"a.py", "b.py", "c.py", "d.py", "e.py", "f.py"}

        by_risk: dict[str, list[str]] = {}
        for batch in result["phases"]:
            by_risk.setdefault(batch["risk_level"], []).extend(batch["file_paths"])

        assert set(by_risk.get("auto_safe", [])) == {"a.py", "c.py"}
        assert "b.py" in by_risk.get("auto_risky", [])
        assert "e.py" in by_risk.get("auto_risky", [])
        assert set(by_risk.get("human_required", [])) == {"d.py", "f.py"}

    def test_risk_summary_reflects_reclassification(self):
        agent = self._make_agent()
        plan = _make_plan(
            [
                ("auto_merge", ["a.py", "b.py"], "auto_safe"),
                ("conflict_analysis", ["c.py"], "auto_risky"),
            ]
        )
        issues = [
            _make_issue("c.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED),
        ]

        result = agent._apply_judge_issues_to_plan(plan, issues)
        rs = result["risk_summary"]

        assert rs["total_files"] == 3
        assert rs["auto_safe_count"] == 2
        assert rs["auto_risky_count"] == 0
        assert rs["human_required_count"] == 1

    def test_no_files_lost(self):
        agent = self._make_agent()
        plan = _make_plan(
            [
                ("auto_merge", ["a.py", "b.py", "c.py", "d.py"], "auto_safe"),
                ("conflict_analysis", ["e.py", "f.py"], "auto_risky"),
            ]
        )
        issues = [
            _make_issue("a.py", RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY),
            _make_issue("e.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED),
        ]

        result = agent._apply_judge_issues_to_plan(plan, issues)

        original_files = {"a.py", "b.py", "c.py", "d.py", "e.py", "f.py"}
        result_files = {fp for batch in result["phases"] for fp in batch["file_paths"]}
        assert result_files == original_files

    def test_merge_into_existing_batch(self):
        agent = self._make_agent()
        plan = _make_plan(
            [
                ("auto_merge", ["a.py", "b.py"], "auto_safe"),
                ("conflict_analysis", ["c.py"], "auto_risky"),
                ("human_review", ["d.py"], "human_required"),
            ]
        )
        issues = [
            _make_issue("c.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED),
        ]

        result = agent._apply_judge_issues_to_plan(plan, issues)

        human_files = []
        for batch in result["phases"]:
            if batch["risk_level"] == "human_required":
                human_files.extend(batch["file_paths"])
        assert "c.py" in human_files
        assert "d.py" in human_files


class TestPlannerEvaluateIssues:
    def _make_agent(self) -> PlannerAgent:
        with patch.dict("os.environ", {"TEST_KEY": "sk-test-dummy"}):
            return PlannerAgent(llm_config=_make_llm_config())

    @pytest.mark.asyncio
    async def test_large_plan_still_evaluates_via_llm(self):
        agent = self._make_agent()
        large_files = [f"file_{i}.py" for i in range(250)]
        plan = _make_plan([("auto_merge", large_files, "auto_safe")])
        issues = [
            _make_issue("file_0.py", RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY),
        ]

        agent._call_llm_with_retry = AsyncMock(
            return_value='{"responses": [{"issue_id": "'
            + issues[0].issue_id
            + '", "file_path": "file_0.py", "action": "reject", '
            + '"reason": "config only, low risk", "counter_proposal": null}]}'
        )

        responses = await agent._evaluate_judge_issues(plan, issues)

        assert len(responses) == 1
        assert responses[0].action == IssueResponseAction.REJECT
        assert "config only" in responses[0].reason

    @pytest.mark.asyncio
    async def test_revise_plan_returns_tuple(self):
        agent = self._make_agent()
        plan = _make_plan(
            [
                ("auto_merge", ["a.py", "b.py"], "auto_safe"),
                ("conflict_analysis", ["c.py"], "auto_risky"),
            ]
        )
        state = MergeState(config=_make_config())
        state.merge_plan = plan

        issues = [
            _make_issue("c.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED),
        ]

        agent._call_llm_with_retry = AsyncMock(
            return_value='{"responses": [{"issue_id": "'
            + issues[0].issue_id
            + '", "file_path": "c.py", "action": "accept", "reason": "agreed", "counter_proposal": null}]}'
        )

        result = await agent.revise_plan(state, issues)

        assert isinstance(result, tuple)
        assert len(result) == 3
        revised_plan, responses, diff_entries = result
        assert isinstance(revised_plan, MergePlan)
        assert len(responses) == 1
        assert responses[0].action == IssueResponseAction.ACCEPT

    @pytest.mark.asyncio
    async def test_revise_plan_syncs_file_diffs_risk_level_on_accept(self):
        """P0-1 regression: when Planner accepts an LLM escalation, the
        corresponding ``state.file_diffs[*].risk_level`` must move with
        it. Otherwise the next round's deterministic precheck reads a
        stale classifier verdict and oscillates against the LLM."""
        agent = self._make_agent()
        plan = _make_plan(
            [
                ("auto_merge", ["a.py", "slack/manifest.yaml"], "auto_safe"),
            ]
        )
        original_fd = FileDiff(
            file_path="slack/manifest.yaml",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.19,
            lines_added=5,
            lines_deleted=2,
            lines_changed=7,
            conflict_count=0,
            hunks=[],
            is_security_sensitive=False,
            change_category=FileChangeCategory.C,
        )
        other_fd = FileDiff(
            file_path="a.py",
            file_status=FileStatus.MODIFIED,
            risk_level=RiskLevel.AUTO_SAFE,
            risk_score=0.05,
            lines_added=1,
            lines_deleted=0,
            lines_changed=1,
            conflict_count=0,
            hunks=[],
            is_security_sensitive=False,
            change_category=FileChangeCategory.B,
        )
        state = MergeState(config=_make_config())
        state.merge_plan = plan
        state.file_diffs = [other_fd, original_fd]

        issues = [
            _make_issue(
                "slack/manifest.yaml", RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY
            ),
        ]

        agent._call_llm_with_retry = AsyncMock(
            return_value='{"responses": [{"issue_id": "'
            + issues[0].issue_id
            + '", "file_path": "slack/manifest.yaml", "action": "accept", '
            + '"reason": "OAuth scopes — security sensitive", "counter_proposal": null}]}'
        )

        revised_plan, responses, _diff_entries = await agent.revise_plan(state, issues)

        assert responses[0].action == IssueResponseAction.ACCEPT

        synced = {fd.file_path: fd.risk_level for fd in state.file_diffs}
        assert synced["slack/manifest.yaml"] == RiskLevel.AUTO_RISKY, (
            "accepted escalation must propagate into state.file_diffs"
        )
        assert synced["a.py"] == RiskLevel.AUTO_SAFE, (
            "non-issue files must not be touched"
        )

        assert original_fd.risk_level == RiskLevel.AUTO_SAFE
        new_fd = next(
            fd for fd in state.file_diffs if fd.file_path == "slack/manifest.yaml"
        )
        assert new_fd is not original_fd

        risky = {
            fp
            for batch in revised_plan.phases
            if batch.risk_level == RiskLevel.AUTO_RISKY
            for fp in batch.file_paths
        }
        assert "slack/manifest.yaml" in risky

    @pytest.mark.asyncio
    async def test_revise_plan_rejected_issues_keep_classification(self):
        agent = self._make_agent()
        plan = _make_plan(
            [
                ("auto_merge", ["a.py"], "auto_safe"),
                ("conflict_analysis", ["b.py"], "auto_risky"),
            ]
        )
        state = MergeState(config=_make_config())
        state.merge_plan = plan

        issues = [
            _make_issue("b.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED),
        ]

        agent._call_llm_with_retry = AsyncMock(
            return_value='{"responses": [{"issue_id": "'
            + issues[0].issue_id
            + '", "file_path": "b.py", "action": "reject", "reason": "low risk file", "counter_proposal": null}]}'
        )

        revised_plan, responses, diff_entries = await agent.revise_plan(state, issues)

        assert responses[0].action == IssueResponseAction.REJECT

        risky_files = []
        for batch in revised_plan.phases:
            if batch.risk_level == RiskLevel.AUTO_RISKY:
                risky_files.extend(batch.file_paths)
        assert "b.py" in risky_files

        assert len(diff_entries) == 0


class TestSplitByRiskLevelOrdering:
    """P1-6: each bucket must come out ordered by (risk_score asc, path asc)
    so the Executor processes the safest files first within a batch."""

    def _make_diff(self, fp: str, score: float, level: RiskLevel) -> FileDiff:
        return FileDiff(
            file_path=fp,
            file_status=FileStatus.MODIFIED,
            risk_level=level,
            risk_score=score,
            lines_added=1,
            lines_deleted=0,
            lines_changed=1,
            conflict_count=0,
            hunks=[],
            is_security_sensitive=False,
            change_category=FileChangeCategory.B,
        )

    def test_safe_bucket_sorted_by_score_then_path(self):
        diffs = {
            "z.py": self._make_diff("z.py", 0.05, RiskLevel.AUTO_SAFE),
            "a.py": self._make_diff("a.py", 0.20, RiskLevel.AUTO_SAFE),
            "m.py": self._make_diff("m.py", 0.10, RiskLevel.AUTO_SAFE),
            "n.py": self._make_diff("n.py", 0.05, RiskLevel.AUTO_SAFE),
        }
        safe, risky, human = PlannerAgent._split_by_risk_level(
            list(diffs.keys()), diffs, set()
        )
        assert safe == ["n.py", "z.py", "m.py", "a.py"]
        assert risky == []
        assert human == []

    def test_each_bucket_independently_sorted(self):
        diffs = {
            "low_safe.py": self._make_diff("low_safe.py", 0.10, RiskLevel.AUTO_SAFE),
            "high_safe.py": self._make_diff("high_safe.py", 0.25, RiskLevel.AUTO_SAFE),
            "low_risky.py": self._make_diff("low_risky.py", 0.40, RiskLevel.AUTO_RISKY),
            "high_risky.py": self._make_diff(
                "high_risky.py", 0.55, RiskLevel.AUTO_RISKY
            ),
        }
        safe, risky, _human = PlannerAgent._split_by_risk_level(
            list(diffs.keys()), diffs, set()
        )
        assert safe == ["low_safe.py", "high_safe.py"]
        assert risky == ["low_risky.py", "high_risky.py"]

    def test_missing_diff_falls_back_to_zero_score(self):
        diffs = {"present.py": self._make_diff("present.py", 0.30, RiskLevel.AUTO_SAFE)}
        safe, _r, _h = PlannerAgent._split_by_risk_level(
            ["present.py", "missing.py"], diffs, set()
        )
        assert safe == ["missing.py", "present.py"]


class TestPlanReviewConvergence:
    @pytest.mark.asyncio
    async def test_stalls_when_plan_unchanged(self):
        from src.core.phases.plan_review import PlanReviewPhase
        from src.models.state import SystemStatus

        plan = _make_plan(
            [
                ("auto_merge", ["a.py"], "auto_safe"),
                ("conflict_analysis", ["b.py"], "auto_risky"),
            ]
        )

        state = MergeState(config=_make_config())
        state.merge_plan = plan
        state.file_classifications = {
            fp: batch.risk_level for batch in plan.phases for fp in batch.file_paths
        }

        verdict = PlanJudgeVerdict(
            result=PlanJudgeResult.REVISION_NEEDED,
            issues=[
                _make_issue("b.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED),
            ],
            approved_files_count=1,
            flagged_files_count=1,
            summary="Issues found",
            judge_model="test",
            timestamp=datetime.now(),
        )

        reject_response = PlannerIssueResponse(
            issue_id=verdict.issues[0].issue_id,
            file_path="b.py",
            action=IssueResponseAction.REJECT,
            reason="Low risk file, no need to escalate",
        )

        mock_judge = AsyncMock()
        mock_judge.review_plan = AsyncMock(return_value=verdict)

        mock_planner = AsyncMock()
        mock_planner.revise_plan = AsyncMock(return_value=(plan, [reject_response], []))

        mock_sm = MagicMock()
        mock_sm.transition = MagicMock()

        mock_config = MagicMock()
        mock_config.max_plan_revision_rounds = 5
        mock_config.output.directory = "/tmp/test_output"
        mock_config.output.language = "en"
        mock_config.plan_review.min_rounds_when_segmented = 0

        ctx = MagicMock()
        ctx.agents = {"planner": mock_planner, "planner_judge": mock_judge}
        ctx.config = mock_config
        ctx.state_machine = mock_sm

        phase = PlanReviewPhase()

        with patch("src.core.phases.plan_review.write_plan_review_report"):
            outcome = await phase.execute(state, ctx)

        assert outcome.target_status == SystemStatus.AWAITING_HUMAN
        assert "stalled" in outcome.reason

        assert mock_planner.revise_plan.call_count == 1

    @pytest.mark.asyncio
    async def test_approved_generates_user_decision_items(self):
        from src.core.phases.plan_review import PlanReviewPhase
        from src.models.state import SystemStatus

        plan = _make_plan(
            [
                ("auto_merge", ["a.py"], "auto_safe"),
                ("human_review", ["b.py"], "human_required"),
            ]
        )

        state = MergeState(config=_make_config())
        state.merge_plan = plan

        verdict = PlanJudgeVerdict(
            result=PlanJudgeResult.APPROVED,
            issues=[],
            approved_files_count=2,
            flagged_files_count=0,
            summary="Plan looks good",
            judge_model="test",
            timestamp=datetime.now(),
        )

        mock_judge = AsyncMock()
        mock_judge.review_plan = AsyncMock(return_value=verdict)

        mock_planner = AsyncMock()

        mock_sm = MagicMock()
        mock_sm.transition = MagicMock()

        mock_config = MagicMock()
        mock_config.max_plan_revision_rounds = 3
        mock_config.output.directory = "/tmp/test_output"
        mock_config.output.language = "en"
        mock_config.plan_review.min_rounds_when_segmented = 0

        ctx = MagicMock()
        ctx.agents = {"planner": mock_planner, "planner_judge": mock_judge}
        ctx.config = mock_config
        ctx.state_machine = mock_sm

        phase = PlanReviewPhase()

        with patch("src.core.phases.plan_review.write_plan_review_report"):
            outcome = await phase.execute(state, ctx)

        assert outcome.target_status == SystemStatus.AWAITING_HUMAN
        assert len(state.pending_user_decisions) == 1
        assert state.pending_user_decisions[0].file_path == "b.py"
        option_keys = {o.key for o in state.pending_user_decisions[0].options}
        # Base ladder (keep_head / take_target / llm_auto_merge) plus
        # always-emitted Round-2/3 extras: llm_with_instruction,
        # manual_paste, skip. The union_additions extra is data-driven
        # (requires both sides to be pure additions); absent here
        # because the synthetic plan carries no FileDiff entries.
        assert option_keys == {
            "keep_head",
            "take_target",
            "llm_auto_merge",
            "llm_with_instruction",
            "manual_paste",
            "skip",
        }
        kinds_by_key = {o.key: o.kind for o in state.pending_user_decisions[0].options}
        assert kinds_by_key["keep_head"] == "keep_head"
        assert kinds_by_key["take_target"] == "take_target"
        assert kinds_by_key["llm_auto_merge"] == "llm_default"
        assert kinds_by_key["llm_with_instruction"] == "llm_with_instruction"
        assert kinds_by_key["manual_paste"] == "manual_paste"
        assert kinds_by_key["skip"] == "skip"


class TestPlanRevisingViaPrecheck:
    """Exercise plan_revising through the *real* deterministic precheck —
    no mocked Judge verdict. A plan that under-classifies a file (classifier
    says AUTO_RISKY, plan parks it in an AUTO_SAFE batch) is a plan the
    PlannerJudge must reject; the phase then runs a revision round and
    converges once the Planner accepts the escalation. This is the
    LLM-independent path that makes plan_revising reachable even when the
    Judge LLM approves everything."""

    def _judge(self):
        from src.agents.planner_judge_agent import PlannerJudgeAgent

        with patch.dict("os.environ", {"TEST_KEY": "sk-test-dummy"}):
            return PlannerJudgeAgent(llm_config=_make_llm_config())

    def _planner(self) -> PlannerAgent:
        with patch.dict("os.environ", {"TEST_KEY": "sk-test-dummy"}):
            return PlannerAgent(llm_config=_make_llm_config())

    def _diff(self, fp: str, level: RiskLevel) -> FileDiff:
        return FileDiff(
            file_path=fp,
            file_status=FileStatus.MODIFIED,
            risk_level=level,
            risk_score=0.5,
            lines_added=3,
            lines_deleted=1,
            lines_changed=4,
            conflict_count=0,
            hunks=[],
            is_security_sensitive=False,
            change_category=FileChangeCategory.B,
        )

    def _approved_verdict(self) -> PlanJudgeVerdict:
        return PlanJudgeVerdict(
            result=PlanJudgeResult.APPROVED,
            issues=[],
            approved_files_count=2,
            flagged_files_count=0,
            summary="LLM sees nothing wrong",
            judge_model="test",
            timestamp=datetime.now(),
        )

    @pytest.mark.asyncio
    async def test_precheck_rejects_underclassified_plan(self):
        """Even with the LLM approving, a classifier/batch risk MISMATCH
        forces REVISION_NEEDED — the deterministic dispute that the
        plan_revising loop hinges on."""
        judge = self._judge()
        judge._review_single = AsyncMock(return_value=(self._approved_verdict(), {}))

        plan = _make_plan([("auto_merge", ["safe.py", "under.py"], "auto_safe")])
        file_diffs = [
            self._diff("safe.py", RiskLevel.AUTO_SAFE),
            self._diff("under.py", RiskLevel.AUTO_RISKY),
        ]

        verdict = await judge.review_plan(plan, file_diffs, revision_round=0)

        assert verdict.result == PlanJudgeResult.REVISION_NEEDED
        flagged = {iss.file_path for iss in verdict.issues}
        assert "under.py" in flagged
        under = next(iss for iss in verdict.issues if iss.file_path == "under.py")
        assert under.source == "precheck"
        assert under.suggested_classification == RiskLevel.AUTO_RISKY

    @pytest.mark.asyncio
    async def test_phase_drives_plan_revising_to_convergence(self):
        from src.core.phases.plan_review import PlanReviewPhase
        from src.models.state import SystemStatus

        judge = self._judge()
        judge._review_single = AsyncMock(return_value=(self._approved_verdict(), {}))

        planner = self._planner()

        async def _accept_all(plan, issues, lang="en", file_diffs=None):
            return [
                PlannerIssueResponse(
                    issue_id=iss.issue_id,
                    file_path=iss.file_path,
                    action=IssueResponseAction.ACCEPT,
                    reason="agreed — escalate",
                )
                for iss in issues
            ]

        planner._evaluate_judge_issues = AsyncMock(side_effect=_accept_all)

        plan = _make_plan([("auto_merge", ["safe.py", "under.py"], "auto_safe")])
        state = MergeState(
            config=MergeConfig(
                upstream_ref="upstream/main",
                fork_ref="origin/main",
                max_plan_revision_rounds=3,
            )
        )
        state.merge_plan = plan
        state.file_diffs = [
            self._diff("safe.py", RiskLevel.AUTO_SAFE),
            self._diff("under.py", RiskLevel.AUTO_RISKY),
        ]
        state.file_classifications = {
            "safe.py": RiskLevel.AUTO_SAFE,
            "under.py": RiskLevel.AUTO_RISKY,
        }

        mock_sm = MagicMock()
        mock_sm.transition = MagicMock()

        ctx = MagicMock()
        ctx.agents = {"planner": planner, "planner_judge": judge}
        ctx.config = state.config
        ctx.state_machine = mock_sm

        phase = PlanReviewPhase()
        with patch("src.core.phases.plan_review.write_plan_review_report"):
            outcome = await phase.execute(state, ctx)

        # A revision round actually ran (not short-circuited at round 0).
        planner._evaluate_judge_issues.assert_awaited()
        transition_targets = [c.args[1] for c in mock_sm.transition.call_args_list]
        assert SystemStatus.PLAN_REVISING in transition_targets
        assert state.plan_revision_rounds == 1

        # ...and the loop converged once the Planner accepted the escalation.
        assert state.review_conclusion.reason == ReviewConclusionReason.APPROVED
        risky = {
            fp
            for batch in state.merge_plan.phases
            if batch.risk_level == RiskLevel.AUTO_RISKY
            for fp in batch.file_paths
        }
        assert "under.py" in risky
        # No HUMAN_REQUIRED files remain, so the converged plan proceeds
        # straight to auto-merge.
        assert outcome.target_status == SystemStatus.AUTO_MERGING


class TestClassifyPriorIssues:
    def test_resolved_when_classification_matches_suggestion(self):
        from src.llm.prompts.planner_judge_prompts import classify_prior_issues

        issues = [
            _make_issue("a.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED),
            _make_issue("b.py", RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY),
        ]
        current = {
            "a.py": RiskLevel.HUMAN_REQUIRED,
            "b.py": RiskLevel.AUTO_RISKY,
        }

        resolved, still_open = classify_prior_issues(issues, current)
        assert len(resolved) == 2
        assert len(still_open) == 0

    def test_still_open_when_not_reclassified(self):
        from src.llm.prompts.planner_judge_prompts import classify_prior_issues

        issues = [
            _make_issue("a.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED),
            _make_issue("b.py", RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY),
        ]
        current = {
            "a.py": RiskLevel.AUTO_RISKY,
            "b.py": RiskLevel.AUTO_SAFE,
        }

        resolved, still_open = classify_prior_issues(issues, current)
        assert len(resolved) == 0
        assert len(still_open) == 2

    def test_mixed_resolved_and_open(self):
        from src.llm.prompts.planner_judge_prompts import classify_prior_issues

        issues = [
            _make_issue("a.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED),
            _make_issue("b.py", RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY),
        ]
        current = {
            "a.py": RiskLevel.HUMAN_REQUIRED,
            "b.py": RiskLevel.AUTO_SAFE,
        }

        resolved, still_open = classify_prior_issues(issues, current)
        assert len(resolved) == 1
        assert resolved[0].file_path == "a.py"
        assert len(still_open) == 1
        assert still_open[0].file_path == "b.py"


class TestBuildPlanReviewPromptHistory:
    def test_round_zero_has_no_history_section(self):
        from src.llm.prompts.planner_judge_prompts import build_plan_review_prompt

        plan = _make_plan([("auto_merge", ["a.py"], "auto_safe")])
        prompt = build_plan_review_prompt(plan, [], lang="en", revision_round=0)

        assert "Prior Review History" not in prompt
        assert "Resolved" not in prompt

    def test_round_one_includes_resolved_and_open(self):
        from src.llm.prompts.planner_judge_prompts import build_plan_review_prompt

        plan = _make_plan([("auto_merge", ["a.py"], "auto_safe")])
        resolved = [_make_issue("x.py", RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY)]
        still_open = [
            _make_issue("y.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED)
        ]

        prompt = build_plan_review_prompt(
            plan,
            [],
            lang="en",
            revision_round=1,
            prior_resolved=resolved,
            prior_still_open=still_open,
        )

        assert "Prior Review History" in prompt
        assert "x.py" in prompt
        assert "Resolved" in prompt
        assert "y.py" in prompt
        assert "Still Open" in prompt
        assert "Do NOT re-raise" in prompt

    def test_zh_lang_uses_chinese_headers(self):
        from src.llm.prompts.planner_judge_prompts import build_plan_review_prompt

        plan = _make_plan([("auto_merge", ["a.py"], "auto_safe")])
        resolved = [_make_issue("x.py", RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY)]

        prompt = build_plan_review_prompt(
            plan,
            [],
            lang="zh",
            revision_round=1,
            prior_resolved=resolved,
            prior_still_open=[],
        )

        assert "已解决" in prompt
        assert "仍未解决" in prompt

    def test_review_plan_passes_prior_issues_to_prompt(self):
        from src.llm.prompts.planner_judge_prompts import build_plan_review_prompt

        plan = _make_plan(
            [
                ("auto_merge", ["a.py", "b.py"], "auto_safe"),
                ("human_review", ["c.py"], "human_required"),
            ]
        )

        prompt = build_plan_review_prompt(
            plan,
            [],
            lang="en",
            revision_round=2,
            prior_resolved=[
                _make_issue("c.py", RiskLevel.AUTO_RISKY, RiskLevel.HUMAN_REQUIRED)
            ],
            prior_still_open=[
                _make_issue("b.py", RiskLevel.AUTO_SAFE, RiskLevel.AUTO_RISKY)
            ],
        )

        assert "c.py" in prompt
        assert "b.py" in prompt
        assert "round 2" in prompt

    def test_planner_responses_included_in_prompt(self):
        from src.llm.prompts.planner_judge_prompts import build_plan_review_prompt

        plan = _make_plan([("auto_merge", ["a.py"], "auto_safe")])
        responses = [
            PlannerIssueResponse(
                issue_id="test-1",
                file_path="x.py",
                action=IssueResponseAction.REJECT,
                reason="File is low risk config",
            ),
            PlannerIssueResponse(
                issue_id="test-2",
                file_path="y.py",
                action=IssueResponseAction.ACCEPT,
                reason="Agreed, security sensitive",
            ),
        ]

        prompt = build_plan_review_prompt(
            plan,
            [],
            lang="en",
            revision_round=1,
            planner_responses=responses,
        )

        assert "Planner's Responses" in prompt
        assert "Rejected" in prompt
        assert "x.py" in prompt
        assert "File is low risk config" in prompt
        assert "Accepted" in prompt
        assert "y.py" in prompt
