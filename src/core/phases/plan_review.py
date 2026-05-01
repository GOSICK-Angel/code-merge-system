from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from src.cli.paths import get_report_dir
from src.core.phases.base import Phase, PhaseContext, PhaseOutcome
from src.models.diff import FileDiff, RiskLevel
from src.models.plan import MergePhase, PlanValidationError, validate_plan_shape
from src.models.plan_judge import PlanIssue, PlanJudgeResult, PlanJudgeVerdict
from src.models.plan_review import (
    DecisionOption,
    IssueResponseAction,
    NegotiationMessage,
    PlanDiffEntry,
    PlannerIssueResponse,
    PlanReviewRound,
    ReviewConclusion,
    ReviewConclusionReason,
    UserDecisionItem,
)
from src.models.state import MergeState, PhaseResult, SystemStatus
from src.llm.prompts.planner_judge_prompts import classify_prior_issues
from src.tools.report_writer import write_plan_review_report

logger = logging.getLogger(__name__)


class PlanReviewPhase(Phase):
    name = "plan_review"

    async def execute(self, state: MergeState, ctx: PhaseContext) -> PhaseOutcome:
        state.current_phase = MergePhase.PLAN_REVIEW
        phase_result = PhaseResult(
            phase=MergePhase.PLAN_REVIEW,
            status="running",
            started_at=datetime.now(),
        )
        state.phase_results[MergePhase.PLAN_REVIEW.value] = phase_result

        planner = ctx.agents["planner"]
        planner_judge = ctx.agents["planner_judge"]
        file_diffs: list[FileDiff] = state.file_diffs
        max_rounds = ctx.config.max_plan_revision_rounds
        lang = ctx.config.output.language

        all_prior_issues: list[PlanIssue] = []
        last_planner_responses: list[PlannerIssueResponse] | None = None

        if state.merge_plan is not None:
            try:
                validate_plan_shape(state.merge_plan)
            except PlanValidationError as exc:
                logger.error(
                    "Plan shape validation failed before LLM judge: %s", exc
                )
                phase_result = phase_result.model_copy(
                    update={"status": "completed", "completed_at": datetime.now()}
                )
                state.phase_results[MergePhase.PLAN_REVIEW.value] = phase_result
                state.review_conclusion = ReviewConclusion(
                    reason=ReviewConclusionReason.LLM_FAILURE,
                    final_round=0,
                    total_rounds=0,
                    max_rounds=max_rounds,
                    summary=(
                        "Plan rejected before LLM review: structural defect "
                        f"in layer dependency graph — {exc}. Fix the planner "
                        "output (or layer config) and re-run."
                    ),
                )
                ctx.notify(
                    "planner_judge",
                    f"Plan structurally invalid: {exc}",
                )
                ctx.state_machine.transition(
                    state,
                    SystemStatus.AWAITING_HUMAN,
                    f"plan structurally invalid: {exc}",
                )
                return PhaseOutcome(
                    target_status=SystemStatus.AWAITING_HUMAN,
                    reason=f"plan structurally invalid: {exc}",
                    checkpoint_tag="after_phase1_5",
                )

        for round_num in range(max_rounds + 1):
            state.plan_revision_rounds = round_num

            assert state.merge_plan is not None

            prior_resolved: list[PlanIssue] = []
            prior_still_open: list[PlanIssue] = []
            if round_num > 0 and all_prior_issues:
                current_cls: dict[str, RiskLevel] = {
                    fp: batch.risk_level
                    for batch in state.merge_plan.phases
                    for fp in batch.file_paths
                }
                prior_resolved, prior_still_open = classify_prior_issues(
                    all_prior_issues, current_cls
                )

            verdict = await planner_judge.review_plan(
                state.merge_plan,
                file_diffs,
                round_num,
                lang=lang,
                prior_resolved=prior_resolved if round_num > 0 else None,
                prior_still_open=prior_still_open if round_num > 0 else None,
                planner_responses=last_planner_responses,
            )
            if verdict.result == PlanJudgeResult.REVISION_NEEDED and not verdict.issues:
                verdict = verdict.model_copy(
                    update={"result": PlanJudgeResult.APPROVED}
                )
            state.plan_judge_verdict = verdict

            seen_files = {iss.file_path for iss in all_prior_issues}
            for iss in verdict.issues:
                if iss.file_path not in seen_files:
                    all_prior_issues.append(iss)
                    seen_files.add(iss.file_path)

            negotiation_msgs: list[NegotiationMessage] = []
            negotiation_msgs.append(
                NegotiationMessage(
                    sender="planner_judge",
                    round_number=round_num,
                    content=verdict.summary or "",
                )
            )

            ctx.notify(
                "planner_judge",
                f"Round {round_num}: {verdict.result.value} "
                f"({len(verdict.issues)} issues) — {verdict.summary}",
            )

            is_llm_failure = verdict.result == PlanJudgeResult.LLM_UNAVAILABLE or (
                len(verdict.issues) == 0
                and verdict.summary
                and (
                    "parse failed" in verdict.summary.lower()
                    or "llm unavailable" in verdict.summary.lower()
                )
            )
            if is_llm_failure:
                # Do NOT mark as APPROVED. Surface to the user and require
                # explicit plan approval via plan_human_review before any merge.
                verdict = verdict.model_copy(
                    update={"result": PlanJudgeResult.LLM_UNAVAILABLE}
                )
                state.plan_judge_verdict = verdict
                round_log = self._build_round_log(round_num, verdict, negotiation_msgs)
                state.plan_review_log.append(round_log)
                state.review_conclusion = ReviewConclusion(
                    reason=ReviewConclusionReason.LLM_FAILURE,
                    final_round=round_num,
                    total_rounds=round_num + 1,
                    max_rounds=max_rounds,
                    summary=(
                        "Plan Judge LLM unavailable — plan was NOT reviewed. "
                        "Human approval required before merge."
                    ),
                )
                # Build one UserDecisionItem per actionable file so the
                # CLI/TUI can surface them as pending decisions even though
                # the judge never assigned HUMAN_REQUIRED.
                user_items = self._build_fallback_decision_items(state)
                state.pending_user_decisions = user_items
                self._complete_phase(state, phase_result, ctx)
                logger.warning(
                    "Plan judge LLM call failed (round %d) — "
                    "surfacing %d files for human review (no silent approval)",
                    round_num,
                    len(user_items),
                )
                ctx.state_machine.transition(
                    state,
                    SystemStatus.AWAITING_HUMAN,
                    "plan judge LLM unavailable — awaiting explicit human approval",
                )
                return PhaseOutcome(
                    target_status=SystemStatus.AWAITING_HUMAN,
                    reason="plan judge LLM unavailable",
                    checkpoint_tag="after_phase1_5",
                )

            if verdict.result == PlanJudgeResult.APPROVED:
                negotiation_msgs.append(
                    NegotiationMessage(
                        sender="planner",
                        round_number=round_num,
                        content="Plan approved by judge — both agents agree.",
                    )
                )
                round_log = self._build_round_log(round_num, verdict, negotiation_msgs)
                state.plan_review_log.append(round_log)

                user_items = self._build_user_decision_items(state)
                state.pending_user_decisions = user_items
                state.review_conclusion = ReviewConclusion(
                    reason=ReviewConclusionReason.APPROVED,
                    final_round=round_num,
                    total_rounds=round_num + 1,
                    max_rounds=max_rounds,
                    summary="Planner and Judge reached agreement — plan approved",
                    pending_decisions_count=len(user_items),
                )

                self._complete_phase(state, phase_result, ctx)

                if not user_items:
                    logger.info(
                        "Plan approved with no human-required files — "
                        "auto-proceeding to merge"
                    )
                    ctx.state_machine.transition(
                        state,
                        SystemStatus.AUTO_MERGING,
                        "plan approved, no human decisions needed",
                    )
                    return PhaseOutcome(
                        target_status=SystemStatus.AUTO_MERGING,
                        reason="plan approved, no human decisions needed",
                        checkpoint_tag="after_phase1_5",
                    )

                logger.info("Plan approved — proceeding to human review")
                ctx.state_machine.transition(
                    state,
                    SystemStatus.AWAITING_HUMAN,
                    "plan approved by both agents",
                )
                return PhaseOutcome(
                    target_status=SystemStatus.AWAITING_HUMAN,
                    reason="plan approved by both agents",
                    checkpoint_tag="after_phase1_5",
                )

            elif verdict.result == PlanJudgeResult.CRITICAL_REPLAN:
                round_log = self._build_round_log(round_num, verdict, negotiation_msgs)
                state.plan_review_log.append(round_log)
                state.review_conclusion = ReviewConclusion(
                    reason=ReviewConclusionReason.CRITICAL_REPLAN,
                    final_round=round_num,
                    total_rounds=round_num + 1,
                    max_rounds=max_rounds,
                    summary="Judge requested critical replan — regenerating entire plan",
                )
                ctx.state_machine.transition(
                    state, SystemStatus.PLANNING, "critical replan required"
                )
                from src.core.phases.planning import PlanningPhase

                planning = PlanningPhase()
                await planning.execute(state, ctx)
                return PhaseOutcome(
                    target_status=state.status,
                    reason="critical replan executed",
                    checkpoint_tag="after_phase1_5",
                )

            elif round_num < max_rounds:
                ctx.state_machine.transition(
                    state,
                    SystemStatus.PLAN_REVISING,
                    f"revision needed (round {round_num + 1}/{max_rounds})",
                )
                state.current_phase = MergePhase.PLAN_REVISING

                revised_plan, planner_responses, plan_diff = await planner.revise_plan(
                    state, verdict.issues, lang
                )

                accepted = [
                    r
                    for r in planner_responses
                    if r.action == IssueResponseAction.ACCEPT
                ]
                rejected = [
                    r
                    for r in planner_responses
                    if r.action == IssueResponseAction.REJECT
                ]
                discussed = [
                    r
                    for r in planner_responses
                    if r.action == IssueResponseAction.DISCUSS
                ]

                planner_summary = (
                    f"Evaluated {len(verdict.issues)} issues: "
                    f"{len(accepted)} accepted, "
                    f"{len(rejected)} rejected, "
                    f"{len(discussed)} under discussion"
                )

                ctx.notify("planner", f"Round {round_num}: {planner_summary}")
                for r in rejected:
                    ctx.notify(
                        "planner",
                        f"  REJECT {r.file_path}: {r.reason}",
                    )
                for r in discussed:
                    ctx.notify(
                        "planner",
                        f"  DISCUSS {r.file_path}: {r.reason}"
                        + (
                            f" | proposal: {r.counter_proposal}"
                            if r.counter_proposal
                            else ""
                        ),
                    )

                negotiation_msgs.append(
                    NegotiationMessage(
                        sender="planner",
                        round_number=round_num,
                        content=planner_summary,
                    )
                )

                old_classifications = {
                    fp: batch.risk_level
                    for batch in state.merge_plan.phases
                    for fp in batch.file_paths
                }
                new_classifications = {
                    fp: batch.risk_level
                    for batch in revised_plan.phases
                    for fp in batch.file_paths
                }
                plan_changed = old_classifications != new_classifications

                round_log = self._build_round_log(
                    round_num,
                    verdict,
                    negotiation_msgs,
                    planner_responses=planner_responses,
                    plan_diff=plan_diff,
                    revision_summary=planner_summary
                    if plan_changed
                    else (
                        f"Plan unchanged — all {len(rejected)} rejections "
                        f"kept current classifications"
                    ),
                )
                state.plan_review_log.append(round_log)

                state.merge_plan = revised_plan
                state.file_classifications = new_classifications
                last_planner_responses = planner_responses

                if not plan_changed and not discussed:
                    logger.warning(
                        "Plan revision had no effect at round %d — "
                        "no pending discussions, stopping review loop",
                        round_num,
                    )
                    user_items = self._build_user_decision_items(state)
                    state.pending_user_decisions = user_items

                    rej_details = [
                        {
                            "file_path": r.file_path,
                            "judge_suggested": next(
                                (
                                    iss.suggested_classification.value
                                    if hasattr(iss.suggested_classification, "value")
                                    else str(iss.suggested_classification)
                                    for iss in verdict.issues
                                    if iss.file_path == r.file_path
                                ),
                                "unknown",
                            ),
                            "planner_reason": r.reason,
                        }
                        for r in rejected
                    ]

                    state.review_conclusion = ReviewConclusion(
                        reason=ReviewConclusionReason.STALLED,
                        final_round=round_num,
                        total_rounds=round_num + 1,
                        max_rounds=max_rounds,
                        summary=f"Planner rejected all {len(rejected)} Judge suggestions at round {round_num} — no further progress possible",
                        pending_decisions_count=len(user_items),
                        rejection_details=rej_details,
                    )

                    self._complete_phase(state, phase_result, ctx)
                    ctx.state_machine.transition(
                        state,
                        SystemStatus.PLAN_REVIEWING,
                        "returning to review before exit",
                    )
                    ctx.state_machine.transition(
                        state,
                        SystemStatus.AWAITING_HUMAN,
                        f"plan revision stalled at round {round_num}",
                    )
                    return PhaseOutcome(
                        target_status=SystemStatus.AWAITING_HUMAN,
                        reason=f"plan revision stalled at round {round_num}",
                        checkpoint_tag="after_phase1_5",
                    )

                ctx.state_machine.transition(
                    state, SystemStatus.PLAN_REVIEWING, "revision complete"
                )
                state.current_phase = MergePhase.PLAN_REVIEW
            else:
                round_log = self._build_round_log(round_num, verdict, negotiation_msgs)
                state.plan_review_log.append(round_log)

                user_items = self._build_user_decision_items(state)
                state.pending_user_decisions = user_items
                state.review_conclusion = ReviewConclusion(
                    reason=ReviewConclusionReason.MAX_ROUNDS,
                    final_round=round_num,
                    total_rounds=round_num + 1,
                    max_rounds=max_rounds,
                    summary=f"Planner and Judge did not converge after {max_rounds} rounds — proceeding with last revised plan",
                    pending_decisions_count=len(user_items),
                )

                self._complete_phase(state, phase_result, ctx)
                logger.warning(
                    "Plan review did not converge after %d rounds — "
                    "proceeding with last revised plan",
                    max_rounds,
                )
                ctx.state_machine.transition(
                    state,
                    SystemStatus.AWAITING_HUMAN,
                    f"plan review did not converge after {max_rounds} rounds, "
                    f"proceeding with last plan",
                )
                return PhaseOutcome(
                    target_status=SystemStatus.AWAITING_HUMAN,
                    reason=f"plan review did not converge after {max_rounds} rounds",
                    checkpoint_tag="after_phase1_5",
                )

        return PhaseOutcome(
            target_status=state.status,
            reason="plan review loop exhausted",
            checkpoint_tag="after_phase1_5",
        )

    def _build_round_log(
        self,
        round_num: int,
        verdict: PlanJudgeVerdict,
        negotiation_msgs: list[NegotiationMessage],
        *,
        planner_responses: list[PlannerIssueResponse] | None = None,
        plan_diff: list[PlanDiffEntry] | None = None,
        revision_summary: str | None = None,
    ) -> PlanReviewRound:
        return PlanReviewRound(
            round_number=round_num,
            verdict_result=verdict.result,
            verdict_summary=verdict.summary,
            issues_count=len(verdict.issues),
            issues_detail=[
                {
                    "file_path": issue.file_path,
                    "reason": issue.reason,
                    "current": issue.current_classification.value
                    if hasattr(issue.current_classification, "value")
                    else str(issue.current_classification),
                    "suggested": issue.suggested_classification.value
                    if hasattr(issue.suggested_classification, "value")
                    else str(issue.suggested_classification),
                }
                for issue in verdict.issues
            ],
            planner_revision_summary=revision_summary,
            planner_responses=planner_responses or [],
            plan_diff=plan_diff or [],
            negotiation_messages=negotiation_msgs,
        )

    def _complete_phase(
        self,
        state: MergeState,
        phase_result: PhaseResult,
        ctx: PhaseContext,
    ) -> None:
        phase_result = phase_result.model_copy(
            update={"status": "completed", "completed_at": datetime.now()}
        )
        state.phase_results[MergePhase.PLAN_REVIEW.value] = phase_result
        write_plan_review_report(
            state,
            str(
                get_report_dir(
                    state.config.repo_path, state.run_id, ctx.config.output.directory
                )
            ),
        )

    # When LLM is unavailable we still need explicit user approval, but we
    # cap how many decision items we surface. A 1500-file plan would
    # otherwise produce 1500 pending decisions and the CLI/TUI is unusable.
    _FALLBACK_MAX_ITEMS: int = 200

    def _build_fallback_decision_items(
        self, state: MergeState
    ) -> list[UserDecisionItem]:
        """When the LLM judge is unavailable, surface only the files the user
        actually has to triage — HUMAN_REQUIRED + AUTO_RISKY — capped at
        ``_FALLBACK_MAX_ITEMS``.

        AUTO_SAFE files are excluded: the planner already classified them as
        safe, and there is no benefit in forcing the user to click through
        thousands of trivially-mergeable files just because the LLM judge
        couldn't run. A single batch ``approve_plan`` decision is appended so
        the user can accept the remaining unreviewed plan in one click.
        """
        if state.merge_plan is None:
            return []

        diff_map = self._collect_file_diff_info(state)
        cap = self._FALLBACK_MAX_ITEMS

        risky_levels = {RiskLevel.HUMAN_REQUIRED, RiskLevel.AUTO_RISKY}
        items: list[UserDecisionItem] = []
        truncated = 0
        auto_safe_count = 0
        for batch in state.merge_plan.phases:
            if batch.risk_level not in risky_levels:
                if batch.risk_level == RiskLevel.AUTO_SAFE:
                    auto_safe_count += len(batch.file_paths)
                continue
            for fp in batch.file_paths:
                if len(items) >= cap:
                    truncated += 1
                    continue
                context = self._build_risk_context(fp, batch.risk_level, {}, diff_map)
                options = self._build_decision_options(fp, batch.risk_level, context)
                description = (
                    "Plan Judge LLM was unavailable; human approval required. "
                    + self._build_description(fp, batch.risk_level, context)
                )
                items.append(
                    UserDecisionItem(
                        item_id=str(uuid4()),
                        file_path=fp,
                        description=description,
                        risk_context=context,
                        current_classification=batch.risk_level.value,
                        options=options,
                    )
                )

        if auto_safe_count or truncated:
            logger.warning(
                "plan_review fallback: hid %d AUTO_SAFE files and truncated "
                "%d additional risky files (cap=%d) from pending decisions",
                auto_safe_count,
                truncated,
                cap,
            )
        return items

    def _build_user_decision_items(self, state: MergeState) -> list[UserDecisionItem]:
        if state.merge_plan is None:
            return []

        issue_reasons = self._collect_issue_reasons(state)
        diff_map = self._collect_file_diff_info(state)

        items: list[UserDecisionItem] = []
        for batch in state.merge_plan.phases:
            if batch.risk_level != RiskLevel.HUMAN_REQUIRED:
                continue

            for fp in batch.file_paths:
                context = self._build_risk_context(
                    fp, batch.risk_level, issue_reasons, diff_map
                )
                options = self._build_decision_options(fp, batch.risk_level, context)
                description = self._build_description(fp, batch.risk_level, context)

                items.append(
                    UserDecisionItem(
                        item_id=str(uuid4()),
                        file_path=fp,
                        description=description,
                        risk_context=context,
                        current_classification=batch.risk_level.value,
                        options=options,
                    )
                )

        return items

    def _collect_issue_reasons(self, state: MergeState) -> dict[str, list[str]]:
        reasons: dict[str, list[str]] = {}
        for rnd in state.plan_review_log:
            for iss in rnd.issues_detail:
                fp = iss.get("file_path", "")
                reason = iss.get("reason", "")
                if fp and reason:
                    reasons.setdefault(fp, []).append(reason)
        if state.plan_judge_verdict:
            for issue in state.plan_judge_verdict.issues:
                if issue.file_path and issue.reason:
                    reasons.setdefault(issue.file_path, []).append(issue.reason)
        for fp in reasons:
            reasons[fp] = list(dict.fromkeys(reasons[fp]))
        return reasons

    def _collect_file_diff_info(self, state: MergeState) -> dict[str, dict[str, Any]]:
        diffs: list[FileDiff] = state.file_diffs
        result: dict[str, dict[str, Any]] = {}
        for fd in diffs:
            result[fd.file_path] = {
                "risk_score": fd.risk_score,
                "lines_added": fd.lines_added,
                "lines_deleted": fd.lines_deleted,
                "is_security_sensitive": fd.is_security_sensitive,
                "language": fd.language,
            }
        return result

    def _build_risk_context(
        self,
        file_path: str,
        risk_level: RiskLevel,
        issue_reasons: dict[str, list[str]],
        diff_map: dict[str, dict[str, Any]],
    ) -> str:
        parts: list[str] = []

        diff_info = diff_map.get(file_path)
        if diff_info:
            parts.append(
                f"+{diff_info['lines_added']}/-{diff_info['lines_deleted']} lines, "
                f"risk_score={diff_info['risk_score']:.2f}"
            )
            if diff_info.get("is_security_sensitive"):
                parts.append("security-sensitive file")

        reasons = issue_reasons.get(file_path)
        if reasons:
            parts.append(f"Judge: {reasons[0]}")

        if not parts:
            if risk_level == RiskLevel.HUMAN_REQUIRED:
                parts.append(
                    "Both upstream and fork modified this file — "
                    "conflicts detected that require human judgment"
                )
            else:
                parts.append(
                    "Both sides modified this file — "
                    "automated merge possible but may need verification"
                )

        return "; ".join(parts)

    def _build_description(
        self,
        file_path: str,
        risk_level: RiskLevel,
        context: str,
    ) -> str:
        if risk_level == RiskLevel.HUMAN_REQUIRED:
            return f"This file cannot be auto-merged safely. {context}"
        return f"This file can be auto-merged but has elevated risk. {context}"

    def _build_decision_options(
        self,
        file_path: str,
        risk_level: RiskLevel,
        context: str,
    ) -> list[DecisionOption]:
        is_security = "security-sensitive" in context

        if risk_level == RiskLevel.HUMAN_REQUIRED:
            return [
                DecisionOption(
                    key="approve_human",
                    label="Manual review",
                    description="You will review and resolve conflicts by hand "
                    "before this file is merged",
                ),
                DecisionOption(
                    key="downgrade_risky",
                    label="Auto-merge with verification",
                    description="System will attempt automated merge, then run "
                    "quality gates to verify correctness"
                    + (
                        " (not recommended: security-sensitive file)"
                        if is_security
                        else ""
                    ),
                ),
                DecisionOption(
                    key="downgrade_safe",
                    label="Auto-merge (trust system)",
                    description="System will merge automatically without extra "
                    "verification — use only if you are confident the changes "
                    "are trivial"
                    + (" (WARNING: security-sensitive file)" if is_security else ""),
                ),
            ]

        return [
            DecisionOption(
                key="confirm_risky",
                label="Auto-merge with verification",
                description="System will attempt automated merge, then run "
                "quality gates to verify correctness",
            ),
            DecisionOption(
                key="upgrade_human",
                label="Escalate to manual review",
                description="You will review and resolve conflicts by hand — "
                "recommended if the changes look complex or risky"
                + (" (recommended: security-sensitive file)" if is_security else ""),
            ),
            DecisionOption(
                key="downgrade_safe",
                label="Auto-merge (trust system)",
                description="System will merge automatically without extra "
                "verification — use only if you are confident the changes "
                "are trivial",
            ),
        ]
