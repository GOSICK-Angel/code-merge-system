from __future__ import annotations

import logging
from datetime import datetime

from src.cli.paths import get_report_dir
from src.core.phases.base import Phase, PhaseContext, PhaseOutcome
from src.models.decision import DecisionSource, MergeDecision
from src.models.plan import MergePhase
from src.models.state import MergeState, PhaseResult, SystemStatus
from src.tools.merge_verification import gather_findings_from_git
from src.tools.report_writer import (
    write_json_report,
    write_living_plan_report,
    write_markdown_report,
)

logger = logging.getLogger(__name__)


def _run_deterministic_verification(state: MergeState, ctx: PhaseContext) -> None:
    """Append LLM-free post-merge findings to ``state.errors``.

    Aggregates duplicate top-level symbols and dropped additive fork exports
    over the run's changed files, reading content from git. Findings land in
    ``state.errors`` so the existing CI summary turns a green ``COMPLETED`` into
    ``partial_failure`` (exit ``EXIT_PARTIAL_FAILURE``) and the merge report's
    errors section surfaces them — no new SystemStatus, so resume / the state
    machine are untouched. Best-effort: any git/read failure logs and returns.
    Skipped in dry-run, where the merge was never committed and HEAD does not
    reflect the merged artifact.
    """
    if state.dry_run:
        return
    try:
        findings = gather_findings_from_git(
            ctx.git_tool,
            list(state.file_decision_records.keys()),
            base_ref=state.merge_base_commit or None,
            fork_ref=state.config.fork_ref or None,
            merged_ref="HEAD",
        )
    except Exception as exc:
        # P1: the deterministic post-merge verification (dup-symbol /
        # dropped-export) could not run at all — record it as a gate-skip so a
        # broken git_tool does not yield a silent clean COMPLETED.
        logger.warning("verification: gathering deterministic findings failed: %s", exc)
        from src.tools.gate_skip import gate_skip_entry

        state.errors.append(
            gate_skip_entry(
                "deterministic_verification",
                "(all)",
                f"post-merge verification could not run: {exc!r}",
            )
        )
        return
    if not findings:
        return
    ctx.notify(
        "orchestrator",
        f"Deterministic verification: {len(findings)} finding(s)",
    )
    now = datetime.now().isoformat()
    for f in findings:
        state.errors.append(
            {
                "timestamp": now,
                "phase": "verification",
                "message": f"[{f.check}] {f.file_path}: {f.detail}",
            }
        )


def _check_compile_gate_advisory(state: MergeState, ctx: PhaseContext) -> None:
    """P3(a): warn when compiled-language files were auto-merged with no compile
    gate configured.

    The always-on per-file syntax gate is balance-only for TS/JS/Go/Rust/Java —
    it cannot catch a brace-balanced merge that does not typecheck. If neither
    ``build_check`` nor a ``gate`` command is configured, such a merge can reach
    ``COMPLETED`` uncompilable. Record a ``no_compile_gate`` advisory in
    ``state.errors`` so the run reports ``partial_failure`` instead of a clean
    green, surfacing the dependency the safety net rests on. Advisory only — it
    does not block (the opt-in soft gate in judge_review does that). Skipped in
    dry-run.
    """
    if state.dry_run:
        return
    from src.tools.compile_gate import auto_merged_compiled_paths_without_gate

    at_risk = auto_merged_compiled_paths_without_gate(state)
    if not at_risk:
        return
    sample = ", ".join(at_risk[:5])
    ctx.notify(
        "orchestrator",
        f"No compile gate configured: {len(at_risk)} compiled-language "
        f"file(s) auto-merged unchecked",
    )
    state.errors.append(
        {
            "timestamp": datetime.now().isoformat(),
            "phase": "verification",
            "message": (
                f"[no_compile_gate] {len(at_risk)} compiled-language file(s) "
                f"auto-merged with no build_check/gate configured (e.g. {sample}); "
                f"the always-on syntax gate is balance-only and cannot catch a "
                f"type error. Configure build_check.command (e.g. 'tsc --noEmit')."
            ),
        }
    )


def _assert_no_dropped_escalations(state: MergeState, ctx: PhaseContext) -> None:
    """方案6: surface every unresolved escalation reaching report as DROPPED.

    A ``FileDecisionRecord`` still at ``ESCALATE_HUMAN`` by report time was
    never resolved — a human resolution rewrites it to a concrete decision with
    ``DecisionSource.HUMAN``. So ``decision == ESCALATE_HUMAN and
    decision_source != HUMAN`` is a precise "unresolved" signal, flagged
    regardless of whether the file reached the human gate: a run that completes
    with an undecided escalation has dropped that file from the merge, whether
    it bypassed the gate (internal ``escalate(0.0)`` from commit-replay /
    skipped auto-merge layers) or was surfaced and left undecided. Each lands in
    ``state.errors`` so CI reports ``partial_failure`` and the report lists it,
    instead of a green ``COMPLETED`` hiding a dropped file.

    Pairs with ``human_review._surface_internal_escalations`` (方案6 part1),
    which registers these in the gate so the operator can decide them in-run;
    this assertion is the backstop for any still undecided at completion.
    """
    dropped = sorted(
        fp
        for fp, rec in state.file_decision_records.items()
        if rec.decision == MergeDecision.ESCALATE_HUMAN
        and rec.decision_source != DecisionSource.HUMAN
    )
    if not dropped:
        return
    ctx.notify(
        "orchestrator",
        f"Finalize: {len(dropped)} dropped (unresolved) escalation(s)",
    )
    now = datetime.now().isoformat()
    for fp in dropped:
        state.errors.append(
            {
                "timestamp": now,
                "phase": "finalize",
                "message": (
                    f"DROPPED (unresolved escalation): {fp} left at "
                    f"ESCALATE_HUMAN — not resolved by a human and not landed"
                ),
            }
        )


