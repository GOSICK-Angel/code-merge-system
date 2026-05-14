import asyncio
import sys
from pathlib import Path

import yaml
from rich.console import Console

from src.cli.paths import get_config_path, get_run_dir, is_dev_mode
from src.core.checkpoint import Checkpoint
from src.core.orchestrator import Orchestrator
from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus

console = Console()

# Runtime-safe fields that may be overlaid by --reload-config without
# breaking the frozen plan. Anything plan-shaping (provider/model,
# auto_merge_confidence, max_files_per_run, project_context, refs) is
# intentionally excluded: changing those mid-run would create a mismatch
# between the plan already on disk and the agents that resume executes.
_RUNTIME_SAFE_TOP_LEVEL: tuple[str, ...] = (
    "commit_round_size",
    "commit_round_max_files",
    "commit_round_max_est_tokens",
)

_RUNTIME_SAFE_PER_AGENT: tuple[str, ...] = (
    "cache_strategy",
    "request_timeout_seconds",
    "max_retries",
    "max_tokens",
    "reasoning_effort",
)


def _reload_runtime_safe_fields(state: MergeState, repo_path: str = ".") -> list[str]:
    """Re-read <repo>/.merge/config.yaml and overlay whitelisted fields onto
    state.config. Returns a list of dotted paths that were actually changed.
    Raises FileNotFoundError if the yaml is missing.
    """
    config_path = get_config_path(repo_path)
    if not config_path.exists():
        raise FileNotFoundError(f"config.yaml not found at {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    # Validate to catch shape errors and coerce types, but read field
    # presence from the raw dict so pydantic defaults from absent fields
    # never silently overwrite checkpoint values.
    fresh = MergeConfig.model_validate(raw)

    changes: list[str] = []
    for field in _RUNTIME_SAFE_TOP_LEVEL:
        if field not in raw:
            continue
        new_val = getattr(fresh, field)
        old_val = getattr(state.config, field, None)
        if new_val != old_val:
            setattr(state.config, field, new_val)
            changes.append(f"{field}: {old_val!r} → {new_val!r}")

    raw_agents = raw.get("agents") or {}
    if not isinstance(raw_agents, dict):
        return changes
    state_agents = state.config.agents
    for agent_name, raw_agent_cfg in raw_agents.items():
        if not isinstance(raw_agent_cfg, dict):
            continue
        state_agent = getattr(state_agents, agent_name, None)
        fresh_agent = getattr(fresh.agents, agent_name, None)
        if state_agent is None or fresh_agent is None:
            continue
        for field in _RUNTIME_SAFE_PER_AGENT:
            if field not in raw_agent_cfg:
                continue
            new_val = getattr(fresh_agent, field)
            old_val = getattr(state_agent, field, None)
            if new_val != old_val:
                setattr(state_agent, field, new_val)
                changes.append(
                    f"agents.{agent_name}.{field}: {old_val!r} → {new_val!r}"
                )

    return changes


def resume_command_impl(
    run_id: str | None,
    checkpoint_path: str | None,
    decisions: str | None = None,
    reload_config: bool = False,
    web: bool = False,
    ws_port: int = 8765,
    web_port: int = 5173,
    open_browser: bool = True,
) -> None:
    if checkpoint_path:
        cp_path = Path(checkpoint_path)
        if not cp_path.exists():
            console.print(f"[red]Checkpoint not found: {checkpoint_path}[/red]")
            sys.exit(1)
        checkpoint = Checkpoint(cp_path.parent)
        state = checkpoint.load(cp_path)
    elif run_id:
        # Production: .merge/runs/<run_id>/checkpoint.json
        # Dev mode: ./outputs/debug/checkpoints/checkpoint.json
        run_dir = get_run_dir(run_id=run_id)
        checkpoint = Checkpoint(run_dir)
        latest = checkpoint.get_latest()
        if latest is None:
            console.print(f"[red]No checkpoint found for run_id: {run_id}[/red]")
            sys.exit(1)
        state = checkpoint.load(latest)
        if state.run_id != run_id and is_dev_mode():
            console.print(
                f"[yellow]Warning: checkpoint run_id {state.run_id} != requested {run_id}[/yellow]"
            )
    else:
        console.print("[red]Either --run-id or --checkpoint is required[/red]")
        sys.exit(1)

    console.print(f"[blue]Resuming run {state.run_id}[/blue]")
    status_val = (
        state.status.value if hasattr(state.status, "value") else str(state.status)
    )
    console.print(f"  Current status: {status_val}")

    if state.status in (SystemStatus.COMPLETED, SystemStatus.FAILED):
        console.print(
            f"[yellow]Run is already in terminal state: {status_val}[/yellow]"
        )
        return

    if reload_config:
        try:
            changes = _reload_runtime_safe_fields(state)
        except FileNotFoundError as exc:
            console.print(f"[red]{exc}[/red]")
            sys.exit(1)
        except Exception as exc:
            console.print(f"[red]Failed to reload config.yaml: {exc}[/red]")
            sys.exit(1)
        if changes:
            console.print(
                f"[green]Reloaded {len(changes)} runtime-safe field(s) from "
                "config.yaml:[/green]"
            )
            for line in changes:
                console.print(f"  {line}")
        else:
            console.print(
                "[yellow]--reload-config: no whitelisted fields differ; "
                "checkpoint config unchanged.[/yellow]"
            )

    if decisions and state.status == SystemStatus.AWAITING_HUMAN:
        from src.cli.decisions_loader import (
            apply_round,
            detect_current_phase,
            load_bundle,
        )

        try:
            bundle = load_bundle(decisions)
        except Exception as _e:
            console.print(f"[red]Failed to read decisions file: {_e}[/red]")
            sys.exit(1)

        # V1 yaml wraps into a single-round bundle; V2 may carry multiple
        # rounds. ``resume`` consumes one matching round per call. The CI
        # ``--auto-decisions`` driver loops AWAITING_HUMAN cycles itself; this
        # single-pass path keeps backwards compatibility with the legacy
        # one-round resume workflow.
        current_phase = detect_current_phase(state)
        round_to_apply = bundle.take_round(current_phase) if current_phase else None
        if round_to_apply is None and bundle.rounds:
            # Fall back to the first round when the V1 path detector cannot
            # pin down a phase but a single round is unambiguously available.
            round_to_apply = bundle.rounds.pop(0)
        if round_to_apply is not None:
            try:
                stats = apply_round(state, round_to_apply)
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                sys.exit(1)
            if stats["item_choices"]:
                console.print(
                    f"[green]Applied {stats['item_choices']} per-file choices[/green]"
                )
            if stats["plan_approval_set"]:
                console.print(
                    f"[green]Plan approval set to "
                    f"{round_to_apply.plan_approval!r} via decisions file[/green]"
                )
            if stats["judge_resolution_set"]:
                console.print(
                    f"[green]Judge resolution set to "
                    f"{round_to_apply.judge_resolution!r} via decisions file[/green]"
                )
            if stats["conflict_decisions"]:
                console.print(
                    f"[green]Loaded {stats['conflict_decisions']} "
                    f"conflict decisions from {decisions}[/green]"
                )

    if state.dry_run:
        console.print(
            "[yellow]Note: checkpoint was saved in dry-run mode; "
            "resuming as a full run (dry_run cleared).[/yellow]"
        )
        state.dry_run = False

    if web:
        from src.cli.commands.web import web_resume_impl

        web_resume_impl(
            state,
            ws_port=ws_port,
            web_port=web_port,
            open_browser=open_browser,
        )
        return

    orchestrator = Orchestrator(state.config)

    async def execute() -> MergeState:
        return await orchestrator.run(state)

    final_state = asyncio.run(execute())

    final_status = (
        final_state.status.value
        if hasattr(final_state.status, "value")
        else str(final_state.status)
    )
    if final_state.status == SystemStatus.COMPLETED:
        console.print("[green]Merge completed successfully![/green]")
    elif final_state.status == SystemStatus.AWAITING_HUMAN:
        console.print("[yellow]Still awaiting human decisions[/yellow]")
        remaining = [
            fp
            for fp, req in final_state.human_decision_requests.items()
            if req.human_decision is None
        ]
        console.print(f"  Pending: {len(remaining)} files")
    elif final_state.status == SystemStatus.FAILED:
        console.print("[red]Run failed[/red]")
        for err in final_state.errors[-3:]:
            console.print(f"  Error: {err.get('message', '')}")
        sys.exit(1)
    else:
        console.print(f"Final status: {final_status}")
