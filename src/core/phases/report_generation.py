from __future__ import annotations

import logging
from datetime import datetime

from src.cli.paths import get_report_dir
from src.core.phases.base import Phase, PhaseContext, PhaseOutcome
from src.models.plan import MergePhase
from src.models.state import MergeState, PhaseResult, SystemStatus
from src.tools.report_writer import (
    write_json_report,
    write_living_plan_report,
    write_markdown_report,
)

logger = logging.getLogger(__name__)


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

        output_dir = str(
            get_report_dir(
                state.config.repo_path, state.run_id, state.config.output.directory
            )
        )

        try:
            cost_summary = ctx.cost_tracker.summary() if ctx.cost_tracker else None
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