def _finalize_working_tree(state: MergeState, ctx: PhaseContext) -> None:
    """P2-2: stage and commit any working-tree leftovers before the report.

    The merge flow can leave behind untracked or modified files (e.g.
    ``take_target`` writes that bypass git's index, or escalate_human
    fallbacks that drop fresh content into the worktree). Without this
    step the run reports ``COMPLETED`` while the tree still holds
    uncommitted changes — surprising the operator and breaking diff
    reproducibility. Commit failures are downgraded to warnings so the
    report itself still gets written.

    .merge/ is always excluded: it contains secrets (.env), runtime
    checkpoints, and reports that must never be committed to merge working
    branches regardless of .gitignore state.
    """
    if state.dry_run:
        return
    try:
        entries = ctx.git_tool.get_status()
    except Exception as exc:
        logger.warning("finalize: git status failed: %s", exc)
        return
    if not entries:
        return

    # Count only source-tree changes; .merge/ entries are noise here.
    source_entries = [
        (code, path) for code, path in entries if not path.startswith(".merge/")
    ]
    if not source_entries:
        return

    untracked = sum(1 for code, _ in source_entries if code == "??")
    modified = len(source_entries) - untracked
    try:
        ctx.git_tool.repo.git.add("-A")
        # Immediately purge .merge/ from the index — belt-and-suspenders
        # against gitignore gaps or previously tracked files from old runs.
        try:
            ctx.git_tool.repo.git.rm(
                "--cached", "-r", "--ignore-unmatch", "--", ".merge/"
            )
        except Exception as unstage_exc:
            logger.warning("finalize: could not unstage .merge/: %s", unstage_exc)
        sha = ctx.git_tool.repo.git.commit(
            "-m",
            (
                f"chore(merge): finalize working tree "
                f"(+{untracked} untracked, ~{modified} modified, "
                f"run={state.run_id[:8]})"
            ),
        )
        logger.info(
            "finalize: committed %d untracked + %d modified file(s) (%s)",
            untracked,
            modified,
            str(sha)[:80],
        )
    except Exception as exc:
        logger.warning(
            "finalize: auto-commit failed (%d untracked + %d modified left in tree): %s",
            untracked,
            modified,
            exc,
        )


class ReportGenerationPhase(Phase):
    name = "report_generation"

    async def execute(self, state: MergeState, ctx: PhaseContext) -> PhaseOutcome:
        state.current_phase = MergePhase.REPORT
        phase_result = PhaseResult(
            phase=MergePhase.REPORT,
            status="running",
            started_at=datetime.now(),
        )
        state.phase_results[MergePhase.REPORT.value] = phase_result

        _finalize_working_tree(state, ctx)
        _run_deterministic_verification(state, ctx)
        _check_compile_gate_advisory(state, ctx)
        _assert_no_dropped_escalations(state, ctx)

        output_dir = str(
            get_report_dir(
                state.config.repo_path, state.run_id, state.config.output.directory
            )
        )

        try:
            # Prefer the cumulative state.cost_summary (merged across resumes
            # by _snapshot_telemetry) over the live CostTracker, which on a
            # resumed run only holds the current process's calls. Using the
            # live tracker made the markdown report under-count cost on
            # resumed runs (e.g. forgejo run 0dec928c showed $0.0240/13 calls
            # in markdown vs the correct $0.0397/21 in JSON + the Web UI).
            cost_summary = state.cost_summary or (
                ctx.cost_tracker.summary() if ctx.cost_tracker else None
            )
            utilization_summary = (
                ctx.trace_logger.get_utilization_summary() if ctx.trace_logger else None
            )
            memory_summary = (
                ctx.memory_hit_tracker.summary() if ctx.memory_hit_tracker else None
            )

            if "json" in state.config.output.formats:
                write_json_report(state, output_dir)
            if "markdown" in state.config.output.formats:
                write_markdown_report(
                    state,
                    output_dir,
                    cost_summary=cost_summary,
                    utilization_summary=utilization_summary,
                    memory_summary=memory_summary,
                )

            write_living_plan_report(state, output_dir)

            phase_result = phase_result.model_copy(
                update={"status": "completed", "completed_at": datetime.now()}
            )
            state.phase_results[MergePhase.REPORT.value] = phase_result
            ctx.state_machine.transition(
                state, SystemStatus.COMPLETED, "reports generated"
            )
            return PhaseOutcome(
                target_status=SystemStatus.COMPLETED,
                reason="reports generated",
                checkpoint_tag="completed",
            )
        except Exception as e:
            state.errors.append(
                {
                    "timestamp": datetime.now().isoformat(),
                    "phase": "report",
                    "message": f"Report generation failed (non-blocking): {e}",
                }
            )
            phase_result = phase_result.model_copy(
                update={"status": "completed", "error": str(e)}
            )
            state.phase_results[MergePhase.REPORT.value] = phase_result
            ctx.state_machine.transition(
                state,
                SystemStatus.COMPLETED,
                "reports failed but marking complete",
            )
            return PhaseOutcome(
                target_status=SystemStatus.COMPLETED,
                reason="reports failed but marking complete",
                checkpoint_tag="completed",
            )
