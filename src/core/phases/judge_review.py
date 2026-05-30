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
from src.models.decision import DecisionSource
from src.models.judge import (
    IssueSeverity,
    IssueResolvability,
    JudgeIssue,
    JudgeVerdict,
    VerdictType,
)
from src.models.plan import MergePhase
from src.models.state import MergeState, PhaseResult, SystemStatus

logger = logging.getLogger(__name__)


def _is_human_decided(state: MergeState, file_path: str) -> bool:
    record = state.file_decision_records.get(file_path)
    return record is not None and record.decision_source == DecisionSource.HUMAN


def _persist_gate_skips(
    state: MergeState, gates_skipped: list[dict[str, str]] | None
) -> None:
    """P1: persist the Judge's "deterministic gate could not run" records into
    ``state.errors`` (the read-only Judge cannot write state itself).

    Deduped by message so a persistent skip recurring across dispute rounds does
    not stack N identical entries. Any entry already present in ``state.errors``
    (same ``message``) is left untouched.
    """
    if not gates_skipped:
        return
    seen_msgs = {e.get("message") for e in state.errors}
    for entry in gates_skipped:
        if entry.get("message") not in seen_msgs:
            state.errors.append(entry)
            seen_msgs.add(entry.get("message"))


def _summarize_exc(exc: BaseException, max_chars: int = 120) -> str:
    msg = str(exc).strip() or type(exc).__name__
    return msg[:max_chars]


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

            # P1: the read-only Judge cannot write state.errors; it returns any
            # "deterministic gate could not run" records in the payload. Persist
            # them here (Orchestrator side) so a silently-disabled veto pipeline
            # surfaces as partial_failure instead of a clean PASS.
            _persist_gate_skips(state, msg.payload.get("gates_skipped"))

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

            if round_num >= max_rounds - 1:
                logger.info("Last dispute round — skipping rebuttal and repair")
                continue

            rebuttal = await executor.build_rebuttal(state.judge_verdict.issues, state)

            if rebuttal.accepts_all:
                repairable = [
                    r for r in rebuttal.repair_instructions if r.is_repairable
                ]
                if repairable and round_num < max_rounds - 1:
                    non_human_repairable = [
                        r
                        for r in repairable
                        if not _is_human_decided(state, r.file_path)
                    ]
                    if not non_human_repairable:
                        logger.info(
                            "All %d repair target(s) are operator-decided "
                            "(executor.repair would no-op them); "
                            "short-circuiting further Judge rounds",
                            len(repairable),
                        )
                        break
                    logger.info(
                        "Executor accepts all issues; repairing %d/%d items "
                        "(round %d/%d; %d human-locked skipped)",
                        len(non_human_repairable),
                        len(repairable),
                        round_num + 1,
                        max_rounds,
                        len(repairable) - len(non_human_repairable),
                    )
                    await executor.repair(non_human_repairable, state)
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

        # O-M4: credit/blame memory entries based on the final verdict's
        # passed/failed file lists. Outcomes accumulate across runs via the
        # tracker's sidecar JSON; future runs use them to bias confidence.
        if state.judge_verdict is not None and ctx.memory_hit_tracker is not None:
            for fp in state.judge_verdict.passed_files:
                ctx.memory_hit_tracker.record_outcome(fp, success=True)
            for fp in state.judge_verdict.failed_files:
                ctx.memory_hit_tracker.record_outcome(fp, success=False)

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
            await self._run_build_check(state, ctx)
            # Build check (compile gate) may downgrade verdict to FAIL; only
            # run functional smoke tests if the tree still builds.
            if state.judge_verdict.verdict == VerdictType.PASS:
                await self._run_smoke_tests(state, ctx)
            # Either gate may have downgraded the verdict to FAIL
            if state.judge_verdict.verdict != VerdictType.PASS:
                reason = (
                    f"post-judge gate failed: {state.judge_verdict.veto_reason}"
                    if state.judge_verdict.veto_reason
                    else "post-judge gate failed"
                )
                ctx.state_machine.transition(state, SystemStatus.AWAITING_HUMAN, reason)
                return PhaseOutcome(
                    target_status=SystemStatus.AWAITING_HUMAN,
                    reason=reason,
                    checkpoint_tag="after_phase5_smoke",
                    memory_phase="judge_review",
                )
            # P3(b): opt-in strict posture — refuse a silent green COMPLETED when
            # compiled-language files were auto-merged with NO compile gate
            # configured (the always-on syntax gate is balance-only; a type error
            # would otherwise reach COMPLETED). JUDGE_REVIEWING → AWAITING_HUMAN is
            # a legal edge; GENERATING_REPORT → AWAITING_HUMAN is not, so this gate
            # must live here, not in report_generation. Default-off flag preserves
            # current behavior; the report-time advisory (P3a) covers the default.
            if state.config.build_check.require_for_compiled_langs:
                from src.tools.compile_gate import (
                    auto_merged_compiled_paths_without_gate,
                )

                at_risk = auto_merged_compiled_paths_without_gate(state)
                if at_risk:
                    reason = (
                        f"no compile gate configured but {len(at_risk)} "
                        f"compiled-language file(s) auto-merged "
                        f"(require_for_compiled_langs=True)"
                    )
                    ctx.state_machine.transition(
                        state, SystemStatus.AWAITING_HUMAN, reason
                    )
                    return PhaseOutcome(
                        target_status=SystemStatus.AWAITING_HUMAN,
                        reason=reason,
                        checkpoint_tag="after_phase5",
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

        # No consensus after all dispute rounds → consult Coordinator
        rounds_done = state.judge_repair_rounds + 1
        self._warn_fixable_issues(state)

        if ctx.coordinator is not None:
            decision = ctx.coordinator.route_judge_stall(state)
            if decision.action == "meta_review":
                ok = await self._run_judge_meta_review(state, ctx, decision.reason)
                if ok:
                    reason = (
                        f"judge stall escalated to meta-review after "
                        f"{rounds_done} rounds"
                    )
                else:
                    reason = (
                        f"judge stall after {rounds_done} rounds "
                        "(meta-review unavailable; see coordinator_directives)"
                    )
                ctx.state_machine.transition(state, SystemStatus.AWAITING_HUMAN, reason)
                return PhaseOutcome(
                    target_status=SystemStatus.AWAITING_HUMAN,
                    reason=reason,
                    checkpoint_tag="after_phase5",
                    memory_phase="judge_review",
                )

        fixable_count = self._count_fixable_issues(state.judge_verdict)
        reason = f"judge verdict: FAIL after {rounds_done} dispute rounds" + (
            f" ({fixable_count} fixable issues)" if fixable_count else ""
        )
        ctx.state_machine.transition(state, SystemStatus.AWAITING_HUMAN, reason)
        return PhaseOutcome(
            target_status=SystemStatus.AWAITING_HUMAN,
            reason=reason,
            checkpoint_tag="after_phase5",
            memory_phase="judge_review",
        )

    async def _run_judge_meta_review(
        self,
        state: MergeState,
        ctx: PhaseContext,
        trigger_reason: str,
    ) -> bool:
        from src.core.coordinator import Coordinator
        from src.models.coordinator import MetaReviewResult

        judge = ctx.agents.get("judge")
        if judge is None:
            return False
        logger.info("Coordinator: running judge meta-review (%s)", trigger_reason)
        try:
            raw = await judge.meta_review(state)
        except Exception as exc:
            summary = _summarize_exc(exc)
            logger.warning("Judge meta-review failed: %s", summary)
            state.coordinator_directives.append(
                MetaReviewResult(
                    phase="judge_review",
                    trigger="judge_stall",
                    assessment=f"meta-review failed: {summary}"[:200],
                    recommendation=(
                        "see judge_verdict.issues; operator decides accept/rerun/abort"
                    ),
                )
            )
            return False
        if ctx.coordinator is not None:
            result = Coordinator.build_meta_review_result(
                phase="judge_review",
                trigger="judge_stall",
                raw=raw,
            )
            state.coordinator_directives.append(result)
            logger.info(
                "Judge meta-review: assessment=%r recommendation=%r",
                result.assessment,
                result.recommendation,
            )
        return True

    async def _run_build_check(self, state: MergeState, ctx: PhaseContext) -> None:
        """Optional compile/build gate run after Judge PASS.

        Runs the config-supplied ``build_check.command`` in the repo. A
        non-zero exit (or timeout) downgrades ``state.judge_verdict`` to FAIL
        with a veto and appends a ``build_check_failed`` issue. This catches
        cross-file compilation breaks the per-file Judge review cannot see.
        Skipped when disabled or no command is configured.
        """
        import asyncio
        from pathlib import Path

        cfg = state.config.build_check
        if not cfg.enabled or not cfg.command.strip():
            return

        ctx.notify("orchestrator", "Running build check (Phase 5.5)")

        cwd = Path(state.config.repo_path)
        if cfg.working_dir and cfg.working_dir != ".":
            cwd = cwd / cfg.working_dir

        try:
            proc = await asyncio.create_subprocess_shell(
                cfg.command,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=cfg.timeout_seconds
                )
                returncode = proc.returncode if proc.returncode is not None else 0
                output = stdout_bytes.decode("utf-8", errors="replace")
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                returncode = -1
                output = f"build check timed out after {cfg.timeout_seconds}s"
        except Exception as exc:
            # #7A: fail CLOSED. A build gate the operator deliberately enabled
            # that cannot even launch (bad command, missing toolchain, OS error)
            # must NOT silently leave the verdict at PASS — that is the exact
            # "the one compile gate fails open" hole. Treat a launch crash as a
            # build failure and fall through to the downgrade below.
            logger.error("Build check raised unexpectedly: %s", exc)
            returncode = -2
            output = f"build check failed to launch: {exc!r}"

        if returncode == 0:
            return

        tail = "\n".join(output.strip().splitlines()[-20:])
        new_issue = JudgeIssue(
            file_path="(build)",
            issue_level=IssueSeverity.CRITICAL,
            issue_type="build_check_failed",
            description=(
                f"Build check `{cfg.command}` exited {returncode}. Output tail:\n{tail}"
            ),
            must_fix_before_merge=True,
            veto_condition="Build check failed",
        )
        if state.judge_verdict is not None:
            state.judge_verdict = state.judge_verdict.model_copy(
                update={
                    "verdict": VerdictType.FAIL,
                    "veto_triggered": True,
                    "veto_reason": (
                        f"Build check failed (exit {returncode}): {cfg.command}"
                    ),
                    "issues": list(state.judge_verdict.issues) + [new_issue],
                    "critical_issues_count": (
                        state.judge_verdict.critical_issues_count + 1
                    ),
                }
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

        # #7A: fail CLOSED on a launch crash. Constructing the agent and running
        # it are both inside the try so a misconfigured suite / missing harness
        # downgrades to FAIL (when block_on_failure) instead of silently leaving
        # the verdict at PASS.
        try:
            from src.agents.smoke_test_agent import SmokeTestAgent

            agent = ctx.agents.get("smoke_test")
            if agent is None:
                agent = SmokeTestAgent(
                    state.config.agents.judge,
                    repo_path=state.config.repo_path,
                )
            await agent.run(state)
        except Exception as exc:
            logger.error("Smoke tests raised unexpectedly: %s", exc)
            if cfg.block_on_failure and state.judge_verdict is not None:
                crash_issue = JudgeIssue(
                    file_path="(smoke)",
                    issue_level=IssueSeverity.CRITICAL,
                    issue_type="smoke_test_failed",
                    description=f"Smoke test harness failed to run: {exc!r}",
                    must_fix_before_merge=True,
                    veto_condition="Smoke test launch failed",
                )
                state.judge_verdict = state.judge_verdict.model_copy(
                    update={
                        "verdict": VerdictType.FAIL,
                        "veto_triggered": True,
                        "veto_reason": f"Smoke test harness failed: {exc!r}",
                        "issues": list(state.judge_verdict.issues) + [crash_issue],
                        "critical_issues_count": (
                            state.judge_verdict.critical_issues_count + 1
                        ),
                    }
                )
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

    @staticmethod
    def _count_fixable_issues(verdict: JudgeVerdict | None) -> int:
        if verdict is None:
            return 0
        return sum(
            1 for i in verdict.issues if i.resolvability == IssueResolvability.FIXABLE
        )

    def _warn_fixable_issues(self, state: MergeState) -> None:
        fixable = [
            i
            for i in (state.judge_verdict.issues if state.judge_verdict else [])
            if i.resolvability == IssueResolvability.FIXABLE
        ]
        if not fixable:
            return
        lines = [
            f"  [{i.issue_level.value.upper()}] {i.file_path}: {i.description}"
            for i in fixable[:10]
        ]
        if len(fixable) > 10:
            lines.append(f"  ... and {len(fixable) - 10} more")
        logger.warning(
            "%d fixable issue(s) detected — resolve before accepting FAIL verdict:\n%s",
            len(fixable),
            "\n".join(lines),
        )
        state.messages.append(
            {
                "type": "fixable_issues_warning",
                "count": len(fixable),
                "issues": [
                    {
                        "file_path": i.file_path,
                        "issue_level": i.issue_level.value,
                        "description": i.description,
                        "suggested_fix": i.suggested_fix,
                    }
                    for i in fixable
                ],
            }
        )
