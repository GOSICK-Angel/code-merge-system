import asyncio
import sys
import yaml
import click
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from src.models.config import MergeConfig
from src.models.state import MergeState, SystemStatus
from src.core.orchestrator import Orchestrator


console = Console()


def run_command_impl(config_path: str, dry_run: bool) -> None:
    config_file = Path(config_path)
    if not config_file.exists():
        console.print(f"[red]Config file not found: {config_path}[/red]")
        sys.exit(1)

    try:
        raw_config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        config = MergeConfig.model_validate(raw_config)
    except Exception as e:
        console.print(f"[red]Invalid config: {e}[/red]")
        sys.exit(1)

    if dry_run:
        console.print("[yellow]Dry run mode: will analyze but not merge[/yellow]")

    state = MergeState(config=config)
    console.print(f"[blue]Starting merge run {state.run_id}[/blue]")
    console.print(f"  Upstream: {config.upstream_ref}")
    console.print(f"  Fork: {config.fork_ref}")

    orchestrator = Orchestrator(config)

    async def execute():
        return await orchestrator.run(state)

    final_state = asyncio.run(execute())

    status_val = final_state.status.value if hasattr(final_state.status, "value") else str(final_state.status)
    if final_state.status == SystemStatus.COMPLETED:
        console.print(f"[green]Merge completed successfully![/green]")
    elif final_state.status == SystemStatus.AWAITING_HUMAN:
        console.print(f"[yellow]Paused: awaiting human decisions[/yellow]")
        console.print(f"  Run ID: {final_state.run_id}")
        console.print(f"  Resume with: merge resume --run-id {final_state.run_id}")
    elif final_state.status == SystemStatus.FAILED:
        console.print(f"[red]Merge failed[/red]")
        for err in final_state.errors:
            console.print(f"  Error: {err.get('message', '')}")
        sys.exit(1)
    else:
        console.print(f"Final status: {status_val}")
