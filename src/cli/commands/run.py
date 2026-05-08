import asyncio
import os
import sys
from pathlib import Path
from rich.console import Console
from src.models.config import AgentLLMConfig, MergeConfig
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


def _preflight_check_api_keys(config: MergeConfig) -> None:
    """O-1/O-5: Warn early if required API key environment variables are missing."""
    checked: set[str] = set()

    def _check_agent_cfg(agent_name: str, cfg: AgentLLMConfig) -> None:
        for env_var in cfg.api_key_env_list:
            if env_var in checked:
                continue
            checked.add(env_var)
            if not os.environ.get(env_var):
                console.print(
                    f"[yellow]Warning: {env_var} is not set "
                    f"(required by agent '{agent_name}'). "
                    f"Provider: {cfg.provider}/{cfg.model}[/yellow]"
                )
        if cfg.fallback is not None:
            _check_agent_cfg(f"{agent_name}.fallback", cfg.fallback)

    for field_name in config.agents.model_fields:
        agent_cfg: AgentLLMConfig = getattr(config.agents, field_name)
        _check_agent_cfg(field_name, agent_cfg)


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


def _run_with_auto_decisions(
    orchestrator: Orchestrator,
    state: MergeState,
    yaml_path: str,
    ci: bool,
) -> MergeState:
    """Drive the orchestrator end-to-end without operator intervention.

    Each AWAITING_HUMAN cycle pops one matching round from the bundle and
    feeds it back via the same code path ``resume`` uses, then re-enters
    ``orchestrator.run(state)`` until the run reaches a terminal status or
    no further matching rounds are available.

    Cap on iterations: 8. The 36-commit run never exceeded 4 awaiting_human
    cycles; 8 leaves headroom while still preventing an infinite loop on a
    misconfigured bundle.
    """
    from src.cli.decisions_loader import (
        apply_round,
        detect_current_phase,
        load_bundle,
    )

    try:
        bundle = load_bundle(yaml_path)
    except Exception as exc:
        console.print(f"[red]Failed to read --auto-decisions file: {exc}[/red]")
        sys.exit(EXIT_UNKNOWN_ERROR)

    if not bundle.rounds:
        console.print(
            "[yellow]--auto-decisions file has no rounds; running without "
            "automation[/yellow]"
        )
        return asyncio.run(orchestrator.run(state))

    max_iterations = 8
    final_state = state
    for iteration in range(max_iterations):
        final_state = asyncio.run(orchestrator.run(final_state))
        if final_state.status != SystemStatus.AWAITING_HUMAN:
            return final_state
        phase = detect_current_phase(final_state)
        if phase is None:
            console.print(
                "[yellow]--auto-decisions: cannot determine current decision "
                "phase, exiting loop[/yellow]"
            )
            return final_state
        rnd = bundle.take_round(phase)
        if rnd is None:
            console.print(
                f"[yellow]--auto-decisions: no round for phase={phase.value}; "
                f"remaining rounds in bundle: "
                f"{[r.phase.value for r in bundle.rounds]}[/yellow]"
            )
            return final_state
        try:
            stats = apply_round(final_state, rnd)
        except ValueError as exc:
            console.print(
                f"[red]--auto-decisions failed at iteration "
                f"{iteration + 1}: {exc}[/red]"
            )
            sys.exit(EXIT_UNKNOWN_ERROR)
        if not ci:
            console.print(
                f"[cyan]--auto-decisions iteration {iteration + 1}: "
                f"applied phase={phase.value} "
                f"(items={stats['item_choices']}, "
                f"conflicts={stats['conflict_decisions']}, "
                f"plan_approval={stats['plan_approval_set']}, "
                f"judge_resolution={stats['judge_resolution_set']})[/cyan]"
            )
    console.print(
        f"[yellow]--auto-decisions exhausted {max_iterations} iterations; "
        f"final status={final_state.status.value}[/yellow]"
    )
    return final_state


def run_command_impl(
    config: MergeConfig,
    dry_run: bool,
    ci: bool = False,
    auto_decisions: str | None = None,
) -> None:
    if dry_run and not ci:
        console.print("[yellow]Dry run mode: will analyze but not merge[/yellow]")

    _preflight_check_api_keys(config)

    state = MergeState(config=config, dry_run=dry_run)
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

    if auto_decisions:
        final_state = _run_with_auto_decisions(
            orchestrator, state, auto_decisions, ci=ci
        )
    else:
        final_state = asyncio.run(orchestrator.run(state))

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
