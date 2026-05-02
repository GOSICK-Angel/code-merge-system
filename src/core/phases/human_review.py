from __future__ import annotations

import logging

from src.cli.paths import get_report_dir
from src.core.phases.base import Phase, PhaseContext, PhaseOutcome
from src.models.diff import RiskLevel
from src.models.plan import MergePhase
from src.models.plan_review import PlanHumanDecision
from src.models.state import MergeState, SystemStatus
from src.tools.merge_plan_report import write_merge_plan_report
from src.tools.report_writer import write_plan_review_report
from src.tools.commit_replayer import CommitReplayer
from src.tools.git_committer import GitCommitter

logger = logging.getLogger(__name__)


class HumanReviewPhase(Phase):
    """Handles the AWAITING_HUMAN state.

    This phase either:
    - Generates a plan report and pauses (returns early)
    - Routes a human decision (approve/reject) to the next state
    """

    name = "human_review"

    async def execute(self, state: MergeState, ctx: PhaseContext) -> PhaseOutcome:
        logger.info("Entering AWAITING_HUMAN status")

        # O-6: if conflict decisions are still pending, go to Case 1 first.
        _has_pending_conflict_decisions = bool(
            state.human_decision_requests
            and any(
                r.human_decision is None for r in state.human_decision_requests.values()
            )
        )

        # Case 0: judge review already ran and paused for human acknowledgement.
        # If the user set `state.judge_resolution` via the CLI (resume
        # --decisions), route accordingly so --no-tui users are not deadlocked.
        if (
            not _has_pending_conflict_decisions
            and state.judge_verdict is not None
            and state.current_phase == MergePhase.JUDGE_REVIEW
            and state.judge_resolution is not None
        ):
            res = state.judge_resolution
            if res == "accept":
                ctx.state_machine.transition(
                    state,
                    SystemStatus.GENERATING_REPORT,
                    "user accepted judge verdict (report only)",
                )
                return PhaseOutcome(
                    target_status=SystemStatus.GENERATING_REPORT,
                    reason="user accepted judge verdict",
                    checkpoint_tag="judge_accepted",
                )
            if res == "abort":
                ctx.state_machine.transition(
                    state,
                    SystemStatus.FAILED,
                    "user aborted after judge FAIL",
                )
                return PhaseOutcome(
                    target_status=SystemStatus.FAILED,
                    reason="user aborted after judge FAIL",
                    checkpoint_tag="judge_aborted",
                )
            if res == "rerun":
                # Clear resolution so next pause requires fresh input
                state.judge_resolution = None
                ctx.state_machine.transition(
                    state,
                    SystemStatus.AUTO_MERGING,
                    "user requested rerun of auto-merge after judge FAIL",
                )
                return PhaseOutcome(
                    target_status=SystemStatus.AUTO_MERGING,
                    reason="user requested rerun",
                    checkpoint_tag="judge_rerun",
                )

        # Guard against O-L1 loop: once judge_review has produced a verdict and
        # is paused for human adjudication, all conflict human_decision_requests
        # are already resolved & executed. Falling into Case 1's "not pending"
        # branch would re-transition to JUDGE_REVIEWING and loop indefinitely.
        if (
            state.judge_verdict is not None
            and state.current_phase == MergePhase.JUDGE_REVIEW
            and state.judge_resolution is None
        ):
            logger.info(
                "judge_review pending human resolution — staying in AWAITING_HUMAN"
            )
            return PhaseOutcome(
                target_status=SystemStatus.AWAITING_HUMAN,
                reason="judge verdict requires human resolution (accept/rerun/abort)",
                checkpoint_tag="judge_resolution_required",
                extra={"paused": True},
            )

        # Case 1: waiting for file-level conflict decisions from conflict analysis
        if state.human_decision_requests:
            pending = [
                req
                for req in state.human_decision_requests.values()
                if req.human_decision is None
            ]
            if not pending:
                executor = ctx.agents["executor"]
                executed = 0
                for req in state.human_decision_requests.values():
                    if req.file_path in state.file_decision_records:
                        continue
                    try:
                        record = await executor.execute_human_decision(req, state)
                        state.file_decision_records[req.file_path] = record
                        executed += 1
                    except Exception as e:
                        logger.error(
                            "Failed to execute human decision for %s: %s",
                            req.file_path,
                            e,
                        )
                logger.info(
                    "Executed %d human decisions — proceeding to judge review",
                    executed,
                )

                # O-B4-e2e-gap: if AUTO_MERGE was skipped on resume (phase
                # was already awaiting_human), any binary files still in
                # merge_plan without a file_decision_record never got their
                # O-B4 bytes path. Catch them up here so commit doesn't
                # leave the working tree in a half-merged state.
                if state.merge_plan is not None:
                    from src.tools.binary_assets import is_binary_asset
                    from src.tools.patch_applier import apply_bytes_with_snapshot

                    binary_catchup: list[str] = []
                    for batch in state.merge_plan.phases:
                        for fp in batch.file_paths:
                            if fp in state.file_decision_records:
                                continue
                            if not is_binary_asset(fp):
                                continue
                            binary_catchup.append(fp)
                    if binary_catchup:
                        logger.info(
                            "O-B4-e2e-gap: catching up %d binary asset(s) "
                            "that missed AUTO_MERGE on this resume",
                            len(binary_catchup),
                        )
                        from src.models.decision import (
                            DecisionSource,
                            FileDecisionRecord,
                            MergeDecision,
                        )
                        from src.models.diff import FileStatus

                        for fp in binary_catchup:
                            try:
                                content_bytes = ctx.git_tool.get_file_bytes(
                                    state.config.upstream_ref, fp
                                )
                                if content_bytes is None:
                                    raise RuntimeError("upstream bytes not found")
                                record = await apply_bytes_with_snapshot(
                                    fp,
                                    content_bytes,
                                    ctx.git_tool,
                                    state,
                                    phase="human_review",
                                    agent="binary_asset_catchup",
                                    decision=MergeDecision.TAKE_TARGET,
                                    rationale=(
                                        "O-B4-e2e-gap: binary TAKE_TARGET "
                                        "catch-up after AUTO_MERGE skip."
                                    ),
                                )
                            except Exception as exc:
                                logger.warning(
                                    "O-B4-e2e-gap catch-up failed for %s: %s",
                                    fp,
                                    exc,
                                )
                                record = FileDecisionRecord(
                                    file_path=fp,
                                    file_status=FileStatus.MODIFIED,
                                    decision=MergeDecision.ESCALATE_HUMAN,
                                    decision_source=DecisionSource.AUTO_EXECUTOR,
                                    confidence=0.0,
                                    rationale=(
                                        f"O-B4-e2e-gap catch-up failed ({exc!r})"
                                    ),
                                    phase="human_review",
                                    agent="binary_asset_catchup",
                                )
                            state.file_decision_records[fp] = record

                    # O-B4-e2e-gap (extension): also catch up B-class and
                    # D-missing text files that missed AUTO_MERGE on resume.
                    # Without this, layer 1+ B/D-missing files stay at fork
                    # content in the working tree, causing Judge to flag them
                    # as "B-class file differs from upstream after merge" or
                    # "D-missing file not present in HEAD after merge".
                    from src.tools.patch_applier import apply_with_snapshot
                    from src.models.diff import FileChangeCategory

                    text_catchup: list[str] = []
                    categories = state.file_categories or {}
                    for batch in state.merge_plan.phases:
                        for fp in batch.file_paths:
                            if fp in state.file_decision_records:
                                continue
                            if is_binary_asset(fp):
                                continue
                            cat = categories.get(fp)
                            if cat in (
                                FileChangeCategory.B,
                                FileChangeCategory.D_MISSING,
                            ):
                                text_catchup.append(fp)

                    text_catchup_applied: list[str] = []
                    if text_catchup:
                        logger.info(
                            "O-B4-e2e-gap: catching up %d B/D-missing text "
                            "file(s) that missed AUTO_MERGE on this resume",
                            len(text_catchup),
                        )
                        from src.models.decision import (
                            DecisionSource,
                            FileDecisionRecord,
                            MergeDecision,
                        )
                        from src.models.diff import FileStatus

                        for fp in text_catchup:
                            try:
                                content = ctx.git_tool.get_file_content(
                                    state.config.upstream_ref, fp
                                )
                                if content is None:
                                    raise RuntimeError(
                                        "upstream text content not found"
                                    )
                                record = await apply_with_snapshot(
                                    fp,
                                    content,
                                    ctx.git_tool,
                                    state,
                                    phase="human_review",
                                    agent="b_d_text_catchup",
                                    decision=MergeDecision.TAKE_TARGET,
                                    rationale=(
                                        "O-B4-e2e-gap: B/D-missing text "
                                        "TAKE_TARGET catch-up after "
                                        "AUTO_MERGE skip on resume."
                                    ),
                                )
                                if not record.is_rolled_back:
                                    text_catchup_applied.append(fp)
                            except Exception as exc:
                                logger.warning(
                                    "O-B4-e2e-gap text catch-up failed for %s: %s",
                                    fp,
                                    exc,
                                )
                                record = FileDecisionRecord(
                                    file_path=fp,
                                    file_status=FileStatus.MODIFIED,
                                    decision=MergeDecision.ESCALATE_HUMAN,
                                    decision_source=DecisionSource.AUTO_EXECUTOR,
                                    confidence=0.0,
                                    rationale=(
                                        f"O-B4-e2e-gap text catch-up failed ({exc!r})"
                                    ),
                                    phase="human_review",
                                    agent="b_d_text_catchup",
                                )
                            state.file_decision_records[fp] = record

                if ctx.config.history.enabled and ctx.config.history.commit_after_phase:
                    human_files = [
                        req.file_path
                        for req in state.human_decision_requests.values()
                        if req.file_path in state.file_decision_records
                        and not state.file_decision_records[
                            req.file_path
                        ].is_rolled_back
                    ]
                    # O-B4-e2e-gap: include text catch-up files in the
                    # commit so the working tree is reflected in HEAD.
                    catchup_extras = [
                        fp
                        for fp in locals().get("text_catchup_applied", [])
                        if fp not in human_files
                    ]
                    human_files = human_files + catchup_extras
                    if human_files:
                        committer = GitCommitter()
                        replayer = CommitReplayer()
                        upstream_ctx = replayer.collect_upstream_messages(
                            ctx.git_tool,
                            state.merge_base_commit,
                            state.config.upstream_ref,
                            human_files,
                        )
                        committer.commit_phase_changes(
                            ctx.git_tool,
                            state,
                            "human_review",
                            human_files,
                            upstream_context=upstream_ctx,
                        )

                _pending_conflict = _unanalyzed_conflict_files(state)
                if _pending_conflict:
                    logger.info(
                        "resume-path: %d conflict file(s) still unanalyzed "
                        "after human decisions — routing to ANALYZING_CONFLICTS",
                        len(_pending_conflict),
                    )
                    ctx.state_machine.transition(
                        state,
                        SystemStatus.ANALYZING_CONFLICTS,
                        "pending conflict files require analysis before judge review",
                    )
                    return PhaseOutcome(
                        target_status=SystemStatus.ANALYZING_CONFLICTS,
                        reason="pending conflict files require analysis",
                        checkpoint_tag="after_human_decisions_to_conflict",
                        memory_phase="conflict_analysis",
                    )
                ctx.state_machine.transition(
                    state,
                    SystemStatus.JUDGE_REVIEWING,
                    "all human conflict decisions complete",
                )
                return PhaseOutcome(
                    target_status=SystemStatus.JUDGE_REVIEWING,
                    reason="all human conflict decisions complete",
                    checkpoint_tag="after_human_decisions",
                    memory_phase="conflict_analysis",
                )
            logger.info(
                "%d/%d conflict decisions still pending",
                len(pending),
                len(state.human_decision_requests),
            )
            return PhaseOutcome(
                target_status=SystemStatus.AWAITING_HUMAN,
                reason=f"{len(pending)} conflict decisions pending",
                checkpoint_tag="awaiting_human",
                extra={"paused": True},
            )

        # Case 2: waiting for plan human review
        if state.plan_human_review is None and state.merge_plan:
            ctx.notify("orchestrator", "Generating merge plan report")
            report_path = write_merge_plan_report(state)
            state.messages.append(
                {
                    "type": "plan_report",
                    "from": "orchestrator",
                    "to": "human",
                    "content": str(report_path),
                }
            )
            ctx.notify("orchestrator", f"Plan report: {report_path}")

        if state.plan_human_review is not None:
            write_plan_review_report(
                state,
                str(
                    get_report_dir(
                        state.config.repo_path,
                        state.run_id,
                        ctx.config.output.directory,
                    )
                ),
            )
            if state.plan_human_review.decision == PlanHumanDecision.APPROVE:
                # O-L4 guard: if AUTO_MERGE appended new undecided items
                # (conflict_markers_*, binary_asset_*, human_required_*)
                # after the original plan approval, stay in AWAITING_HUMAN
                # until the user decides them. Without this, the state
                # machine ping-pongs AUTO_MERGING ↔ AWAITING_HUMAN every
                # ~20s because AUTO_MERGE's pre-pass bounces back here and
                # Case 2 sees plan approved → AUTO_MERGING.
                undecided_items = [
                    it for it in state.pending_user_decisions if it.user_choice is None
                ]
                if undecided_items:
                    logger.info(
                        "O-L4: %d pending_user_decisions item(s) undecided "
                        "after plan approval — staying in AWAITING_HUMAN",
                        len(undecided_items),
                    )
                    return PhaseOutcome(
                        target_status=SystemStatus.AWAITING_HUMAN,
                        reason=(
                            f"{len(undecided_items)} plan-level items "
                            "undecided after approval"
                        ),
                        checkpoint_tag="awaiting_human_post_approval_items",
                        extra={"paused": True},
                    )
                # O-L3 guard: if AUTO_MERGE previously exhausted its dispute
                # budget for one or more batches, do NOT bounce back into
                # AUTO_MERGING. Route to JUDGE_REVIEWING so the final verdict
                # reflects whatever was merged plus user resolutions; the
                # state machine's own guards will decide next step.
                if state.auto_merge_dispute_exhausted_layers:
                    logger.info(
                        "auto_merge dispute exhausted for layers %s — "
                        "routing to JUDGE_REVIEWING instead of AUTO_MERGING",
                        state.auto_merge_dispute_exhausted_layers,
                    )
                    ctx.state_machine.transition(
                        state,
                        SystemStatus.JUDGE_REVIEWING,
                        "auto_merge dispute exhausted; skip re-entry to AUTO_MERGING",
                    )
                    return PhaseOutcome(
                        target_status=SystemStatus.JUDGE_REVIEWING,
                        reason="auto_merge dispute exhausted",
                        checkpoint_tag="after_auto_merge_exhausted",
                        memory_phase="auto_merge",
                    )
                ctx.state_machine.transition(
                    state,
                    SystemStatus.AUTO_MERGING,
                    "plan approved by human reviewer",
                )
                return PhaseOutcome(
                    target_status=SystemStatus.AUTO_MERGING,
                    reason="plan approved by human reviewer",
                    checkpoint_tag="plan_approved",
                )
            elif state.plan_human_review.decision == PlanHumanDecision.REJECT:
                ctx.state_machine.transition(
                    state,
                    SystemStatus.FAILED,
                    "plan rejected by human reviewer",
                )
                return PhaseOutcome(
                    target_status=SystemStatus.FAILED,
                    reason="plan rejected by human reviewer",
                    checkpoint_tag="plan_rejected",
                )
            else:
                return PhaseOutcome(
                    target_status=SystemStatus.AWAITING_HUMAN,
                    reason="awaiting human decision (modify)",
                    checkpoint_tag="awaiting_human",
                    extra={"paused": True},
                )
        else:
            return PhaseOutcome(
                target_status=SystemStatus.AWAITING_HUMAN,
                reason="awaiting human decision",
                checkpoint_tag="awaiting_human",
                extra={"paused": True},
            )


def _unanalyzed_conflict_files(state: MergeState) -> list[str]:
    """Return files that still need conflict_analysis on the resume path.

    Covers two sources that ConflictAnalysisPhase reads as its worklist:
    1. merge_plan batches with risk_level in (HUMAN_REQUIRED, AUTO_RISKY)
    2. state.pending_conflict_files surfaced by auto_merge (skipped layers)

    A file is considered 'unanalyzed' when it has no entry in
    state.file_decision_records yet.
    """
    decided: set[str] = set(state.file_decision_records)
    pending: list[str] = []

    if state.merge_plan:
        _conflict_risks = {RiskLevel.HUMAN_REQUIRED, RiskLevel.AUTO_RISKY}
        for batch in state.merge_plan.phases:
            if batch.risk_level in _conflict_risks:
                for fp in batch.file_paths:
                    if fp not in decided:
                        pending.append(fp)

    seen: set[str] = set(pending)
    for fp in state.pending_conflict_files or []:
        if fp not in decided and fp not in seen:
            pending.append(fp)
            seen.add(fp)

    return pending
