import asyncio
import sys
from pathlib import Path
from rich.console import Console
from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus
from src.core.orchestrator import Orchestrator
from src.core.phases.base import ActivityEvent
from src.cli.exit_codes import (
    EXIT_SUCCESS,
    EXIT_NEEDS_HUMAN,
    EXIT_JUDGE_REJECTED,
    EXIT_PARTIAL_FAILURE,
    EXIT_UNKNOWN_ERROR,
)
from src.tools.ci_reporter import build_ci_summary, format_ci_summary


console = Console()


def _handle_ci_exit(final_state: MergeState) -> None:
    summary = build_ci_summary(final_state)
    print(format_ci_summary(summary))

    if final_state.status == SystemStatus.COMPLETED:
        if final_state.judge_verdict:
            from src.models.judge import VerdictType

            if final_state.judge_verdict.verdict == VerdictType.FAIL:
                sys.exit(EXIT_JUDGE_REJECTED)
        if final_state.errors:
            sys.exit(EXIT_PARTIAL_FAILURE)
        sys.exit(EXIT_SUCCESS)
    elif final_state.status == SystemStatus.AWAITING_HUMAN:
        sys.exit(EXIT_NEEDS_HUMAN)
    elif final_state.status == SystemStatus.FAILED:
        sys.exit(EXIT_UNKNOWN_ERROR)
    else:
        sys.exit(EXIT_UNKNOWN_ERROR)


def run_command_impl(
    config: MergeConfig,
    dry_run: bool,
    ci: bool = False,
) -> None:
    if dry_run and not ci:
        console.print("[yellow]Dry run mode: will analyze but not merge[/yellow]")

    state = MergeState(config=config)
    if not ci:
        console.print(f"[blue]Starting merge run {state.run_id}[/blue]")
        console.print(f"  Upstream: {config.upstream_ref}")
        console.print(f"  Fork: {config.fork_ref}")

    orchestrator = Orchestrator(config)

    if not ci:

        def _print_activity(event: ActivityEvent) -> None:
            color = {"planner": "cyan", "planner_judge": "magenta"}.get(
                event.agent, "dim"
            )
            console.print(f"  [{color}][{event.agent}][/{color}] {event.action}")

        orchestrator.set_activity_callback(_print_activity)

    async def execute() -> MergeState:
        return await orchestrator.run(state)

    final_state = asyncio.run(execute())

    output_dir = config.output.directory
    debug_dir = config.output.debug_directory

    if ci:
        _handle_ci_exit(final_state)
        return

    status_val = (
        final_state.status.value
        if hasattr(final_state.status, "value")
        else str(final_state.status)
    )
    if final_state.status == SystemStatus.COMPLETED:
        console.print("[green]Merge completed successfully![/green]")
    elif final_state.status == SystemStatus.AWAITING_HUMAN:
        console.print("[yellow]Paused: awaiting human decisions[/yellow]")
        console.print(f"  Run ID: {final_state.run_id}")

        if final_state.plan_review_log:
            console.print("")
            console.print("[bold]Plan Review Negotiation Summary:[/bold]")
            for rnd in final_state.plan_review_log:
                result_val = (
                    rnd.verdict_result.value
                    if hasattr(rnd.verdict_result, "value")
                    else str(rnd.verdict_result)
                )
                console.print(
                    f"  Round {rnd.round_number}: "
                    f"[magenta]{result_val}[/magenta] "
                    f"({rnd.issues_count} issues)"
                )
                if rnd.planner_responses:
                    accepted = sum(
                        1 for r in rnd.planner_responses if r.action.value == "accept"
                    )
                    rejected = sum(
                        1 for r in rnd.planner_responses if r.action.value == "reject"
                    )
                    discussed = sum(
                        1 for r in rnd.planner_responses if r.action.value == "discuss"
                    )
                    console.print(
                        f"    Planner: [green]{accepted} accept[/green], "
                        f"[red]{rejected} reject[/red], "
                        f"[yellow]{discussed} discuss[/yellow]"
                    )
                    for r in rnd.planner_responses:
                        if r.action.value == "reject":
                            console.print(
                                f"    [red]REJECT[/red] `{r.file_path}`: {r.reason}"
                            )
                        elif r.action.value == "discuss":
                            console.print(
                                f"    [yellow]DISCUSS[/yellow] `{r.file_path}`: "
                                f"{r.reason}"
                            )
                if rnd.plan_diff:
                    console.print("    Plan diff:")
                    for d in rnd.plan_diff:
                        console.print(
                            f"      `{d.file_path}`: {d.old_risk} → {d.new_risk}"
                        )

        if final_state.pending_user_decisions:
            console.print("")
            console.print(
                f"[bold yellow]{len(final_state.pending_user_decisions)} "
                f"files require your decision:[/bold yellow]"
            )
            for item in final_state.pending_user_decisions:
                console.print(
                    f"  - `{item.file_path}` "
                    f"[{item.current_classification}]: {item.description}"
                )

        plan_review_file = Path(output_dir) / f"plan_review_{final_state.run_id}.md"
        if plan_review_file.exists():
            console.print("")
            console.print(f"  [green]Plan review report: {plan_review_file}[/green]")
            console.print("  Please review the plan before approving.")

        console.print(f"  Resume with: merge resume --run-id {final_state.run_id}")

        log_file = Path(debug_dir) / f"run_{final_state.run_id}.log"
        traces_file = Path(debug_dir) / f"llm_traces_{final_state.run_id}.jsonl"
        console.print("")
        console.print("[dim]Developer debug outputs:[/dim]")
        if log_file.exists():
            console.print(f"  [dim]Run log: {log_file}[/dim]")
        if traces_file.exists():
            console.print(f"  [dim]LLM traces: {traces_file}[/dim]")

    elif final_state.status == SystemStatus.FAILED:
        console.print("[red]Merge failed[/red]")
        for err in final_state.errors:
            console.print(f"  Error: {err.get('message', '')}")
        sys.exit(EXIT_UNKNOWN_ERROR)
    else:
        console.print(f"Final status: {status_val}")
