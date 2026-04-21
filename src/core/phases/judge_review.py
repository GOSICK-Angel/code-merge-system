from __future__ import annotations

import logging
from datetime import datetime

from src.core.phases.base import Phase, PhaseContext, PhaseOutcome
from src.core.phases._gate_helpers import (
    append_judge_record,
    handle_gate_failure,
    run_gates,
)
from src.core.read_only_state_view import ReadOnlyStateView
from src.models.judge import (
    IssueSeverity,
    JudgeIssue,
    VerdictType,
)
from src.models.plan import MergePhase
from src.models.state import MergeState, PhaseResult, SystemStatus

logger = logging.getLogger(__name__)


class JudgeReviewPhase(Phase):
    name = "judge_review"

    async def execute(self, state: MergeState, ctx: PhaseContext) -> PhaseOutcome:
        state.current_phase = MergePhase.JUDGE_REVIEW
        phase_result = PhaseResult(
            phase=MergePhase.JUDGE_REVIEW,
            status="running",
            started_at=datetime.now(),
        )
        state.phase_results[MergePhase.JUDGE_REVIEW.value] = phase_result

        judge = ctx.agents["judge"]
        executor = ctx.agents["executor"]
        max_rounds = ctx.config.max_dispute_rounds
        state.judge_repair_rounds = 0

        for round_num in range(max_rounds):
            state.judge_repair_rounds = round_num
            executor.reset_circuit_breaker()

            readonly = ReadOnlyStateView(state)
            msg = await judge.run(readonly)
            verdict_data = msg.payload.get("verdict")
            if verdict_data:
                from src.models.judge import JudgeVerdict as JV

                state.judge_verdict = JV.model_validate(verdict_data)

            customization_violations = judge.verify_customizations(
                ctx.config.customizations,
                merge_base=state.merge_base_commit,
            )
            if state.judge_verdict and customization_violations:
                state.judge_verdict = state.judge_verdict.model_copy(
                    update={
                        "customization_violations": customization_violations,
                        "veto_triggered": True,
                        "veto_reason": (
                            "Customization(s) lost: "
                            f"{', '.join(v.customization_name for v in customization_violations)}"
                        ),
                        "verdict": VerdictType.FAIL,
                    }
                )

            state.judge_verdicts_log.append(
                {
                    "round": round_num,
                    "verdict": state.judge_verdict.verdict.value
                    if state.judge_verdict
                    else "none",
                    "timestamp": datetime.now().isoformat(),
                    "issues_count": len(state.judge_verdict.issues)
                    if state.judge_verdict
                    else 0,
                    "veto": state.judge_verdict.veto_triggered
                    if state.judge_verdict
                    else False,
                }
            )

            append_judge_record(state, round_num)

            if state.judge_verdict is None:
                break

            if state.judge_verdict.verdict == VerdictType.PASS:
                logger.info("Judge PASS on round %d", round_num)
                break

            # No VETO hard-stop: enter Executor ↔ Judge negotiation
            logger.info(
                "Judge non-PASS on round %d (veto=%s): attempting negotiation",
                round_num,
                state.judge_verdict.veto_triggered,
            )

            rebuttal = await executor.build_rebuttal(state.judge_verdict.issues, state)

            if rebuttal.accepts_all:
                repairable = [
                    r for r in rebuttal.repair_instructions if r.is_repairable
                ]
                if repairable and round_num < max_rounds - 1:
                    logger.info(
                        "Executor accepts all issues; repairing %d items (round %d/%d)",
                        len(repairable),
                        round_num + 1,
                        max_rounds,
                    )
                    await executor.repair(repairable, state)
                    ctx.checkpoint.save(state, f"phase5_repair_{round_num}")
                continue

            # Executor disputes some issues — judge re-evaluates
            from src.models.judge import BatchVerdict as BV

            proxy_verdict = BV(
                layer_id=None,
                approved=False,
                issues=state.judge_verdict.issues,
                repair_instructions=state.judge_verdict.repair_instructions,
                reviewed_files=state.judge_verdict.passed_files
                + state.judge_verdict.failed_files,
                round_num=round_num,
            )
            batch_verdict = await judge.re_evaluate(rebuttal, proxy_verdict, readonly)

            remaining_issues = batch_verdict.issues
            if batch_verdict.approved:
                logger.info(
                    "Judge accepts rebuttal on round %d — consensus reached", round_num
                )
                state.judge_verdict = state.judge_verdict.model_copy(
                    update={
                        "verdict": VerdictType.PASS,
                        "issues": remaining_issues,
                        "veto_triggered": False,
                        "veto_reason": None,
                    }
                )
                break
            else:
                state.judge_verdict = state.judge_verdict.model_copy(
                    update={"issues": remaining_issues}
                )
                logger.info(
                    "Judge maintains %d issues after rebuttal on round %d",
                    len(remaining_issues),
                    round_num,
                )

        phase_result = phase_result.model_copy(
            update={"status": "completed", "completed_at": datetime.now()}
        )
        state.phase_results[MergePhase.JUDGE_REVIEW.value] = phase_result

        gate_ok = await run_gates(state, ctx, "judge_review")
        if not gate_ok:
            gate_blocked = await handle_gate_failure(state, ctx)
            if gate_blocked:
                return PhaseOutcome(
                    target_status=SystemStatus.AWAITING_HUMAN,
                    reason="gate failure after judge review",
                    checkpoint_tag="after_phase5",
                    memory_phase="judge_review",
                )

        if state.judge_verdict is None:
            ctx.state_machine.transition(
                state,
                SystemStatus.GENERATING_REPORT,
                "judge review complete (no verdict)",
            )
            return PhaseOutcome(
                target_status=SystemStatus.GENERATING_REPORT,
                reason="judge review complete (no verdict)",
                checkpoint_tag="after_phase5",
                memory_phase="judge_review",
            )

        # Final routing: consensus reached (PASS) or escalate to human
        if state.judge_verdict.verdict == VerdictType.PASS:
            await self._run_smoke_tests(state, ctx)
            # Smoke tests may downgrade verdict to FAIL
            if state.judge_verdict.verdict != VerdictType.PASS:
                reason = (
                    f"smoke test failed: {state.judge_verdict.veto_reason}"
                    if state.judge_verdict.veto_reason
                    else "smoke test failed"
                )
                ctx.state_machine.transition(state, SystemStatus.AWAITING_HUMAN, reason)
                return PhaseOutcome(
                    target_status=SystemStatus.AWAITING_HUMAN,
                    reason=reason,
                    checkpoint_tag="after_phase5_smoke",
                    memory_phase="judge_review",
                )
            ctx.state_machine.transition(
                state, SystemStatus.GENERATING_REPORT, "judge verdict: PASS"
            )
            return PhaseOutcome(
                target_status=SystemStatus.GENERATING_REPORT,
                reason="judge verdict: PASS",
                checkpoint_tag="after_phase5",
                memory_phase="judge_review",
            )

        # No consensus after all dispute rounds → human decision
        reason = (
            f"judge verdict: no consensus after {state.judge_repair_rounds + 1} "
            f"dispute rounds"
        )
        ctx.state_machine.transition(state, SystemStatus.AWAITING_HUMAN, reason)
        return PhaseOutcome(
            target_status=SystemStatus.AWAITING_HUMAN,
            reason=reason,
            checkpoint_tag="after_phase5",
            memory_phase="judge_review",
        )

    async def _run_smoke_tests(self, state: MergeState, ctx: PhaseContext) -> None:
        """P1-3 Phase 5.5: run smoke tests after Judge PASS.

        If any case fails and ``smoke_tests.block_on_failure`` is True,
        downgrade ``state.judge_verdict`` to FAIL + veto and append a
        ``smoke_test_failed`` issue. Smoke tests are skipped when
        disabled or no suites are configured.
        """
        cfg = state.config.smoke_tests
        if not cfg.enabled or not cfg.suites:
            return

        ctx.notify("orchestrator", "Running smoke tests (Phase 5.5)")

        from src.agents.smoke_test_agent import SmokeTestAgent

        agent = ctx.agents.get("smoke_test")
        if agent is None:
            agent = SmokeTestAgent(
                state.config.agents.judge,
                repo_path=state.config.repo_path,
            )

        try:
            await agent.run(state)
        except Exception as exc:
            logger.error("Smoke tests raised unexpectedly: %s", exc)
            return

        report = state.smoke_test_report
        if report is None or report.all_passed:
            return

        if not cfg.block_on_failure:
            logger.warning(
                "Smoke tests failed (%d/%d) but block_on_failure=False",
                report.total_failed,
                report.total_cases,
            )
            return

        failed_summary = ", ".join(
            f"{r.suite_name}:{r.case_id}" for r in report.failed_results()[:5]
        )
        new_issue = JudgeIssue(
            file_path="(smoke)",
            issue_level=IssueSeverity.CRITICAL,
            issue_type="smoke_test_failed",
            description=(
                f"Smoke test regressions after Judge PASS: "
                f"{report.total_failed}/{report.total_cases} cases failed "
                f"({failed_summary})"
            ),
            must_fix_before_merge=True,
            veto_condition="Smoke test failed",
        )
        if state.judge_verdict is not None:
            state.judge_verdict = state.judge_verdict.model_copy(
                update={
                    "verdict": VerdictType.FAIL,
                    "veto_triggered": True,
                    "veto_reason": (
                        f"Smoke test failed: {report.total_failed} cases "
                        f"({failed_summary})"
                    ),
                    "issues": list(state.judge_verdict.issues) + [new_issue],
                    "critical_issues_count": (
                        state.judge_verdict.critical_issues_count + 1
                    ),
                }
            )
